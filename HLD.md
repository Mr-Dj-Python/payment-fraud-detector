# High-Level Design: Real-Time Mule-Network Interception

Authoritative design document. See [PRD.md](./PRD.md) for business framing and
[ARCHITECTURE.md](./ARCHITECTURE.md) for the architectural decision records (ADRs)
this design is built on.

Scope: **Phase 0** only, per PRD §9 — a simulation that proves the detection
mechanism and produces numbers for the cost-benefit case. Phases 1–2 (shadow mode,
limited production rollout) are out of scope for this document.

## 1. System context

This sits alongside the existing per-transaction fraud model, not in place of it.

```
   Sender  -->  [ Existing payment gateway / per-tx fraud check ]  -->  Receiver
                                   │
                                   │ (every settled transaction, fire-and-forget)
                                   v
                     [ Mule-Network Interception — this system ]
                                   │
                                   │ (only if a ring is flagged, and only within
                                   │  the settlement window)
                                   v
                     [ Settlement hold / escrow — mocked in Phase 0 ]
```

In Phase 0, "existing payment gateway" and "settlement hold" are both simulated —
there is no real integration. The goal is to validate the interception system's own
behavior (detection rate, false-positive rate, added latency) in isolation.

## 2. Components

| Component | Responsibility |
|---|---|
| **Transaction simulator** | Generates a timed stream of synthetic transactions: normal P2P/merchant traffic, benign fan-out (payroll-like), and injected fraud rings (fan-out → mule layer → fan-in → cash-out). Drives the virtual clock. |
| **Ingestion** | Receives each transaction, immediately acks the "sender," and hands the transaction off asynchronously — this is the boundary that must never block on detection. |
| **Rolling-window graph store** | In-memory graph (accounts = nodes, transfers = edges), holding only the last N minutes (virtual time) of activity. Evicts expired edges. Exposes incremental update (add edge) and local-neighborhood lookup. |
| **Ring detector** | Runs heuristics against the edge just added and its local neighborhood: fan-out/fan-in ratio, hop-chain depth within the window, near-cycle back to origin. Returns clean or flagged (+ evidence: the account/edge set). |
| **Settlement interceptor (mocked)** | If flagged before the simulated settlement window for those funds closes, records a hold. If the window has already closed, records a miss. This is the Phase-0 stand-in for a real bank/NPCI call. |
| **Metrics/reporting** | Aggregates results: rings injected vs. caught, false positives on benign traffic, latency distribution on the ingestion→ack path. Printed as a summary at the end of a run. |

## 3. Data flow (per transaction)

1. Simulator emits a transaction at virtual time `t` with `(sender, receiver, amount)`.
2. Ingestion assigns it a timestamp, acks immediately, and enqueues it for graph
   update — **this is the only step on the "customer-visible" path**.
3. Graph store adds the edge, evicts anything older than the window.
4. Ring detector inspects the local neighborhood of `sender`/`receiver` only (not
   the whole graph) and returns a verdict.
5. If flagged: interceptor checks whether `t + detection_latency < settlement_window_close(t)`.
   If yes → hold recorded. If no → miss recorded (counted, not hidden).
6. Metrics collector logs the outcome for the end-of-run summary.

Steps 3–6 happen off the critical path from step 2 onward.

## 4. Technology choices (finalized for Phase 0)

| Decision | Choice | Rationale |
|---|---|---|
| Language/runtime | Python 3.15, `asyncio` | Single process; async/queues model the critical-path vs. detection-path split without real service/network overhead. Version pinned to what's installed on the build machine. See [ARCHITECTURE.md](./ARCHITECTURE.md) ADR-004. |
| Environment/dependency management | `uv` | Single lockfile + fast installs for the project's one real dependency (Babel) plus pytest; avoids `pip`/`venv` boilerplate without the overhead poetry would add for a project this size. |
| Testing | `pytest` | One test module per non-trivial piece (ring heuristics, window eviction, currency resolution) — standard fixtures/parametrize if the suite grows past Phase 0. |
| Data | Custom synthetic generator | Need labeled ground truth (which edges are a ring vs. a benign look-alike) to measure detection/false-positive rate. ADR-005. |
| Clock | Virtual/logical clock, decoupled from wall time | Deterministic, reproducible, and lets a "5-minute window" be a number instead of an actual wait. ADR-006. |
| Detection algorithm | Deterministic heuristics (fan-out/fan-in ratio, hop-chain depth, near-cycle) | Scopes Phase 0 to proving the architecture; a GNN scorer is a later upgrade (ADR-002), needs labeled training data and model evaluation this phase doesn't cover. |
| Graph structure | Adjacency maps keyed by account, edges as `(timestamp, amount)` tuples in a per-account deque, window eviction on insert | O(1)-ish amortized insert/evict; no external graph DB needed at this scale. |
| Output | Single script, printed summary | A CLI or dashboard is presentation polish — add later if the numbers are worth presenting live. |
| Country/currency resolution | Configured `COUNTRY_CODE`, resolved dynamically via Babel/CLDR (not a hardcoded currency or hand-maintained table) | Supports all 200+ ISO territories correctly, including currencies without a 1/100th minor unit (e.g. JPY). See ARCHITECTURE.md ADR-007. |

## 5. Non-functional requirements and how this design meets them

| NFR (from PRD/ARCHITECTURE) | How Phase 0 demonstrates it |
|---|---|
| No customer-visible latency added | Measure ack latency with detection enabled vs. disabled; report the delta — must be ~0 by construction, since detection is off the ack path. |
| Detection fast relative to settlement window | Report detection latency (virtual time from last contributing edge to verdict) against the configured `settlement_window_ms`, at several window sizes. |
| Bounded memory, no full-history scans | Window eviction keeps graph size bounded regardless of run length; report peak node/edge count as evidence. |
| Graceful degradation (fail open on payments, fail closed on holds) | Simulated by disabling the detector mid-run and confirming ingestion/ack is unaffected. |

## 6. Assumptions and dependencies (carried from PRD/ARCHITECTURE, not resolved here)

- A real settlement window of usable length exists on UPI rails, and a hold action
  is operationally/legally possible within it. **Unconfirmed** — Phase 0 only
  models this as a configurable parameter; see PRD §8.
- Benign fan-out (payroll, merchant batches) is distinguishable from a mule ring by
  the heuristics chosen — Phase 0's false-positive metric is the test of this, not
  an assumption taken for granted.

## 7. Out of scope for this HLD

- Real NPCI/bank settlement integration (Phase 1+).
- GNN-based detection (deferred; see ARCHITECTURE.md ADR-002).
- Any UI/dashboard beyond a printed run summary.
- Low-level design (exact function signatures, class layout, file structure) — to
  be covered in a separate LLD once this HLD is confirmed.
