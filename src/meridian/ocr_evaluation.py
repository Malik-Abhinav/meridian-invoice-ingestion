"""OCR transcription metrics against printed truth and source-PDF text."""

from collections import Counter
import re

import pypdfium2 as pdfium
from pydantic import Field

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.ocr import OcrDocument, normalize_text
from meridian.synthetic import SyntheticModel


FIELD_NAMES = ("invoice_number", "supplier", "invoice_date", "po_reference",
               "product_identifiers", "quantities", "unit_prices", "totals")


class FieldRecognition(SyntheticModel):
    recognized: int = Field(ge=0)
    expected: int = Field(ge=0)
    missed_values: tuple[str, ...]


class InvoiceOcrEvaluation(SyntheticModel):
    case_id: str
    status: str
    character_error_rate: float | None = None
    word_error_rate: float | None = None
    character_edits: int = 0
    reference_characters: int = 0
    word_edits: int = 0
    reference_words: int = 0
    token_multiset_error_rate: float | None = None
    token_multiset_missing: int = 0
    token_multiset_extra: int = 0
    observed_tokens: int = 0
    fields: dict[str, FieldRecognition] = Field(default_factory=dict)
    processing_seconds: float = Field(ge=0)
    error: str | None = None
    ocr_excerpt: str = ""


class AggregateOcrEvaluation(SyntheticModel):
    reference: str = "selectable source PDF printed text; fields from fixture displayed_invoice"
    transcript_normalization: str = "collapse whitespace, trim; preserve case, punctuation, and OCR characters"
    token_multiset_definition: str = (
        "Whitespace tokens with exact case and punctuation; duplicate counts retained. "
        "For token counts R and O, missing=sum(max(R-O,0)), extra=sum(max(O-R,0)); "
        "rate=(missing+extra)/(reference_tokens+observed_tokens). Not conventional WER."
    )
    invoices: tuple[InvoiceOcrEvaluation, ...]
    character_error_rate: float | None
    word_error_rate: float | None
    token_multiset_error_rate: float | None
    field_accuracy: dict[str, float | None]
    failures: int


def edit_distance(left: list[str] | str, right: list[str] | str) -> int:
    """Levenshtein distance with linear memory."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, 1):
        current = [row]
        for column, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (a != b)))
        previous = current
    return previous[-1]


def token_multiset_difference(reference: str, observed: str) -> tuple[int, int, int, int]:
    """Return missing, extra, reference, observed token counts; duplicates count."""
    ref = Counter(normalize_text(reference).split())
    obs = Counter(normalize_text(observed).split())
    return (sum((ref - obs).values()), sum((obs - ref).values()),
            sum(ref.values()), sum(obs.values()))


def source_pdf_text(source_pdf: bytes) -> str:
    """Read printed page text, including labels absent from field ground truth."""
    with pdfium.PdfDocument(source_pdf) as document:
        pages = []
        for number in range(len(document)):
            page = document[number]
            try:
                text_page = page.get_textpage()
                try:
                    pages.append(text_page.get_text_range())
                finally:
                    text_page.close()
            finally:
                page.close()
    return normalize_text("\n\n".join(pages))


def _printed_fields(truth: FixtureGroundTruth) -> dict[str, tuple[str, ...]]:
    invoice = truth.displayed_invoice
    return {
        "invoice_number": (invoice.invoice_number,),
        "supplier": (invoice.supplier.name,),
        "invoice_date": (invoice.invoice_date.isoformat(),
                         invoice.invoice_date.strftime("%d %b %Y"),
                         invoice.invoice_date.strftime("%B %d, %Y")),
        "po_reference": (invoice.purchase_order_reference,) if invoice.purchase_order_reference else (),
        "product_identifiers": tuple(product.upc or product.ndc for product in invoice.products),
        "quantities": tuple(str(line.quantity) for line in invoice.lines),
        "unit_prices": tuple(f"{line.unit_cost:.4f}" for line in invoice.lines),
        "totals": tuple(f"{amount:.4f}" for amount in (
            invoice.displayed_totals.subtotal, invoice.displayed_totals.tax,
            invoice.displayed_totals.total)) if invoice.displayed_totals else (),
    }


def _present(value: str, text: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9.]){re.escape(value)}(?![A-Za-z0-9.])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def evaluate_invoice(case_id: str, observation: OcrDocument,
                     truth: FixtureGroundTruth, source_pdf: bytes,
                     processing_seconds: float) -> InvoiceOcrEvaluation:
    """Score OCR against printed text; expected corrections are excluded."""
    if case_id != truth.case_id:
        raise ValueError("case ID does not match ground truth")
    reference = source_pdf_text(source_pdf)
    if not reference:
        raise ValueError("source PDF has no selectable reference text")
    observed = normalize_text(observation.normalized_text)
    char_edits = edit_distance(reference, observed)
    reference_words, observed_words = reference.split(), observed.split()
    word_edits = edit_distance(reference_words, observed_words)
    missing, extra, reference_tokens, observed_tokens = token_multiset_difference(reference, observed)
    fields = {}
    for name, values in _printed_fields(truth).items():
        if name == "invoice_date":
            found = any(_present(value, observed) for value in values)
            fields[name] = FieldRecognition(recognized=int(found), expected=1,
                                            missed_values=() if found else (values[0],))
        else:
            missed = tuple(value for value in values if not _present(value, observed))
            fields[name] = FieldRecognition(recognized=len(values) - len(missed),
                                            expected=len(values), missed_values=missed)
    return InvoiceOcrEvaluation(
        case_id=case_id, status="ok", character_error_rate=char_edits / len(reference),
        word_error_rate=word_edits / len(reference_words),
        character_edits=char_edits, reference_characters=len(reference),
        word_edits=word_edits, reference_words=len(reference_words),
        token_multiset_error_rate=(missing + extra) / (reference_tokens + observed_tokens),
        token_multiset_missing=missing, token_multiset_extra=extra,
        observed_tokens=observed_tokens,
        fields=fields, processing_seconds=processing_seconds,
        ocr_excerpt=observed[:240],
    )


def aggregate_evaluations(invoices: tuple[InvoiceOcrEvaluation, ...]) -> AggregateOcrEvaluation:
    char_edits = sum(item.character_edits for item in invoices if item.status == "ok")
    ref_chars = sum(item.reference_characters for item in invoices if item.status == "ok")
    word_edits = sum(item.word_edits for item in invoices if item.status == "ok")
    ref_words = sum(item.reference_words for item in invoices if item.status == "ok")
    token_missing = sum(item.token_multiset_missing for item in invoices if item.status == "ok")
    token_extra = sum(item.token_multiset_extra for item in invoices if item.status == "ok")
    observed_tokens = sum(item.observed_tokens for item in invoices if item.status == "ok")
    totals: Counter = Counter()
    hits: Counter = Counter()
    for item in invoices:
        if item.status != "ok":
            continue
        for name, result in item.fields.items():
            totals[name] += result.expected
            hits[name] += result.recognized
    return AggregateOcrEvaluation(
        invoices=invoices, character_error_rate=char_edits / ref_chars if ref_chars else None,
        word_error_rate=word_edits / ref_words if ref_words else None,
        token_multiset_error_rate=((token_missing + token_extra) / (ref_words + observed_tokens)
                                   if ref_words + observed_tokens else None),
        field_accuracy={name: hits[name] / totals[name] if totals[name] else None for name in FIELD_NAMES},
        failures=sum(item.status != "ok" for item in invoices),
    )
