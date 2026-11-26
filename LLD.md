# Low-Level Design: Real-Time Mule-Network Interception (Phase 0)

Implements the components defined in [HLD.md](./HLD.md). This document goes down
to field/data-type level for every data structure the Phase 0 prototype needs, with
the reasoning behind each type choice. Architectural rationale (why these
components exist at all) is in [ARCHITECTURE.md](./ARCHITECTURE.md).

## 1. Module layout

```
payment-fraud-detector/
  pyproject.toml                       # project + dependency manifest (uv)
  src/payment_fraud_detector/
    __init__.py
    config.py        # tunable constants (window sizes, thresholds)
    models.py        # all dataclasses in this document
    simulator.py      # synthetic transaction generator
    graph_store.py    # rolling-window in-memory graph
    detector.py        # heuristic ring detection
    interceptor.py     # mocked settlement hold logic
    metrics.py          # run summary aggregation
    main.py              # wires it all together, prints summary
  tests/
    test_detector.py    # ring heuristics: fan-out/in, hop-chain, near-cycle
    test_graph_store.py # window eviction, incremental counters
    test_currency.py    # Babel-backed country→currency resolution (§2.1)
```

`src/`-layout is `uv init`'s default convention (not hand-chosen) — kept rather
than flattened, since fighting a tool's standard layout for no benefit isn't worth
it. Dependencies (Babel, pytest) and the pinned Python version live in
`pyproject.toml`, managed with `uv` — see HLD §4. Run via `uv run
payment-fraud-detector` or `uv run pytest`.

## 2. Core unit conventions (apply to every field below)

| Convention | Choice | Why |
|---|---|---|
| Money | `int`, smallest unit of the **configured currency** | Integer arithmetic has no rounding error, unlike `float`; and it's cheaper than `Decimal` for the high volume of sums/comparisons this does per transaction. `Decimal` is the usual "correct" choice for money, but here the extra precision isn't needed (no currency conversion or tax math) and the perf cost isn't worth it at this scale. "Smallest unit" is deliberately not hardcoded to paise — see `CurrencyInfo` below, since the minor-unit size (100 for INR/USD, 1 for JPY) depends on which currency is configured. |
| Time | `int`, **epoch-anchored virtual clock** milliseconds (same representation as a real Unix epoch timestamp — ms since 1970-01-01 UTC — but computed as `SIMULATION_START_MS + virtual_elapsed_ms`, never read from the real system clock) | Matches ADR-006/HLD §4. Staying in standard epoch-ms form means any timestamp can be fed straight into `datetime.fromtimestamp(ms/1000)` for human-readable evidence reports (PRD §6), while the clock itself remains fully virtual/deterministic — no real sleeps, no wall-clock reads, fully reproducible across runs. `int` makes every comparison a cheap integer comparison, same as a pure relative counter would. |
| Identifiers (account id, transaction id) | `str` | Real-world account/transaction identifiers are opaque and often alphanumeric (e.g. `"ACC004821"`) — using `str` from day one avoids a silent assumption that ids are numeric, which would break the moment real account numbers are substituted in. |

### 2.1 `CurrencyInfo` — derived from configured country, not hardcoded

The simulator previously assumed INR/paise everywhere. That's now a configuration
input, not a baked-in assumption: `config.py` sets a `COUNTRY_CODE`, which is
resolved to a currency and its minor-unit digit count (2 for INR/USD, 0 for JPY —
not every currency has a 1/100th minor unit).

With 200+ real countries, a hand-maintained dict is the wrong tool — it would start
incomplete and go stale the moment a country changes currency. Resolution instead
goes through **Babel** (`babel.numbers`), which ships the same CLDR (Unicode
Common Locale Data Repository) dataset used by browsers/OSes for this exact
problem, covering every ISO 3166 territory:

