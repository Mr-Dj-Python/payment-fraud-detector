"""Tunable constants: country/currency, clock anchor, window sizes, detection thresholds. See LLD.md §2.1/§4."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from babel.numbers import get_currency_precision, get_territory_currencies


@dataclass(frozen=True)
class CurrencyInfo:
    code: str
    minor_unit_digits: int


def resolve_currency(country_code: str) -> CurrencyInfo:
    """Resolve an ISO 3166-1 alpha-2 country code to its currency via Babel/CLDR (LLD §2.1).

    Raises ValueError for a country code Babel doesn't recognize (it returns an
    empty list rather than raising, which would otherwise fail silently).
    """
    currencies = get_territory_currencies(country_code, start_date=datetime.date.today())
    if not currencies:
        raise ValueError(f"no currency known for country code {country_code!r}")
    currency_code = currencies[0]
    return CurrencyInfo(code=currency_code, minor_unit_digits=get_currency_precision(currency_code))


COUNTRY_CODE = "IN"
CURRENCY = resolve_currency(COUNTRY_CODE)

# Epoch-anchored virtual clock (ARCHITECTURE.md ADR-006): a fixed, non-"now" anchor
# so timestamps are deterministic but still convert to a real calendar date.
SIMULATION_START_MS = int(
    datetime.datetime(2026, 11, 21, 9, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
)

WINDOW_MS = 5 * 60 * 1000  # rolling graph window: last 5 simulated minutes
SETTLEMENT_WINDOW_MS = 10_000  # placeholder stand-in for PRD §8's unconfirmed real-world window

FAN_OUT_THRESHOLD = 10  # distinct counterparties within WINDOW_MS to trigger FAN_OUT
FAN_IN_THRESHOLD = 10  # distinct counterparties within WINDOW_MS to trigger FAN_IN
MAX_HOP_CHAIN_MS = 100  # max span between first/last hop of a chain to count as suspiciously fast
MAX_NEAR_CYCLE_MS = 100  # max span between a round-trip's out/in edges to count as suspiciously fast
RING_CAUGHT_EDGE_FRACTION = 0.5  # fraction of a ring's true edges that must be caught to count it

# Tuning rationale (Chunk 8, tests/result/2026-11-21/7.md): a real threshold sweep
# against the demo scenario showed the default 2000ms hop/cycle windows let ordinary
# random P2P traffic (pay-then-get-paid-back days apart) false-positive on
# near-cycle; 100ms keeps catching the injected ring (its hops are 10ms apart)
# while eliminating that false-positive source. FAN_OUT/FAN_IN_THRESHOLD (10) were
# not the driver of false positives in this scenario and are left as originally set.
