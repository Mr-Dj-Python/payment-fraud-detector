# Task List — Phase 0 Prototype

Design is done (PRD, HLD, ARCHITECTURE, LLD, C4.html all confirmed). This breaks
the build into chunks so progress can be tracked and resumed across sessions —
each chunk is small enough to finish and verify in one sitting.

Status key: `[ ]` not started · `[~]` in progress · `[x]` done

## Chunk 0 — Design (done)
- [x] PRD.md, HLD.md, ARCHITECTURE.md, LLD.md, C4.html
- [x] Tech stack confirmed: Python 3.15, uv, pytest, asyncio, Babel, stdlib-only data structures

## Chunk 1 — Project scaffolding (done)
- [x] `uv init`, pin Python 3.15 in `pyproject.toml`
- [x] Add dependencies: `babel`, `pytest`
- [x] Create empty module files per LLD §1 layout, adopted `uv`'s default
      `src/payment_fraud_detector/` layout (`config.py`, `models.py`,
      `simulator.py`, `graph_store.py`, `detector.py`, `interceptor.py`,
      `metrics.py`, `main.py`, `tests/`)
- [x] Verified: `uv run payment-fraud-detector` runs, `uv run pytest` collects
      the (empty) test files cleanly

## Chunk 2 — Core data model + config (done)
- [x] `models.py`: `Transaction`, `EdgeRecord`, `AccountStats`, `RollingWindowGraph`,
      `RingVerdict`, `HoldRecord`, `RunMetrics` dataclasses/enums (LLD §3)
- [x] `config.py`: `CurrencyInfo`, `resolve_currency()` via Babel (LLD §2.1),
      `COUNTRY_CODE`, `SIMULATION_START_MS`, `WINDOW_MS`, `SETTLEMENT_WINDOW_MS`,
      detection thresholds (LLD §4) — placeholder numeric values set, **to be
      re-tuned once the simulator in Chunk 4 produces data**
- [x] `tests/test_currency.py` + `tests/test_models.py`: 13/13 passing
      (happy + negative cases) — see `tests/result/2026-11-21/1.md`

## Chunk 3 — Rolling-window graph store (done)
- [x] `graph_store.py`: incremental edge insert, window eviction, counterparty
      counts, running amount sums (LLD §3.3/3.4)
- [x] `tests/test_graph_store.py`: 6/6 passing (happy + negative, incl. exact
      cutoff-boundary eviction case) — see `tests/result/2026-11-21/2.md`
- [x] Flagged, not fixed: `graph.accounts` dict keys are never removed (grows
      with distinct accounts ever seen, not window-bounded) — fine at Phase 0 scale

## Chunk 4 — Transaction simulator (done)
- [x] `simulator.py`: legit traffic, benign fan-out (payroll-like), injected
      ring-fraud traffic (fan-out → mule layer → fan-in → cash-out), each
      labeled with `TxLabel`/`ring_id` (LLD §3.1)
- [x] `tests/test_simulator.py`: 9/9 passing (happy + negative, incl. exact
      remainder-conservation on an uneven split) — see `tests/result/2026-11-21/3.md`
- [ ] Feed back into Chunk 2's open threshold values once real distributions exist
      (deferred to Chunk 8 end-to-end tuning)

## Chunk 5 — Ring detector (done)
- [x] `detector.py`: fan-out/fan-in, hop-chain, near-cycle heuristics +
      verdict aggregator (LLD §3.5, C4 Level 3)
- [x] `tests/test_detector.py`: 11/11 passing (happy + negative, incl. a test
      that documents — not hides — the benign-fan-out false positive from
      PRD §10) — see `tests/result/2026-11-21/4.md`
- [x] Found + resolved a real gap: direction-separated counterparty counts
      needed for fan-out/fan-in, recomputed from the edges deque rather than
      reshaping already-tested `counterparty_counts` — see `detector.py` ponytail comment

## Chunk 6 — Settlement interceptor + metrics (done)
- [x] `interceptor.py`: mocked hold/escrow vs. missed-window outcome (LLD §3.6)
- [x] `metrics.py`: `RunMetrics` aggregation (LLD §3.7)
- [x] `tests/test_interceptor.py` + `tests/test_metrics.py`: 11/11 passing
      (happy + negative, incl. both boundary conditions) — see
      `tests/result/2026-11-21/5.md`

## Chunk 7 — Wiring (`main.py`) (done)
- [x] asyncio ingestion loop: ack path vs. async detection path, wired per
      HLD §3 data flow / C4 Level 2
- [x] Printed end-of-run summary; real demo run confirms: 1/1 ring caught,
      sub-ms ack latency regardless of detection cost
- [x] `tests/test_main.py`: 5/5 passing after fixing a real `detect()` bug
      (hop-chain/near-cycle only checked the receiver, missing the sender-side
      pass-through case) — full regression suite 55/55 — see
      `tests/result/2026-11-21/6.md`
- [x] Confirmed (not fixed, by design): false positives are high (6, untuned) —
      deferred to Chunk 8 threshold tuning

## Chunk 8 — End-to-end validation (done)
- [x] Real threshold sweep (not guessed) found the true false-positive driver:
      `check_near_cycle` had no time constraint at all — fixed by adding
      `MAX_NEAR_CYCLE_MS`, same "fast = suspicious" logic as hop-chain
- [x] `detection_enabled=False` mode + `ack_latency_delta_ms()` added for HLD §5
      N1's real with/without comparison (not assumed from the ack-path design alone)
- [x] Full run against the demo scenario after tuning: 1/1 ring caught, **0**
      false positives (down from 6), ack-latency delta **+0.0001ms**
- [x] `tests/`: 58/58 passing across all chunks — see `tests/result/2026-11-21/7.md`
- [x] Sanity-checked against PRD §6 — see the same report for what these numbers
      do and don't validate (settlement-window assumption still unconfirmed, single
      scenario/seed, not a statistical guarantee)

**All 8 chunks complete — Phase 0 prototype done.**