```python
from babel.numbers import get_territory_currencies, get_currency_precision

def resolve_currency(country_code: str) -> CurrencyInfo:
    currency_code = get_territory_currencies(country_code)[0]   # e.g. "IN" -> "INR"
    minor_unit_digits = get_currency_precision(currency_code)    # e.g. "INR" -> 2, "JPY" -> 0
    return CurrencyInfo(currency_code, minor_unit_digits)
```

| Field | Type | Why this type |
|---|---|---|
| `country_code` | `str` (ISO 3166-1 alpha-2, e.g. `"IN"`, `"US"`, `"JP"`) | The actual configuration input (§4). `str` matches the ISO standard's own representation — no numeric country-code encoding exists worth using instead. |
| `currency_code` | `str` (ISO 4217, e.g. `"INR"`, `"USD"`, `"JPY"`) | Resolved from `country_code` via Babel/CLDR, not entered independently — avoids a config that lets country and currency disagree (e.g. `"US"` + `"INR"`). `str`, matching the ISO 4217 standard representation, same reasoning as account/transaction ids. |
| `minor_unit_digits` | `int` (e.g. `2`, `0`) | The exponent that turns a stored `int` amount back into major units (divide by `10 ** minor_unit_digits`). `int` because it's always a small whole number of decimal digits — never fractional. |

This adds one dependency (`babel`) — justified because the alternative is
maintaining correctness data for 200+ countries by hand, which is exactly the kind
of thing a standard, widely-used library already does correctly and keeps updated.
See ARCHITECTURE.md ADR-007.

## 3. Data model

### 3.1 `Transaction` — one simulated payment

| Field | Type | Why this type |
|---|---|---|
| `transaction_id` | `str` (UUID4 hex) | UUIDs let the simulator generate unique ids with no central counter/coordination — matters once generation is concurrent. `str` form (not raw `UUID` object) keeps it trivially serializable/loggable. |
| `sender_id` | `str` | See §2 — opaque account identifier. |
| `receiver_id` | `str` | Same. |
| `amount` | `int`, in the configured currency's smallest unit | See §2/§2.1 — exact money arithmetic, unit depends on configured `CurrencyInfo`, not hardcoded. |
| `timestamp_ms` | `int` | See §2 — virtual clock, ordering and window-membership checks are pure integer comparisons. |
| `label` | `TxLabel` (`str`-backed `Enum`: `LEGIT`, `RING_FRAUD`, `BENIGN_FANOUT`) | A plain `str` would allow typos (`"ring_fruad"`) to pass silently. A `str`-backed `Enum` gets static/type-checker validation *and* still serializes as a plain string in logs/JSON — best of both, no runtime cost worth avoiding at this volume. |
| `ring_id` | `str \| None` | Only set when `label == RING_FRAUD`; groups every edge belonging to the same injected ring so detection-rate scoring can check "were all/some/none of this ring's edges caught." `None` (not `""`) makes "no ring" unambiguous rather than relying on an empty-string convention. |

### 3.2 `EdgeRecord` — a transaction as seen from one account's side, stored in the rolling window

| Field | Type | Why this type |
|---|---|---|
| `counterparty_id` | `str` | The *other* account in the transfer — stored per-account, so this is the one side of the edge not implied by which account's record it lives in. |
| `amount` | `int`, in the configured currency's smallest unit | See §2/§2.1. |
| `timestamp_ms` | `int` | See §2 — used both for window eviction and for recomputing hop timing during detection. |
| `transaction_id` | `str` | Back-reference to the originating `Transaction`, so flagged evidence (PRD §6 evidence-trail requirement) can be traced to the exact transaction, not just an aggregate. |
| `direction` | `EdgeDirection` (`str`-backed `Enum`: `IN`, `OUT`) | Needed because the same account's deque holds both incoming and outgoing edges for fan-in/fan-out ratio math; an `Enum` here (vs. two separate deques) keeps a single time-ordered sequence per account, which matters for hop-chain detection where in/out order matters. |

### 3.3 `AccountStats` — one account's rolling-window state in the graph store

