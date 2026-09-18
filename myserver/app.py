from flask import Flask, jsonify,request
from db import get_conn, init_db
import uuid
from datetime import datetime, timezone
import json
import threading


app = Flask(__name__)
init_db()
transactions = {}  # in-memory store: transactionId -> transaction dict
lock = threading.Lock()# for scan item, solve race condition

LOW_STOCK_THRESHOLD = 50  # server-side default
WINDOW_SIZE = 1000
SLIDE_INTERVAL = 500



# GET /items
@app.get("/items")
def list_item():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT sku, name, price FROM items")
    rows = c.fetchall()
    """
    rows = [
    ("SKU-000001", "Item 1", 4.32),
    ("SKU-000002", "Item 2", 17.90),
    ...
    ]
    """
    conn.close()
    return jsonify({"items": [{"sku": r[0], "name": r[1], "price": r[2]} for r in rows]})
    ## jsonify: converting that Python dict into an actual HTTP JSON response


@app.post("/transactions")
def start_transactions():
    body = request.get_json()
    station_id = body["stationId"]

    tx_id = str(uuid.uuid4())
    tx = {
        "transactionId": tx_id,
        "stationId": station_id,
        "status": "OPEN",
        "itemCount": 0,
        "runningTotal": 0.0,
        "startedAt": datetime.now(timezone.utc).isoformat(),
    }
    transactions[tx_id] = tx

    return jsonify(tx), 201

