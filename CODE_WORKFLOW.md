# Code Workflow — Runtime Trace, Function by Function

This traces what actually happens in the running code, in call order: where
ingestion starts, which function turns a transaction into stored objects, and how
the detector reads those objects back out. Companion to `WORKFLOW.md` (which
covers how the *project* was built) — this one covers how the *program* runs.

All line numbers refer to files under `src/payment_fraud_detector/`.

## 1. Entry point

```
main()                                  main.py:156
  -> build_demo_scenario()              main.py:111  (builds the transaction list)
  -> asyncio.run(run_simulation(...))   main.py:159  (the real run)
  -> asyncio.run(run_simulation(..., detection_enabled=False))  main.py:161 (baseline, no detection)
  -> print_summary(metrics, baseline)   main.py:140
```

`build_demo_scenario()` doesn't touch the graph or detector at all — it only
calls the simulator functions (`generate_legit_stream`, `generate_benign_fanout`,
`generate_ring`, all in `simulator.py`) to produce a plain `list[Transaction]`,
sorted by `timestamp_ms`. Nothing is "live" yet; these are just dataclass
instances sitting in a list, per LLD §3.1.

## 2. Ingestion starts: `run_simulation()` (main.py:66)

This is where the asyncio machinery is actually set up:

```python
queue: asyncio.Queue = asyncio.Queue()                     # main.py:78
worker = asyncio.create_task(_detection_worker(...))         # main.py:91 — starts running concurrently
for tx in transactions:
    await _ingest(tx, queue, ack_latencies_ms)               # main.py:98-99 — one call per transaction
await queue.put(_STOP)                                       # main.py:101 — tells the worker there's no more
await queue.join()                                           # main.py:102 — waits for the worker to finish
await worker
```

**The ingestion loop (`for tx in transactions: await _ingest(...)`) is the whole
"data ingestion" path.** It runs once per transaction, in order, and each call is
just `_ingest()`:

```python
async def _ingest(tx, queue, ack_latencies_ms):             # main.py:20
    start = time.perf_counter()
    await queue.put(tx)                                      # hand off, nothing else
    ack_latencies_ms.append((time.perf_counter() - start) * 1000)
```

`_ingest()` does exactly one thing: put the `Transaction` object onto the
`asyncio.Queue` and record how long that took. It never calls the graph store,
the detector, or anything else — that's the entire point (HLD §3 step 2: "this is
the only step on the customer-visible path"). The `worker` task, started one line
earlier, is a **separate concurrent coroutine already running in the background**
— by the time `_ingest()` is called for transaction #2, the worker may already be
processing transaction #1. Ingestion and detection genuinely overlap; ingestion
never blocks waiting for detection to catch up.

## 3. Where a transaction becomes stored objects: `insert_transaction()` (graph_store.py:22)

The `_detection_worker()` loop (main.py:27) pulls one `Transaction` off the queue
at a time and calls `insert_transaction(graph, tx)` first, before anything else.
**This is the function that actually turns a `Transaction` into the graph's
internal objects.** Concretely, for a transaction `tx` (sender `A`, receiver `B`,
amount `1000`, timestamp `5000`):

```python
sender_stats = graph.accounts[tx.sender_id]       # graph_store.py:31 — dict lookup, auto-creates
receiver_stats = graph.accounts[tx.receiver_id]   #   an empty AccountStats() if A/B are new (models.py:62-67)

_evict_expired(sender_stats, tx.timestamp_ms, graph.window_ms)   # graph_store.py:34 — drop stale edges first
_evict_expired(receiver_stats, tx.timestamp_ms, graph.window_ms) # graph_store.py:35

out_edge = EdgeRecord(counterparty_id="B", amount=1000, timestamp_ms=5000,
                       transaction_id=tx.transaction_id, direction=EdgeDirection.OUT)
sender_stats.edges.append(out_edge)               # graph_store.py:44 — A's own edge history grows by one
sender_stats.counterparty_counts["B"] += 1         # graph_store.py:45
sender_stats.out_amount_sum += 1000                # graph_store.py:46

in_edge = EdgeRecord(counterparty_id="A", amount=1000, timestamp_ms=5000,
                      transaction_id=tx.transaction_id, direction=EdgeDirection.IN)
receiver_stats.edges.append(in_edge)               # graph_store.py:55 — B's own edge history grows by one
receiver_stats.counterparty_counts["A"] += 1        # graph_store.py:56
receiver_stats.in_amount_sum += 1000                # graph_store.py:57
```

One `Transaction` becomes **two** `EdgeRecord` objects — one filed under the
sender's own `AccountStats.edges` deque (direction `OUT`), one filed under the
receiver's (direction `IN`). Nothing global is built; each account only ever
holds its own edges. `graph.accounts` (a `defaultdict`, models.py:63-66) is the
only place these `AccountStats` objects live — that single dict *is* "the graph."

