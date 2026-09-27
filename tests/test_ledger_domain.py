import uuid
from decimal import Decimal

import pytest

from app.domain.ledger import (
    DESCRIPTION_MAX_LENGTH,
    EntryInput,
    UnbalancedTransactionError,
    assert_balanced,
    validate_description,
)


def _entry(entry_type: str, amount: str, currency: str = "INR") -> EntryInput:
    return EntryInput(
        account_id=uuid.uuid4(),
        entry_type=entry_type,
        amount=Decimal(amount),
        currency=currency,
    )


def test_balanced_transaction_passes():
    entries = [_entry("debit", "100.00"), _entry("credit", "100.00")]
    assert_balanced(entries)  # should not raise


def test_unbalanced_transaction_raises():
    entries = [_entry("debit", "100.00"), _entry("credit", "99.00")]
    with pytest.raises(UnbalancedTransactionError):
        assert_balanced(entries)


def test_currencies_are_balanced_independently():
    # INR side balances, USD side balances — should pass even though
    # it's a 4-entry, 2-currency transaction.
    entries = [
        _entry("debit", "100.00", "INR"),
        _entry("credit", "100.00", "INR"),
        _entry("debit", "50.00", "USD"),
        _entry("credit", "50.00", "USD"),
    ]
    assert_balanced(entries)


def test_mixed_currency_imbalance_is_caught():
    entries = [
        _entry("debit", "100.00", "INR"),
        _entry("credit", "99.00", "INR"),
        _entry("debit", "50.00", "USD"),
        _entry("credit", "50.00", "USD"),
    ]
    with pytest.raises(UnbalancedTransactionError):
        assert_balanced(entries)


def test_zero_or_negative_amount_rejected():
    with pytest.raises(ValueError):
        EntryInput(
            account_id=uuid.uuid4(),
            entry_type="debit",
            amount=Decimal("0"),
            currency="INR",
        )


def test_invalid_entry_type_rejected():
    with pytest.raises(ValueError):
        EntryInput(
            account_id=uuid.uuid4(),
            entry_type="sideways",
            amount=Decimal("10"),
            currency="INR",
        )


@pytest.mark.parametrize("amount", ["100.005", "0.001", "12345678901234567"])
def test_amounts_the_column_cannot_store_exactly_are_rejected(amount):
    """Numeric(18, 2) would round these, or overflow on the last one."""
    with pytest.raises(ValueError):
        _entry("debit", amount)


@pytest.mark.parametrize("amount", ["100", "100.5", "100.000", "9999999999999999.99"])
def test_amounts_the_column_stores_exactly_are_accepted(amount):
    assert _entry("debit", amount).amount == Decimal(amount)


def test_description_longer_than_the_column_is_rejected():
    validate_description("x" * DESCRIPTION_MAX_LENGTH)
    validate_description(None)
    with pytest.raises(ValueError, match="at most 512 characters"):
        validate_description("x" * (DESCRIPTION_MAX_LENGTH + 1))
