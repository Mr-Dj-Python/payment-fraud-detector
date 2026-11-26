# Workflow — How This Project Was Built, Step by Step

This is the narrative of how `payment-fraud-detector` went from a spoken interview
scenario to a working, tested Phase 0 prototype. Each step below produced one of
the files in this folder — this document explains the *order* and *why*, the other
files hold the actual content.

## Origin

The project started from a Forward Deployed Engineer interview scenario: a
payment gateway's fraud model scores transactions one at a time (~2s each), but an
attacker split ₹18 crore across ~10,000 mule accounts in transfers too small and
too individually-normal-looking for that model to catch — and cashed out in
milliseconds. The two naive fixes ("check full account history per payment" /
"add hard transfer limits") either crash the gateway or block legitimate
customers. The question: how do you catch the *network-shaped* fraud without
slowing down real payments?

That scenario became the subject of this entire project.

## Step 1 — Decide what to build before building it

Before any code: a clarifying round established the deliverable should be a real
**business document chain** (PRD → HLD → ARCHITECTURE → LLD), grounded in real
UPI/NPCI settlement mechanics, written as if a security team were pitching this
internally — not a toy narrative. This decision shaped every document that followed.

## Step 2 — `PRD.md`: the business case

Written first. Covers:
- The business problem and why the naive fixes fail (§1–2)
- The one assumption the whole architecture depends on: UPI's customer-visible
  "instant" payment isn't the same as final inter-bank settlement — there's a real
  gap to act inside (§2)
- Goals, non-goals, stakeholders, and **business-first** success metrics (₹ fraud
  prevented, false-positive cost) with technical metrics as supporting evidence,
  not the headline (§6–7)
- A phased rollout plan: Phase 0 (prove it in simulation) → Phase 1 (shadow mode)
  → Phase 2 (limited production hold) (§9)
- The single biggest open risk, flagged explicitly rather than assumed away:
  whether a real, actionable settlement window exists on UPI rails (§8)

## Step 3 — `HLD.md`: the authoritative design

Once the business case was settled, the **High-Level Design** became the single
source of truth for *how* Phase 0 actually works: system context, the five
components (ingestion, rolling-window graph store, ring detector, settlement
interceptor, metrics), the data flow between them, and the finalized tech choices
(Python/asyncio, synthetic data, virtual clock, heuristics-only detection, Babel
for currency). Every later implementation decision had to trace back to something
in this document.

## Step 4 — `ARCHITECTURE.md`: why, not just what

Structured as ADRs (Architecture Decision Records) rather than prose — each one is
a decision, the naive alternatives that were rejected and why, and the
consequences accepted. Seven decisions ended up here, in the order they were made:

1. **ADR-001** — bounded in-memory incremental graph instead of full-history DB joins
2. **ADR-002** — speculative accept + async detection + hold-within-settlement-window
3. **ADR-003** — heuristics-only detection for Phase 0, GNN explicitly deferred
4. **ADR-004** — single Python process (asyncio), not a multi-service simulation
5. **ADR-005** — synthetic data generator, not a public AML dataset
6. **ADR-006** — a virtual, epoch-anchored clock (added after a follow-up question
   about how timestamps should actually be stored — not wall-clock, but still in
   real epoch-ms form so they convert to human-readable dates)
7. **ADR-007** — Babel/CLDR for country→currency resolution, added after reviewing
   the LLD and realizing a hand-maintained 3-country table doesn't scale to the
   200+ real countries the system claims to support

## Step 5 — `LLD.md`: down to the field

The **Low-Level Design** took HLD's components down to actual data types — every
dataclass field, with a stated reason for *that specific type* (why `int` for
money instead of `Decimal`, why `deque` instead of `list`, why `str`-backed `Enum`
instead of raw strings). This is also where the currency work landed concretely:
`CurrencyInfo` + `resolve_currency()`, and later the epoch-anchored timestamp
convention once that question came up.

## Step 6 — `C4.html`: seeing the shape of it

A self-contained, three-level C4 diagram (Context → Containers → Components,
hand-drawn SVG, no external dependencies) to check the design *looked* right, not
just read right. Code-level (C4 Level 4) was deliberately skipped — it would have
just redrawn what LLD already said as text.

## Step 7 — Tech stack discussion

A dedicated pass settled what the documents had left implicit: Python 3.15 (the
version actually installed, confirmed by checking rather than assuming), `uv` for
environment/dependency management, `pytest` for testing, stdlib-only data
structures (explicitly rejecting a graph database/library — detection only ever
touches a 2-account local neighborhood, never the whole graph). Also explicitly
decided: **no web framework** (FastAPI, etc.) — Phase 0 has no external caller to
serve, so one would model a deployment shape nothing in scope actually needs.

## Step 8 — `TASKS.md`: breaking the build into chunks

