"""Database-independent invoice lifecycle and value rules."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum


class DomainError(ValueError):
    """Base class for invalid domain operations or values."""


class InvalidTransition(DomainError):
    """An operation cannot be applied in the invoice's current state."""


class InvalidDomainValue(DomainError):
    """A value violates a domain invariant."""


class InvoiceState(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTED = "extracted"
    REVIEW_REQUIRED = "review_required"
    VALIDATED = "validated"
    APPROVED = "approved"
    POSTED = "posted"
    REJECTED = "rejected"
    FAILED = "failed"


class InvoiceOperation(StrEnum):
    COMPLETE_EXTRACTION = "complete_extraction"
    FAIL_EXTRACTION = "fail_extraction"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_REQUIRES_REVIEW = "validation_requires_review"
    CORRECT = "correct"
    HUMAN_VALIDATE = "human_validate"
    HUMAN_APPROVE = "human_approve"
    HUMAN_REJECT = "human_reject"
    MARK_POSTED = "mark_posted"


_TRANSITIONS: dict[tuple[InvoiceState, InvoiceOperation], InvoiceState] = {
    (InvoiceState.UPLOADED, InvoiceOperation.COMPLETE_EXTRACTION): InvoiceState.EXTRACTED,
    (InvoiceState.UPLOADED, InvoiceOperation.FAIL_EXTRACTION): InvoiceState.FAILED,
    (InvoiceState.EXTRACTED, InvoiceOperation.VALIDATION_PASSED): InvoiceState.VALIDATED,
    (InvoiceState.EXTRACTED, InvoiceOperation.VALIDATION_REQUIRES_REVIEW): InvoiceState.REVIEW_REQUIRED,
    (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.CORRECT): InvoiceState.EXTRACTED,
    (InvoiceState.VALIDATED, InvoiceOperation.CORRECT): InvoiceState.EXTRACTED,
    (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.HUMAN_VALIDATE): InvoiceState.VALIDATED,
    (InvoiceState.VALIDATED, InvoiceOperation.HUMAN_APPROVE): InvoiceState.APPROVED,
    (InvoiceState.REVIEW_REQUIRED, InvoiceOperation.HUMAN_REJECT): InvoiceState.REJECTED,
    (InvoiceState.VALIDATED, InvoiceOperation.HUMAN_REJECT): InvoiceState.REJECTED,
    (InvoiceState.APPROVED, InvoiceOperation.MARK_POSTED): InvoiceState.POSTED,
}


def transition_invoice(state: InvoiceState, operation: InvoiceOperation) -> InvoiceState:
    """Return the next state; the caller persists it if the operation succeeds."""
    if not isinstance(state, InvoiceState) or not isinstance(operation, InvoiceOperation):
        raise InvalidDomainValue("state and operation must use their domain enums")
    try:
        return _TRANSITIONS[state, operation]
    except KeyError as exc:
        raise InvalidTransition(f"Cannot {operation.value} an invoice in {state.value} state") from exc


def require_money(value: Decimal) -> Decimal:
    """Accept an exact, finite, nonnegative monetary value without rounding it."""
    if not isinstance(value, Decimal):
        raise InvalidDomainValue("money must be a Decimal")
    if not value.is_finite() or value < 0:
        raise InvalidDomainValue("money must be finite and nonnegative")
    return value


def require_utc_datetime(value: datetime) -> datetime:
    """Require an aware datetime and return the same instant in UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidDomainValue("timestamp must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)
