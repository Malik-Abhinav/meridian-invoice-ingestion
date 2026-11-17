from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from meridian.domain import (
    InvalidDomainValue,
    InvalidTransition,
    InvoiceOperation,
    InvoiceState,
    require_money,
    require_utc_datetime,
    transition_invoice,
)


@pytest.mark.parametrize(
    ("start", "operation", "expected"),
    [
        (InvoiceState.UPLOADED, InvoiceOperation.COMPLETE_EXTRACTION, InvoiceState.EXTRACTED),
        (InvoiceState.UPLOADED, InvoiceOperation.FAIL_EXTRACTION, InvoiceState.FAILED),
        (InvoiceState.EXTRACTED, InvoiceOperation.VALIDATION_PASSED, InvoiceState.VALIDATED),
        (InvoiceState.EXTRACTED, InvoiceOperation.VALIDATION_REQUIRES_REVIEW, InvoiceState.REVIEW_REQUIRED),
        (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.CORRECT, InvoiceState.EXTRACTED),
        (InvoiceState.VALIDATED, InvoiceOperation.CORRECT, InvoiceState.EXTRACTED),
        (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.HUMAN_VALIDATE, InvoiceState.VALIDATED),
        (InvoiceState.VALIDATED, InvoiceOperation.HUMAN_APPROVE, InvoiceState.APPROVED),
        (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.HUMAN_REJECT, InvoiceState.REJECTED),
        (InvoiceState.VALIDATED, InvoiceOperation.HUMAN_REJECT, InvoiceState.REJECTED),
        (InvoiceState.APPROVED, InvoiceOperation.MARK_POSTED, InvoiceState.POSTED),
    ],
)
def test_allowed_transitions(start: InvoiceState, operation: InvoiceOperation, expected: InvoiceState) -> None:
    assert transition_invoice(start, operation) is expected


@pytest.mark.parametrize(
    ("state", "operation"),
    [
        (InvoiceState.UPLOADED, InvoiceOperation.HUMAN_APPROVE),
        (InvoiceState.EXTRACTED, InvoiceOperation.HUMAN_APPROVE),
        (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.HUMAN_APPROVE),
        (InvoiceState.VALIDATED, InvoiceOperation.MARK_POSTED),
        (InvoiceState.REJECTED, InvoiceOperation.MARK_POSTED),
        (InvoiceState.REJECTED, InvoiceOperation.CORRECT),
        (InvoiceState.POSTED, InvoiceOperation.CORRECT),
        (InvoiceState.POSTED, InvoiceOperation.MARK_POSTED),
        (InvoiceState.FAILED, InvoiceOperation.COMPLETE_EXTRACTION),
    ],
)
def test_invalid_transitions(state: InvoiceState, operation: InvoiceOperation) -> None:
    with pytest.raises(InvalidTransition):
        transition_invoice(state, operation)


def test_every_unspecified_transition_is_rejected() -> None:
    allowed = 0
    for state in InvoiceState:
        for operation in InvoiceOperation:
            try:
                transition_invoice(state, operation)
            except InvalidTransition:
                continue
            allowed += 1
    assert allowed == 11


@pytest.mark.parametrize("state", [InvoiceState.POSTED, InvoiceState.REJECTED, InvoiceState.FAILED])
@pytest.mark.parametrize("operation", list(InvoiceOperation))
def test_terminal_states_reject_every_operation(state: InvoiceState, operation: InvoiceOperation) -> None:
    with pytest.raises(InvalidTransition):
        transition_invoice(state, operation)


def test_transition_requires_domain_enums() -> None:
    with pytest.raises(InvalidDomainValue):
        transition_invoice("uploaded", InvoiceOperation.COMPLETE_EXTRACTION)  # type: ignore[arg-type]
    with pytest.raises(InvalidDomainValue):
        transition_invoice(InvoiceState.UPLOADED, "complete_extraction")  # type: ignore[arg-type]


def test_money_preserves_decimal_precision() -> None:
    value = Decimal("0.1000")
    assert require_money(value) == value
    assert require_money(value).as_tuple().exponent == -4
    assert require_money(Decimal("0")) == Decimal("0")


@pytest.mark.parametrize("value", [0.1, 1, "0.1", True, None, Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01")])
def test_invalid_money(value: object) -> None:
    with pytest.raises(InvalidDomainValue):
        require_money(value)  # type: ignore[arg-type]


def test_aware_timestamp_normalizes_to_utc() -> None:
    local = datetime(2026, 11, 13, 18, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert require_utc_datetime(local) == datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [datetime(2026, 11, 13), "2026-11-13T13:00:00Z", None])
def test_invalid_timestamp(value: object) -> None:
    with pytest.raises(InvalidDomainValue):
        require_utc_datetime(value)  # type: ignore[arg-type]