Rather than build the whole prototype in one pass, the work was split into 8
small, independently-verifiable chunks (0: design, 1: scaffolding, 2: data model +
config, 3: graph store, 4: simulator, 5: detector, 6: interceptor + metrics, 7:
wiring, 8: end-to-end validation). A matching project memory entry was saved so
the plan survives a context reset, not just this conversation.

## Step 9 — Scaffolding (Chunk 1)

`uv init` pinned Python 3.15 and created the project's standard `src/`-layout
(adopted as-is rather than fighting the tool's convention). `babel` and `pytest`
were added as dependencies, empty module files were created matching LLD's module
map, and the entry point was verified to actually run before moving on.

## Step 10 — Building chunk by chunk, each one proven before the next

From here, a stricter discipline applied to every remaining chunk: **implement →
write real pytest happy-path *and* negative/edge-case tests → run them for
real → store the dated result (`tests/result/2026-11-21/<N>.md` + raw pytest
output) → only then mark the chunk done and move on.** No chunk was checked off
on the assumption that the code "should" work.

- **Chunk 2** — `config.py` + `models.py`. Verified Babel's real behavior (it
  returns `[]` for unknown countries rather than raising) before writing
  `resolve_currency()`, so the error-handling was based on fact, not a guess.
  13/13 tests passed first try.
- **Chunk 3** — `graph_store.py`: incremental insert + window eviction. Included
  a test at the *exact* eviction boundary (not just "comfortably inside/outside"
  the window), since that's where off-by-one bugs actually hide. 6/6 passed.
- **Chunk 4** — `simulator.py`: legit traffic, benign payroll fan-out, and
  injected rings. One test specifically checks that splitting money unevenly
  across mules never drops or invents a paisa — the integer-division remainder
  has to land somewhere. 9/9 passed.
- **Chunk 5** — `detector.py`: the four heuristics (fan-out, fan-in, hop-chain,
  near-cycle) plus a verdict aggregator. One test deliberately proves — rather
  than hides — that a legitimate payroll run above the fan-out threshold gets
  flagged exactly like real fraud, because that's the false-positive risk PRD §10
  already named, not a bug to paper over. Also surfaced a real gap: the LLD's
  single combined `counterparty_counts` can't answer "distinct OUT counterparties"
  on its own, so the detector recomputes direction-filtered counts from the raw
  edge deque instead. 11/11 passed.
- **Chunk 6** — `interceptor.py` (mocked settlement hold) + `metrics.py`
  (end-of-run rollup). Tested both the settlement-window-close boundary and the
  ring-caught-fraction boundary on both sides, and specifically distinguished
  "correctly flagged fraud" from "incorrectly flagged legitimate traffic" so the
  false-positive count can't accidentally include true positives. 11/11 passed.
- **Chunk 7** — `main.py`: the actual asyncio wiring (ack path vs. async
  detection path). **This is where a real bug was caught by a failing test, not
  by review**: the detector only checked hop-chain/near-cycle against a
  transaction's *receiver*, which misses a mule — the account doing the
  "received, then quickly forwarded" pass-through — because in that forwarding
  leg the mule is the *sender*, not the receiver. Fixed, and the full 55-test
  suite was re-run to confirm nothing else broke. A second test failure right
  after that was traced to a flawed *test* assumption, not a code bug, and was
  fixed in the test instead.
- **Chunk 8** — end-to-end validation. A real threshold sweep (actually run, not
  guessed) isolated the true remaining source of false positives: `near_cycle`
  had no time constraint at all, so it flagged ordinary "pay a friend back"
  traffic. Fixed by giving it the same "fast = suspicious" logic hop-chain
  already had. Also added a `detection_enabled=False` mode specifically so the
  ack-latency-with-detection-vs-without comparison HLD §5 asks for could be
  *measured*, not assumed from the design alone.

## Final result

58/58 tests passing. The demo scenario (one injected ring plus ordinary traffic)
is caught 1/1, with 0 false positives (down from 6 before tuning) and an
ack-latency delta of **+0.0001ms** — i.e., detection running asynchronously
genuinely costs legitimate customers nothing measurable, which is the central
architectural claim this whole project exists to test.

## What's still open

This process deliberately did **not** resolve PRD §8's biggest risk: whether a
real, usable settlement window and bank cooperation actually exist on UPI rails.
Everything here proves the *detection mechanism* works in simulation — it doesn't
prove the real-world assumption the mechanism depends on. That's explicitly
Phase 1's job, not this prototype's.

## How to pick this back up

1. Read `TASKS.md` — all 8 chunks are checked off, so this is about *extending*
   the prototype, not finishing it.
2. `uv run pytest tests/` to confirm the baseline still passes.
3. `uv run payment-fraud-detector` to see the current real output.
4. Check `tests/result/2026-11-21/` for the dated history of what was tested and
   found at each step, if you need to know *why* a given constant or check exists.
