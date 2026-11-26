"""Babel-backed country -> currency resolution (LLD.md §2.1). Chunk 2."""

import pytest

from payment_fraud_detector.config import CurrencyInfo, resolve_currency


# --- happy path ---

def test_resolves_india_to_inr_with_two_decimal_digits():
    assert resolve_currency("IN") == CurrencyInfo(code="INR", minor_unit_digits=2)


def test_resolves_united_states_to_usd_with_two_decimal_digits():
    assert resolve_currency("US") == CurrencyInfo(code="USD", minor_unit_digits=2)


def test_resolves_japan_to_jpy_with_zero_decimal_digits():
    # JPY has no minor unit at all — the whole reason minor_unit_digits isn't hardcoded to 2.
    assert resolve_currency("JP") == CurrencyInfo(code="JPY", minor_unit_digits=0)


# --- negative / edge cases ---

def test_unknown_country_code_raises_value_error():
    with pytest.raises(ValueError):
        resolve_currency("ZZ")


def test_empty_country_code_raises_value_error():
    with pytest.raises(ValueError):
        resolve_currency("")
