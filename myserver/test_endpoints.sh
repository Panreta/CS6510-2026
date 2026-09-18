#!/usr/bin/env bash
# Exercises all 7 self-checkout endpoints against a running server.
# Usage: ./test_endpoints.sh [base_url]
# Default base_url is http://localhost:8080

BASE="${1:-http://localhost:8080}"

hr() { echo "----------------------------------------"; }

echo "Testing server at $BASE"
hr

echo "[1] GET /items"
curl -s "$BASE/items" | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK - items count:', len(d['items']))"
hr

echo "[2] POST /transactions"
START_RESP=$(curl -s -X POST "$BASE/transactions" \
  -H "Content-Type: application/json" \
  -d '{"stationId":"station-01"}')
echo "$START_RESP"
TX_ID=$(echo "$START_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['transactionId'])")
echo "Extracted transactionId: $TX_ID"
hr

echo "[3] POST /transactions/{id}/items  (scan SKU-000001, 1st time)"
curl -s -X POST "$BASE/transactions/$TX_ID/items" \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-000001"}'
echo
hr

echo "[4] POST /transactions/{id}/items  (scan SKU-000001, 2nd time -- expect itemCount=2)"
curl -s -X POST "$BASE/transactions/$TX_ID/items" \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-000001"}'
echo
hr

echo "[5] POST /transactions/{id}/items  (scan SKU-000002 -- expect itemCount=3)"
curl -s -X POST "$BASE/transactions/$TX_ID/items" \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-000002"}'
echo
hr

echo "[6] POST /transactions/{id}/complete  (expect a receipt with 2 lines)"
curl -s -X POST "$BASE/transactions/$TX_ID/complete"
echo
hr

echo "[7] POST /transactions/{id}/complete AGAIN  (expect 409 TRANSACTION_NOT_OPEN)"
curl -s -i -X POST "$BASE/transactions/$TX_ID/complete" | head -n 1
echo
hr

echo "[8] GET /transactions/{id}  (expect status COMPLETED)"
curl -s "$BASE/transactions/$TX_ID"
echo
hr

echo "[9] GET /inventory/low-stock  (expect empty alerts)"
curl -s "$BASE/inventory/low-stock"
echo
hr

echo "[10] GET /analytics/popular-items  (expect empty items, only 3 scans so far)"
curl -s "$BASE/analytics/popular-items"
echo
hr

echo "Done. Compare each block above against the expected outputs."