# POST /transactions/{transactionId}/items (scanning one item),only one for fetchone()
@app.post("/transactions/<transaction_id>/items")# For Flask using <>
def scan_item(transaction_id):
    tx = transactions.get(transaction_id)
    if tx is None:
        return jsonify({"error": "TRANSACTION_NOT_FOUND",
                         "message": f"Transaction {transaction_id} not found."}), 404
    if tx["status"] != "OPEN":
        return jsonify({"error": "TRANSACTION_NOT_OPEN",
                         "message": f"Transaction {transaction_id} is not open."}), 409

    body = request.get_json()
    sku = body['sku']

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name, price FROM items WHERE sku = ?", (sku,))#(sku,) for inputing a tuple
    row = c.fetchone()

    if row is None:
        conn.close()

        return jsonify({"error": "SKU_NOT_FOUND",
                         "message": f"SKU {sku} not found."}), 404
    name,price = row
    tx.setdefault("basket",{})
    tx["basket"][sku] = tx["basket"].get(sku, 0) + 1
    tx["itemCount"] += 1
    tx["runningTotal"] = round(tx["runningTotal"] + price, 2)
    with lock:#Since all the threads will try to update the same database.
        c.execute("INSERT INTO scan_log (sku, ts) VALUES (?, ?)",
                  (sku, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM scan_log")
        total_scans = c.fetchone()[0]
        conn.close()

        if total_scans % SLIDE_INTERVAL == 0:
            recompute_popular_items(total_scans)

    return jsonify({
        "transactionId": transaction_id,
        "sku": sku,
        "name": name,
        "unitPrice": price,
        "itemCount": tx["itemCount"],
        "runningTotal": tx["runningTotal"],
    })

# POST /transactions/{transactionid}/complete, Pay, decrement stock, return a receipt
@app.post("/transactions/<transaction_id>/complete")
def complete_transaction(transaction_id):
    tx = transactions.get(transaction_id)
    if tx is None:
        return jsonify({"error": "TRANSACTION_NOT_FOUND",
                            "message": f"Transaction {transaction_id} not found."}), 404
    if tx["status"] != "OPEN":
        return jsonify({"error": "TRANSACTION_NOT_OPEN",
                         "message": "Already completed or cancelled."}), 409
    if tx.get("itemCount", 0) == 0:
        return jsonify({"error": "EMPTY_BASKET",
                         "message": "Cannot complete an empty transaction."}), 409

    conn = get_conn()
    c = conn.cursor()
    lines = []
    for sku, qty in tx["basket"].items():
        c.execute("SELECT name, price FROM items WHERE sku = ?", (sku,))
        name, price = c.fetchone()
        c.execute("UPDATE items SET stock = stock - ? WHERE sku = ? AND stock >= ?",
                  (qty, sku, qty))
        lines.append({"sku": sku, "name": name, "unitPrice": price, "quantity": qty})
    conn.commit()
    conn.close()

    tx["status"] = "COMPLETED"
    return jsonify({
        "transactionId": tx["transactionId"],
        "stationId": tx["stationId"],
        "itemCount": tx["itemCount"],
        "totalAmount": tx["runningTotal"],
        "startedAt": tx["startedAt"],
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "lines": lines,
    })

# GET /transactions/{id}
@app.get("/transactions/<transaction_id>")
def get_transaction(transaction_id):
    tx = transactions.get(transaction_id)
    if tx is None:
        return jsonify({"error": "TRANSACTION_NOT_FOUND",
                         "message": f"Transaction {transaction_id} not found."}), 404
    return jsonify({
        "transactionId": tx["transactionId"],
        "stationId": tx["stationId"],
        "status": tx["status"],
        "itemCount": tx["itemCount"],
        "runningTotal": tx["runningTotal"],
        "startedAt": tx["startedAt"],
    })


# GET /inventory/low-stock
@app.get("/inventory/low-stock")
def low_stock():
    threshold = request.args.get("threshold", default=LOW_STOCK_THRESHOLD, type=int)

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT sku,name,stock FROM items where stock < ?",(threshold,))
    rows = c.fetchall()
    conn.close()

    now = datetime.now(timezone.utc).isoformat()
    return jsonify({
        "threshold": threshold,
        "generatedAt": now,
        "alerts": [
            {"sku": r[0], "name": r[1], "currentStock": r[2],
             "threshold": threshold, "triggeredAt": now}
            for r in rows
        ],
    })

#
def recompute_popular_items(total_scans):
    conn = get_conn()
    c = conn.cursor()

    window_start = max(1, total_scans - WINDOW_SIZE + 1)
    c.execute("""SELECT sku, COUNT(*) as cnt FROM scan_log
                 WHERE seq >= ? AND seq <= ?
                 GROUP BY sku ORDER BY cnt DESC LIMIT 10""",
              (window_start, total_scans))
    rows = c.fetchall()

    skus = [r[0] for r in rows]
    names = {}
    if skus:
        qmarks = ",".join("?" * len(skus))
        c.execute(f"SELECT sku, name FROM items WHERE sku IN ({qmarks})", skus)# how many paras do I need (?,?,?)

        """
        c.fetchall()   # [('APPLE', 'Red Apple'), ('BREAD', 'Wheat Bread'), ('MILK', 'Whole Milk')]
        dict(...) # {'APPLE': 'Red Apple', 'BREAD': 'Wheat Bread', 'MILK': 'Whole Milk'}
        """
        names = dict(c.fetchall())

    items = [
        {"sku": s, "name": names.get(s, ""), "scanCount": cnt, "rank": i + 1}
        for i, (s, cnt) in enumerate(rows)
    ]

    computed_at = datetime.now(timezone.utc).isoformat()
    c.execute("""INSERT INTO popular_snapshot (id, window_start, window_end, computed_at, items_json)
                 VALUES (1, ?, ?, ?, ?)
                 ON CONFLICT(id) DO UPDATE SET 
                   window_start = excluded.window_start,
                   window_end = excluded.window_end,
                   computed_at = excluded.computed_at,
                   items_json = excluded.items_json""",
              (window_start, total_scans, computed_at, json.dumps(items)))
    conn.commit()
    conn.close()
    
# # GET /analytics/popular-items
@app.get("/analytics/popular-items")
def popular_items():
    limit = request.args.get("limit", default=10, type=int)

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT window_start, window_end, computed_at, items_json FROM popular_snapshot WHERE id = 1")
    row = c.fetchone()
    conn.close()

    if row is None:
        return jsonify({
            "windowSize": WINDOW_SIZE,
            "slideInterval": SLIDE_INTERVAL,
            "windowStart": 0,
            "windowEnd": 0,
            "computedAt": datetime.now(timezone.utc).isoformat(),
            "items": [],
        })

    ws, we, computed_at, items_json = row
    return jsonify({
        "windowSize": WINDOW_SIZE,
        "slideInterval": SLIDE_INTERVAL,
        "windowStart": ws,
        "windowEnd": we,
        "computedAt": computed_at,
        "items": json.loads(items_json)[:limit],
    })



if __name__ == "__main__":
    app.run(port=8080, threaded=True)# handle multiple requests