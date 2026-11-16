"""Ground-truth field checks and transcript metric behavior."""

from pathlib import Path

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.ocr import OcrDocument, OcrPage, PreprocessingConfig
from meridian.ocr_evaluation import (
    InvoiceOcrEvaluation, _present, aggregate_evaluations, edit_distance, evaluate_invoice, source_pdf_text,
    token_multiset_difference,
)


ROOT = Path(__file__).resolve().parents[1] / "output/evaluation"


def test_edit_distance_for_characters_and_words() -> None:
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance(["one", "two"], ["one", "three"]) == 1
    assert edit_distance("", "abc") == 3


def test_token_multiset_preserves_duplicates_but_ignores_order() -> None:
    assert token_multiset_difference("A B A", "A A B") == (0, 0, 3, 3)
    assert edit_distance("A B A".split(), "A A B".split()) > 0
    assert token_multiset_difference("A A B", "A B B") == (1, 1, 3, 3)
    assert token_multiset_difference("A  A\nB", "A B") == (1, 0, 3, 2)


def test_field_presence_does_not_count_numeric_substrings() -> None:
    assert not _present("4", "4.0000")
    assert not _present("4", "0.4")
    assert _present("4", "Qty 4 Unit cost 4.0000")
    assert _present("17.5000", "Price 17.5000")


def test_exact_printed_reference_scores_zero_and_uses_displayed_values() -> None:
    truth = FixtureGroundTruth.model_validate_json(
        (ROOT / "ground_truth/fixture_a_arithmetic_02.json").read_text())
    source = (ROOT / "original/fixture_a_arithmetic_02.pdf").read_bytes()
    reference = source_pdf_text(source)
    observation = OcrDocument(
        source_sha256="0" * 64, engine_version="test", configuration=PreprocessingConfig(),
        pages=(OcrPage(page_number=1, text=reference, words=(), image_width=100,
                       image_height=100, deskew_angle_degrees=0),), normalized_text=reference,
    )
    result = evaluate_invoice(truth.case_id, observation, truth, source, 0.1)
    assert result.character_error_rate == result.word_error_rate == 0
    assert result.token_multiset_error_rate == 0
    assert result.fields["invoice_number"].recognized == 1
    assert result.fields["totals"].recognized == 3
    assert truth.displayed_invoice.displayed_totals.subtotal != truth.expected_correct_totals.subtotal


def test_both_transcripts_receive_identical_whitespace_normalization() -> None:
    truth = FixtureGroundTruth.model_validate_json(
        (ROOT / "ground_truth/fixture_a_clean_01.json").read_text())
    source = (ROOT / "original/fixture_a_clean_01.pdf").read_bytes()
    reference = source_pdf_text(source)
    observation = OcrDocument(
        source_sha256="0" * 64, engine_version="test", configuration=PreprocessingConfig(),
        pages=(OcrPage(page_number=1, text=reference, words=(), image_width=100,
                       image_height=100, deskew_angle_degrees=0),),
        normalized_text="  " + reference.replace(" ", " \n  ") + "  ",
    )
    result = evaluate_invoice(truth.case_id, observation, truth, source, 0.1)
    assert result.character_error_rate == result.word_error_rate == 0
    assert result.token_multiset_error_rate == 0


def test_aggregate_metrics_weight_by_reference_length_and_record_failures() -> None:
    success = InvoiceOcrEvaluation(
        case_id="a", status="ok", character_error_rate=0.1, word_error_rate=0.5,
        character_edits=1, reference_characters=10, word_edits=1, reference_words=2,
        token_multiset_missing=1, token_multiset_extra=0, observed_tokens=1,
        processing_seconds=0.2,
    )
    longer = InvoiceOcrEvaluation(
        case_id="c", status="ok", character_error_rate=0.9, word_error_rate=0.1,
        character_edits=90, reference_characters=100, word_edits=1, reference_words=10,
        token_multiset_missing=2, token_multiset_extra=1, observed_tokens=9,
        processing_seconds=0.4,
    )
    failure = InvoiceOcrEvaluation(case_id="b", status="error", error="OCR failed",
                                   processing_seconds=0.3)
    report = aggregate_evaluations((success, longer, failure))
    assert report.character_error_rate == 91 / 110
    assert report.word_error_rate == 2 / 12
    assert report.token_multiset_error_rate == 4 / 22
    assert report.failures == 1
    assert report.field_accuracy["invoice_number"] is None