| Field | Type | Why this type |
|---|---|---|
| `edges` | `collections.deque[EdgeRecord]` | `deque` gives O(1) append (new edge arrives) *and* O(1) popleft (oldest edge evicted) — a `list` would be O(n) on eviction. Since the virtual clock guarantees edges arrive in non-decreasing timestamp order, the oldest edge is always at the front, so a simple two-pointer-style eviction works without sorting. |
| `counterparty_counts` | `collections.Counter[str]` | Tracks how many edges exist per counterparty *within the current window*, incrementally. A plain `set` of counterparties can't be safely decremented when an edge expires (you'd need to know if *another* edge to the same counterparty still exists); `Counter` makes "decrement on evict, remove key at zero" correct and O(1). `len(counterparty_counts)` gives distinct-counterparty count for the fan-out/fan-in ratio. |
| `out_amount_sum` | `int`, in the configured currency's smallest unit | Maintained incrementally (+amount on insert, -amount on evict) so the detector never re-sums the deque on the hot path — O(1) read instead of O(window size). |
| `in_amount_sum` | `int`, in the configured currency's smallest unit | Same, for incoming total. |

### 3.4 `RollingWindowGraph` — the store itself

| Field | Type | Why this type |
|---|---|---|
| `accounts` | `collections.defaultdict[str, AccountStats]` | `defaultdict` removes the "does this account already have an entry" branch from the hot insert path — a brand-new mule account (the common case in a ring) gets a fresh `AccountStats()` with zero special-casing. |
| `window_ms` | `int` | Configured window length in virtual ms (e.g. `5 * 60 * 1000`); `int` for the same reason as all other time fields — direct comparison against `timestamp_ms`. |

### 3.5 `RingVerdict` — detector output for one transaction

