"""Uncertain invoice observations, independent of invoice business rules."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Protocol

from pydantic import BeforeValidator, Field, field_validator, model_validator

from meridian.ocr import OcrDocument
from meridian.synthetic import SyntheticModel


class ExtractionError(Exception):
    """An extraction could not be produced or verified."""


class ProviderError(ExtractionError):
    """The configured provider failed."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ResponseError(ExtractionError):
    """The provider returned malformed or unsupported extraction data."""


def exact_money(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError("money must be a decimal string or Decimal, never a JSON number")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal amount") from exc
    if not amount.is_finite() or -amount.as_tuple().exponent > 4:
        raise ValueError("money must be finite and have at most four decimal places")
    if len(amount.as_tuple().digits) + max(amount.as_tuple().exponent, 0) > 18:
        raise ValueError("money exceeds 18 digits")
    return amount


Money = Annotated[Decimal, BeforeValidator(exact_money)]
Uncertainty = Literal["low", "medium", "high"]


class EvidenceRef(SyntheticModel):
    page_number: int = Field(gt=0)
    word_indexes: tuple[int, ...] = Field(min_length=1)
    block_number: int = Field(ge=0)
    paragraph_number: int = Field(ge=0)
    line_number: int = Field(ge=0)


class TextValue(SyntheticModel):
    value: str | None = None
    uncertainty: Uncertainty | None = None
    evidence: tuple[EvidenceRef, ...] = ()

    @field_validator("value")
    @classmethod
    def no_blank_value(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("unreadable text must be null")
        return value


class DateValue(SyntheticModel):
    value: date | None = None
    uncertainty: Uncertainty | None = None
    evidence: tuple[EvidenceRef, ...] = ()


class CountValue(SyntheticModel):
    # Printed zero or negative quantities remain observable; validation is later.
    value: int | None = Field(default=None, strict=True)
    uncertainty: Uncertainty | None = None
    evidence: tuple[EvidenceRef, ...] = ()


class MoneyValue(SyntheticModel):
    value: Money | None = None
    uncertainty: Uncertainty | None = None
    evidence: tuple[EvidenceRef, ...] = ()


class ExtractedLine(SyntheticModel):
    source_row_order: int = Field(gt=0)
    line_number: CountValue
    description: TextValue
    upc: TextValue
    ndc: TextValue
    quantity: CountValue
    unit_cost: MoneyValue
    printed_line_total: MoneyValue


class ExtractedInvoice(SyntheticModel):
    supplier_name: TextValue
    invoice_number: TextValue
    invoice_date: DateValue
    purchase_order_reference: TextValue
    lines: tuple[ExtractedLine, ...]
    printed_subtotal: MoneyValue
    printed_tax: MoneyValue
    printed_total: MoneyValue

    @model_validator(mode="after")
    def unique_source_rows(self):
        orders = [line.source_row_order for line in self.lines]
        if len(orders) != len(set(orders)):
            raise ValueError("source row order must identify each extracted row")
        return self


class ExtractionResult(SyntheticModel):
    source_sha256: str
    provider: str
    model_name: str
    repair_attempted: bool
    invoice: ExtractedInvoice


class ExtractionProvider(Protocol):
    name: str
    model_name: str

    def complete(self, ocr_input: str, repair_output: str | None = None) -> str: ...


def ocr_payload(document: OcrDocument) -> str:
    """Only OCR observations cross the provider boundary."""
    import json

    pages = []
    for page in document.pages:
        lines = {}
        for index, word in enumerate(page.words):
            key = (word.block_number, word.paragraph_number, word.line_number)
            lines.setdefault(key, []).append({
                "index": index, "text": word.text, "confidence": word.confidence,
                "box": [word.left, word.top, word.width, word.height],
            })
        pages.append({"page_number": page.page_number, "text": page.text,
                      "lines": [{"id": list(key), "words": words}
                                for key, words in lines.items()]})
    return json.dumps({"source_sha256": document.source_sha256, "pages": pages},
                      ensure_ascii=False, separators=(",", ":"))


def validate_evidence(invoice: ExtractedInvoice, document: OcrDocument) -> None:
    fields = [invoice.supplier_name, invoice.invoice_number, invoice.invoice_date,
              invoice.purchase_order_reference, invoice.printed_subtotal,
              invoice.printed_tax, invoice.printed_total]
    for line in invoice.lines:
        fields.extend((line.line_number, line.description, line.upc, line.ndc,
                       line.quantity, line.unit_cost, line.printed_line_total))
    pages = {page.page_number: page for page in document.pages}
    for field in fields:
        if field.value is not None and not field.evidence:
            raise ResponseError("non-null value lacks OCR evidence")
        for ref in field.evidence:
            page = pages.get(ref.page_number)
            if page is None or not ref.word_indexes:
                raise ResponseError("OCR evidence refers to an absent page or word")
            if len(set(ref.word_indexes)) != len(ref.word_indexes):
                raise ResponseError("OCR evidence repeats a word index")
            for index in ref.word_indexes:
                if index < 0 or index >= len(page.words):
                    raise ResponseError("OCR evidence word index is out of range")
                word = page.words[index]
                if (word.block_number, word.paragraph_number, word.line_number) != (
                    ref.block_number, ref.paragraph_number, ref.line_number
                ):
                    raise ResponseError("OCR evidence line ID does not match the word")


def extract_invoice(document: OcrDocument, provider: ExtractionProvider,
                    max_retries: int = 1) -> ExtractionResult:
    """Retry transport failures, then attempt one explicit syntax/schema repair."""
    import json
    from pydantic import ValidationError

    if max_retries < 0 or max_retries > 3:
        raise ValueError("max_retries must be between zero and three")
    payload = ocr_payload(document)
    repair_output = None
    for repair_attempted in (False, True):
        for attempt in range(max_retries + 1):
            try:
                raw = provider.complete(payload, repair_output)
                break
            except ProviderError as exc:
                if not exc.retryable or attempt == max_retries:
                    raise
        try:
            if not isinstance(raw, str):
                raise ResponseError("provider output must be JSON text")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            invoice = ExtractedInvoice.model_validate_json(raw)
            validate_evidence(invoice, document)
            return ExtractionResult(source_sha256=document.source_sha256,
                                    provider=provider.name, model_name=provider.model_name,
                                    repair_attempted=repair_attempted, invoice=invoice)
        except (ValueError, ValidationError, ResponseError) as exc:
            if repair_attempted:
                raise ResponseError(f"invalid extraction after one repair attempt: {exc}") from exc
            repair_output = raw
    raise AssertionError("unreachable")
