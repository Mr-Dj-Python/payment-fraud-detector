# Architecture Decision Record

This is a log of the key architectural decisions for the Mule-Network Interception
project and the reasoning behind each. The authoritative design is
[HLD.md](./HLD.md); this document explains *why* it looks that way. Business
context is in [PRD.md](./PRD.md).

---

### ADR-001: Detect via bounded, incremental in-memory graph — not full-history DB joins

**Context:** The fraud signal (a laundering ring) only exists in the *shape* of
money movement across many accounts, not in any single transaction. The obvious way
to see that shape — query full sender/receiver transaction history per payment — is
O(history) per request and saturates the database at gateway volume.

**Decision:** Maintain a rolling window (e.g. last 5 minutes) of transactions as a
live in-memory graph (accounts = nodes, transfers = edges). Detection runs only
against this bounded structure, updated incrementally per transaction, never
against durable history on the hot path.

**Rejected alternatives:**
- *Hard per-account/per-transaction limits* — trivially defeated by splitting
  amounts across many small transfers (the attack already does this), and
  penalizes legitimate high-volume accounts (merchants, payroll).
- *Run the existing per-transaction model faster* — doesn't fix the structural
  blind spot; a faster scorer of the wrong unit of analysis (one edge) still can't
  see a multi-edge ring.

**Consequences:** Detection is bounded by window size, not total history — memory
and compute stay flat regardless of platform scale. Tradeoff: a ring that forms
slower than the window length (e.g. spread over hours) would not be caught by this
mechanism as designed.

---

### ADR-002: Speculative accept + async detection + hold-within-settlement-window

**Context:** Legitimate payments cannot wait on network-level fraud detection to
complete; the existing 2-second-per-transaction model is already a hard enough
constraint. But detection also can't run instead of acceptance, or it adds
customer-visible latency.

**Decision:** Accept and ack the sender immediately on existing checks. Run ring
detection asynchronously, off the critical path. Only if a ring is flagged, and
only while the funds are still within the settlement window (see PRD §2/§8), issue
a hold/escrow action — never by declining the sender's payment outright.

**Consequences:** Legitimate traffic pays zero detection latency, by construction.
The tradeoff is explicit and accepted: a ring whose detection verdict lands after
the settlement window closes is a miss, not a system failure — see HLD §5 for how
this is measured rather than hidden.

---

### ADR-003: Heuristics-only detection for Phase 0; GNN deferred

**Context:** A GNN-based scorer over the local k-hop neighborhood is a plausible
upgrade path over hand-written rules, but needs labeled training data and a model
quality evaluation — a separate effort from proving the architecture.

**Decision:** Phase 0 uses deterministic heuristics only (fan-out/fan-in ratio,
hop-chain depth within the window, near-cycle detection). GNN scoring is explicitly
out of scope until Phase 0's numbers justify the additional investment.

**Consequences:** Faster to build and fully explainable (useful for PRD §6's
evidence-trail requirement). Tradeoff: heuristics may miss subtler ring shapes a
learned model would catch — acceptable for proving the architecture, not necessarily
for a production detection rate target.

---

### ADR-004: Single Python process (asyncio), not multi-service simulation

**Context:** The architecture's key claim is a separation between the
customer-visible path and the async detection path — not a claim about service
topology.

**Decision:** Simulate this separation with asyncio/queues inside a single process,
rather than standing up separate services communicating over sockets/HTTP.

**Consequences:** Much lower build/run complexity for Phase 0. Tradeoff: doesn't
exercise real network latency/failure modes between services — acceptable, since
Phase 0's job is to validate the detection mechanism, not a deployment topology.

---

### ADR-005: Synthetic data generator, not a public AML dataset

**Context:** Measuring detection rate and false-positive rate requires ground truth
— knowing exactly which edges are part of an injected ring versus a benign
look-alike (e.g. payroll fan-out).

**Decision:** Write a custom synthetic transaction generator with configurable,
labeled ring injection, rather than adopting a public dataset (e.g. IBM AMLSim,
PaySim).

**Consequences:** Full control over exactly which patterns are tested, at the cost
of the data being synthetic rather than real-world-validated. A public dataset
remains an option for a later validation pass, not for Phase 0.

---

### ADR-006: Virtual/logical clock, not real wall-clock timing

**Context:** Real settlement windows and detection windows operate on timescales
(minutes to seconds) that would make a wall-clock simulation slow to run and
non-reproducible across runs.

**Decision:** Drive the simulation with a virtual/logical clock, decoupled from
wall time, so any window length is just a number compared against other numbers.
The clock is **epoch-anchored**: every timestamp is in standard Unix epoch-ms form
(`SIMULATION_START_MS + virtual_elapsed_ms`, a fixed config constant plus virtual
progress — never a real `datetime.now()`/`time.time()` read), so timestamps remain
fully deterministic but still convert directly to human-readable calendar dates for
analyst evidence reports (PRD §6), rather than being a meaningless relative counter.

**Consequences:** Runs are fast and deterministic. Tradeoff: doesn't capture real
scheduling jitter/latency variance a wall-clock run might surface — acceptable for
Phase 0's goal of proving detection logic, not system timing behavior under load.

---

### ADR-007: Resolve currency via Babel/CLDR, not a hand-maintained country→currency table

**Context:** Money amounts are stored as `int` in the smallest unit of a configured
currency (ADR-001 territory, LLD §2). The country this runs for is configurable, and
there are 200+ ISO 3166 countries — each needing both its correct ISO 4217 currency
code and that currency's minor-unit digit count (2 for most, 0 for currencies like
JPY that have no subunit). A hand-written dict covering this would start incomplete
and silently go stale as currencies change.

**Decision:** Resolve `country_code → (currency_code, minor_unit_digits)` at startup
using Babel (`babel.numbers.get_territory_currencies` /
`get_currency_precision`), which ships the Unicode CLDR dataset — the same
authoritative source browsers and operating systems use for this exact problem.

**Rejected alternatives:**
- *Hand-maintained dict* — fine at 3 entries, wrong tool at 200+; becomes a
  maintenance liability that duplicates data a standard library already gets right.
- *No configurability (keep INR/paise hardcoded)* — simplest, but was the explicit
  requirement this ADR responds to.

**Consequences:** Adds one dependency (`babel`) to the Phase 0 build. Justified
because the alternative — maintaining correctness for 200+ countries by hand — is
strictly worse than depending on a widely-used library that already solves it and
stays current.