| Field | Type | Why this type |
|---|---|---|
| `flagged` | `bool` | The interceptor only needs a binary branch (act or don't) — anything richer belongs in the fields below, not encoded into this one. |
| `reason_codes` | `list[RingReason]` (`str`-backed `Enum`: `FAN_OUT`, `FAN_IN`, `HOP_CHAIN`, `NEAR_CYCLE`) | `list`, not `set`, because order of detection (which rule fired first) is useful debugging/evidence context and costs nothing to keep. Enum values (not raw strings) so every reason code used in the codebase is one of a known, greppable set — matters for PRD §6's "evidence-backed" requirement: an analyst-facing report can map each code to a fixed explanation. |
| `confidence_score` | `float` (0.0–1.0) | A simple weighted combination of the heuristic signals (not a calibrated probability) — `float` is the right type for a score meant to be thresholded/ranked, without implying the statistical rigor a model-derived probability would. |
| `accounts_involved` | `set[str]` | Evidence for which accounts to hold. `set` because membership testing ("is this account part of a flagged ring?") is the operation the interceptor actually performs, and duplicates across multiple firing rules must collapse automatically. |
| `flagged_edges` | `list[EdgeRecord]` | Reuses `EdgeRecord` rather than inventing a new shape — the evidence *is* just "these specific edges," and reusing the type avoids a second definition to keep in sync. `list` because duplicate edges can't occur (each is a distinct transaction) and insertion order (roughly chronological) is useful for a human reading the evidence. |

### 3.6 `HoldRecord` — interceptor output

| Field | Type | Why this type |
|---|---|---|
| `transaction_id` | `str` | Links back to the triggering transaction. |
| `hold_time_ms` | `int` | Virtual time the hold was placed — needed to compute margin against the window close. |
| `window_close_ms` | `int` | Virtual time the settlement window for these funds would have closed. Stored explicitly (not recomputed later) so a later audit of "how close were we" doesn't depend on reconstructing simulator state. |
| `outcome` | `HoldOutcome` (`str`-backed `Enum`: `HELD`, `MISSED`) | A `bool` (`True`/`False`) would force the reader to remember which direction is good; the named `Enum` values are self-describing in logs and in the metrics summary, at zero extra cost. |
| `accounts_frozen` | `set[str]` | Same reasoning as `RingVerdict.accounts_involved` — only present when `outcome == HELD`. |

### 3.7 `RunMetrics` — end-of-run summary

| Field | Type | Why this type |
|---|---|---|
| `rings_injected` | `int` | Simple count; ground truth comes from the simulator's labels, not from detection. |
| `rings_caught` | `int` | Count where at least the defined "caught" threshold of a ring's edges were flagged and held in time (threshold itself is a `config.py` constant, not hardcoded here). |
| `rings_missed` | `int` | `rings_injected - rings_caught`, but stored explicitly rather than derived, so a printed summary doesn't require recomputation or risk drifting from the two counts above if either changes independently during development. |
| `false_positives` | `int` | Count of `BENIGN_FANOUT`-labeled transactions that were flagged — the PRD §6/§7 false-positive metric directly. |
| `detection_latencies_ms` | `list[int]` | Raw per-ring latency samples, not a running mean — keeping raw samples defers the choice of statistic (p50 vs p99 vs max) to report time instead of baking one in during the run, and costs nothing at this data volume. |
| `ack_latencies_ms` | `list[int]` | Same reasoning, for the customer-visible ack path (must stay ~0 by construction — this list is the evidence, not an assumption). |
| `peak_node_count` | `int` | Largest `len(graph.accounts)` seen during the run — evidence for HLD §5's "bounded memory" NFR. |
| `peak_edge_count` | `int` | Largest total edges across all accounts' deques seen during the run — same purpose. |

## 4. Config constants (`config.py`)

| Constant | Type | Why this type |
|---|---|---|
| `COUNTRY_CODE` | `str` (ISO 3166-1 alpha-2) | The single configuration input for §2.1 — changing this to `"US"` or `"JP"` (or any of 200+ ISO territories) re-derives currency and minor-unit digits via Babel/CLDR with no other code change. |
| `CURRENCY` | `CurrencyInfo` | Resolved once at startup (`resolve_currency(COUNTRY_CODE)`, §2.1) and reused everywhere an amount is formatted or reasoned about — avoids repeating the Babel lookup per call, and guarantees every part of the run agrees on one currency. |
| `SIMULATION_START_MS` | `int` (epoch ms, e.g. `int(datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)`) | The anchor every `timestamp_ms` in a run is computed from (§2) — a fixed constant, not `datetime.now()`, so runs stay deterministic/reproducible while timestamps still look like real calendar dates. |
| `WINDOW_MS` | `int` | Rolling graph window length — `int` ms, per §2. |
| `SETTLEMENT_WINDOW_MS` | `int` | How long simulated funds remain hold-able after a transaction — the configurable stand-in for PRD §2/§8's unconfirmed real-world assumption. Kept as a single named constant specifically so it's trivial to sweep across values when producing the cost-benefit numbers HLD §5 calls for. |
| `FAN_OUT_THRESHOLD` | `int` | Minimum distinct counterparties within the window to trigger the `FAN_OUT` reason code. `int` (a count), not a ratio, because it's compared directly against `len(counterparty_counts)`. |
| `FAN_IN_THRESHOLD` | `int` | Same, for incoming edges. |
| `MAX_HOP_CHAIN_MS` | `int` | Maximum virtual-time span allowed between the first and last hop of a chain for it to count as suspiciously fast — `int` ms, comparable directly to `timestamp_ms` deltas. |
| `RING_CAUGHT_EDGE_FRACTION` | `float` | Fraction (0.0–1.0) of a ring's true edges that must be caught for `RunMetrics.rings_caught` to count it — `float` because it's a proportion, not a count, and rings can have different total edge counts. |

## 5. Notes on omissions

- No database/ORM types — Phase 0 is in-memory only, per HLD §4; persistence is out
  of scope until a real integration phase.
- No network/serialization types (JSON schema, protobuf, etc.) — everything above
  is in-process Python objects; a wire format is only needed once this talks to a
  real service, which is explicitly out of scope for this HLD/LLD (HLD §7).
