# Architectural Characteristics Analysis — Self-Checkout System (Monolith)

## 1. Required Architectural Characteristics

|Characteristic|Concrete Requirement(s)|
|---|---|
|**Performance**|Starting a transaction must complete in well under 100ms under normal load (10 stations). Scanning an item should stay under ~50ms p95 under normal load.|
|**Consistency**|A SKU's stock must never go negative, even under concurrent completions from multiple stations. Sum of all completed decrements + final stock must equal starting stock (10000 per SKU).|
|**Scalability**|The system should support at least 10x the default station count (100 stations) without returning errors, though latency may degrade.|
|**Reliability / Fault Tolerance**|A crash in one station's transaction must not corrupt or block other stations' transactions.|
|**Availability**|The server must remain responsive to all 7 endpoints for the full duration of a test run (60–120+ seconds) without crashing.|
|**Observability**|Low-stock levels and popular-item rankings must be queryable at any time via `/inventory/low-stock` and `/analytics/popular-items`, reflecting the system's real-time state.|
|**Data Durability**|Catalog, stock, and popularity snapshot data must persist in a database and reinitialize cleanly at the start of each test run.|
|**Maintainability**|Business logic (checkout, inventory, analytics) should be separable enough to be re-implemented under different architectural styles in later assignments, without changing the external API contract.|

## 2. Top 3 Prioritized Characteristics, and Trade-offs

### 1. Consistency

**Implementation choice:** Stock decrements use a guarded SQL update (`UPDATE items SET stock = stock - ? WHERE sku = ? AND stock >= ?`) rather than a read-then-write pattern, so concurrent completions can never push stock below zero. Similarly, the sliding-window popularity trigger (recording a scan, checking the total count, and conditionally recomputing) is wrapped in a `threading.Lock()`, since without it, concurrent scans could each observe a stale scan count and the window would never trigger at all — confirmed empirically: without the lock, `popularItems` came back empty after 84,000+ scans in testing; with the lock, it populated correctly.

**Trade-off — Performance under high concurrency:** This was measured directly. At 10 concurrent stations, `SCAN_ITEM` averaged 6.85ms (p50 0.83ms). At 100 concurrent stations, the same operation averaged 130.24ms (p50 128.38ms) — a ~150x increase in typical latency, even though throughput only roughly doubled. This is a direct consequence of serializing every scan through a single lock: as concurrency increases, more threads queue up waiting their turn at that one checkpoint. Consistency was prioritized over raw scan throughput at scale.

### 2. Correctness of the Checkout Contract

**Implementation choice:** Every endpoint strictly follows the OpenAPI spec's state machine — a transaction must be `OPEN` to accept scans or completion, `404`/`409` responses are returned precisely as specified, and stock is only decremented at completion time, never at scan time (matching the spec's explicit domain model).

**Trade-off — Simplicity vs. flexibility:** Because transaction state is enforced strictly (e.g., a completed transaction can never be reopened or re-scanned), there's no support for mid-transaction corrections (e.g., removing a scanned item) — a real self-checkout kiosk would likely need this. This was accepted for this assignment's scope in favor of a simpler, spec-compliant state machine.

### 3. Simplicity / Development Speed (Monolith-appropriate)

**Implementation choice:** Open transactions are held in a plain in-memory Python dictionary rather than persisted to the database, since only catalog/stock and popularity data have an explicit persistence requirement in the assignment.

**Trade-off — Reliability / Durability:** If the server process crashes or restarts, all in-progress (not-yet-completed) transactions are lost — a customer's scanned basket would simply vanish, with no way to recover it. This trades resilience for implementation simplicity and speed, which was judged an acceptable trade-off for a single-process monolith whose primary grading criteria (this week) are functional correctness and load-test performance, not fault tolerance.

## 3. Load Test Results Summary

|Metric|Run 1 (10 stations, 60s)|Run 2 (100 stations, 120s, stress)|
|---|---|---|
|Transactions completed|8,144|8,755|
|Items scanned|84,241|92,456|
|START_TRANSACTION error rate|0%|0%|
|SCAN_ITEM mean latency|6.85ms|130.24ms|
|COMPLETE_TRANSACTION mean latency|6.91ms|2.82ms|
|Low-stock alerts triggered|1 (SKU-000001)|1 (SKU-000001)|
|Stock ever went negative?|No|No|

Full JSON reports: `reports/report-20260917-205003.json` (Run 1), `reports/report-20260917-205549.json` (Run 2).