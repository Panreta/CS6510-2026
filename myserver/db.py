import sqlite3, random

DB_PATH = "store.db"
CATALOG_SIZE = 2000
STOCK_PER_ITEM = 10000

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)# different thread can be used

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS items")
    c.execute("DROP TABLE IF EXISTS scan_log")
    c.execute("""CREATE TABLE items (
        sku TEXT PRIMARY KEY,
        name TEXT,
        price REAL,
        stock INTEGER
    )""")
    c.execute("""CREATE TABLE scan_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT,
        ts TEXT
    )""")
    c.execute("DROP TABLE IF EXISTS popular_snapshot")
    c.execute("""CREATE TABLE popular_snapshot (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        window_start INTEGER,
        window_end INTEGER,
        computed_at TEXT,
        items_json TEXT
    )""")
    random.seed(42)  # fixed seed = same catalog every run, easier to debug
    rows = [
        (f"SKU-{i:06d}", f"Item {i}", round(random.uniform(0.5, 20.0), 2), STOCK_PER_ITEM)
        for i in range(1, CATALOG_SIZE + 1)
    ]
    c.executemany("INSERT INTO items VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

init_db()
print("DB initialized.")