`_evict_expired()` (graph_store.py:8) runs first, every time, for both accounts —
it pops edges off the front of the deque while they're older than
`tx.timestamp_ms - graph.window_ms`, decrementing `counterparty_counts` and the
relevant amount sum as it goes. This is "the window" in concrete terms: old edges
are physically removed from the deque, not just ignored.

## 4. How the detector reads those same objects: `detect()` (detector.py:63)

Immediately after `insert_transaction()` returns, the worker calls `detect(graph,
tx, ...)` — and `detect()` reads **the exact same two `AccountStats` objects**
`insert_transaction()` just updated (it looks them up again by account id,
`detector.py:80-81`). It never looks at any other account — this is ADR-001's
"local neighborhood only" rule in code: whatever account wasn't touched by this
transaction is never even loaded.

Four independent checks run against those two objects, each reading `.edges` (the
deque built in step 3) directly:

| Check | Function | What it reads |
|---|---|---|
| Fan-out | `check_fan_out(sender_stats, ...)` (detector.py:21) | counts distinct `OUT`-direction counterparties in `sender_stats.edges` |
| Fan-in | `check_fan_in(receiver_stats, ...)` (detector.py:25) | counts distinct `IN`-direction counterparties in `receiver_stats.edges` |
| Hop-chain | `check_hop_chain(...)` (detector.py:29), run on **both** stats | looks for any `IN` edge followed by an `OUT` edge within `max_hop_chain_ms` |
| Near-cycle | `check_near_cycle(...)` (detector.py:40), run on **both** stats | looks for a counterparty that appears in both the `OUT` and `IN` edge sets within `max_near_cycle_ms` |

Each one that fires appends a `RingReason` to a local `reasons` list (deduplicated
by `_add_reason`, detector.py:58) and adds the implicated account(s) to
`accounts_involved`. At the end, `detect()` builds and returns one
`RingVerdict` object (detector.py:115) — `flagged`, the reasons, a confidence
score, the involved accounts, and the specific `EdgeRecord`s that triggered
fan-out/fan-in (evidence, per PRD §6). Nothing is mutated here — `detect()` is
read-only against the graph; all the writing happened in step 3.

## 5. Back in the worker: verdict handling (main.py:52-61)

```python
verdicts[tx.transaction_id] = verdict                 # always recorded, flagged or not
peaks["node"] = max(peaks["node"], node_count(graph))  # graph_store.py:60
peaks["edge"] = max(peaks["edge"], total_edge_count(graph))  # graph_store.py:64

if verdict.flagged:
    detection_latencies_ms.append(elapsed_ms)
    detection_time_ms = tx.timestamp_ms + int(elapsed_ms)
    holds[tx.transaction_id] = intercept(tx, verdict, detection_time_ms, settlement_window_ms)
```

`node_count()`/`total_edge_count()` (graph_store.py:60/64) are simple reads over
`graph.accounts` — called after every single insert so `peaks` tracks the true
maximum over the whole run, not just the final snapshot (edges can shrink via
eviction, so the peak isn't always the last value).

Only if `verdict.flagged` is true does `intercept()` (interceptor.py:11) get
called — it compares `detection_time_ms` against `tx.timestamp_ms +
settlement_window_ms` and returns a `HoldRecord` with outcome `HELD` or `MISSED`
(LLD §3.6). Calling `intercept()` on a non-flagged verdict is treated as a bug and
raises `ValueError` — it's never called that way in the real loop.

## 6. End of run: `summarize()` (metrics.py:11)

Once every transaction has been through steps 3–5 and the `_STOP` sentinel has
been processed, `run_simulation()` calls `summarize(transactions, verdicts,
holds, ack_latencies_ms, detection_latencies_ms, peaks["node"], peaks["edge"],
ring_caught_edge_fraction)`. This is the only function that looks at *all*
transactions at once — it groups `RING_FRAUD`-labeled transactions by `ring_id`,
checks what fraction of each ring's edges ended up `HELD`, and counts any flagged
non-fraud transaction as a false positive. It returns one `RunMetrics` object,
which `print_summary()` (main.py:140) turns into the printed report.

## Object lifecycle, summarized

```
Transaction (from simulator.py)
   │  insert_transaction() — graph_store.py:22
   ▼
2× EdgeRecord  --appended into-->  AccountStats.edges (per account, in graph.accounts)
   │  detect() reads those same AccountStats — detector.py:63
   ▼
RingVerdict (one per transaction, always created)
   │  only if verdict.flagged: intercept() — interceptor.py:11
   ▼
HoldRecord (one per flagged transaction)
   │  after ALL transactions processed: summarize() — metrics.py:11
   ▼
RunMetrics (one per run)
```

## Concurrency model in one sentence

One `asyncio.Queue` connects a single producer (the `for tx in transactions`
loop calling `_ingest()`) to a single consumer (`_detection_worker()` running as
a background `asyncio.Task`) — ingestion only ever waits on `queue.put()`
(effectively instant), and all the real work (graph insert, detection,
interception) happens in the worker, concurrently with ingestion continuing to
accept the next transaction.
