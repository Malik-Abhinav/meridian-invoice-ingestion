"""OCR contracts, preprocessing, failure paths, and real engine integration."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
from pydantic import ValidationError
import pytest
from reportlab.pdfgen import canvas

from meridian import ocr
from meridian.ocr import (
    EmptyScanError, InvalidDocumentError, OcrDocument, OcrPage, OcrWord,
    PreprocessingConfig, extract_ocr, normalize_text, preprocess_image,
)


def _white_pdf() -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _data(text: str) -> dict:
    return {
        "text": ["", text], "conf": ["-1", "93.2"],
        "left": [0, 10], "top": [0, 15], "width": [100, 45], "height": [100, 15],
        "block_num": [0, 1], "par_num": [0, 1], "line_num": [0, 1], "word_num": [0, 1],
    }


def test_configuration_and_word_contracts_reject_invalid_values() -> None:
    for kwargs in ({"dpi": 50}, {"contrast_factor": 0}, {"page_segmentation_mode": 2},
                   {"timeout_seconds": 0}, {"denoise": "yes"}):
        with pytest.raises(ValidationError):
            PreprocessingConfig(**kwargs)
    with pytest.raises(ValidationError):
        OcrWord(text="bad", confidence=101, left=0, top=0, width=1, height=1,
                block_number=1, paragraph_number=1, line_number=1, word_number=1)
    with pytest.raises(ValidationError, match="exceeds page image"):
        OcrPage(page_number=1, text="bad", image_width=20, image_height=20,
                deskew_angle_degrees=0, words=(OcrWord(
                    text="bad", confidence=50, left=19, top=0, width=2, height=1,
                    block_number=1, paragraph_number=1, line_number=1, word_number=1),))


def test_preprocessing_is_opt_in_and_does_not_mutate_source() -> None:
    image = Image.new("RGB", (200, 90), "white")
    ImageDraw.Draw(image).text((15, 15), "FICT INVOICE 100", fill="black")
    before = image.tobytes()
    default, angle = preprocess_image(image, PreprocessingConfig())
    assert default.mode == "L" and angle == 0
    assert image.tobytes() == before
    changed, _ = preprocess_image(image, PreprocessingConfig(
        contrast_factor=1.8, denoise=True, threshold=True))
    assert changed.mode == "L"
    assert ImageChops.difference(default, changed).getbbox() is not None
    color, _ = preprocess_image(image, PreprocessingConfig(grayscale=False))
    assert color.mode == "RGB"
    skewed, angle = preprocess_image(image, PreprocessingConfig(deskew=True))
    assert -3 <= angle <= 3
    assert skewed.width >= image.width and skewed.height >= image.height


def test_normalization_preserves_characters_and_is_idempotent() -> None:
    assert normalize_text("  A\tB\r\n  4.1250  ") == "A B 4.1250"
    assert normalize_text(normalize_text(" A  B ")) == "A B"
    assert normalize_text("O0") == "O0"


def test_word_order_uses_tesseract_line_identifiers() -> None:
    data = {
        "text": ["third", "first", "second"], "conf": [80, 90, 70],
        "left": [10, 10, 50], "top": [40, 10, 10],
        "width": [20, 20, 20], "height": [10, 10, 10],
        "block_num": [1, 1, 1], "par_num": [1, 1, 1],
        "line_num": [2, 1, 1], "word_num": [1, 1, 2],
    }
    words = ocr._observations(data, Image.new("L", (100, 100), "white"))
    assert [word.text for word in words] == ["first", "second", "third"]
    assert ocr._page_text(words) == "first second\nthird"
    assert [word.confidence for word in words] == [90, 70, 80]


def test_out_of_bounds_engine_box_is_reported() -> None:
    data = _data("word")
    data["left"][1] = 90
    with pytest.raises(ocr.OcrEngineError, match="out-of-bounds"):
        ocr._observations(data, Image.new("L", (100, 100), "white"))


def test_invalid_and_corrupt_pdfs_report_clear_errors() -> None:
    for value in (b"", b"not a pdf", b"%PDF-corrupt"):
        with pytest.raises(InvalidDocumentError):
            extract_ocr(value)


def test_empty_scan_and_engine_failure_are_distinct(monkeypatch) -> None:
    monkeypatch.setattr(ocr, "rasterize_pdf", lambda *_args, **_kwargs: (Image.new("RGB", (100, 100)),))
    monkeypatch.setattr(ocr.pytesseract, "get_tesseract_version", lambda: "5.0")
    monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda **_kwargs: ["eng"])
    monkeypatch.setattr(ocr.pytesseract, "image_to_data", lambda *_args, **_kwargs: _data(""))
    with pytest.raises(EmptyScanError):
        extract_ocr(_white_pdf())
    monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda **_kwargs: [])
    with pytest.raises(ocr.OcrEngineError, match="English"):
        extract_ocr(_white_pdf())
    with pytest.raises(TypeError, match="PreprocessingConfig"):
        extract_ocr(_white_pdf(), config="bad")


def test_multi_page_extraction_and_json_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(ocr, "rasterize_pdf", lambda *_args, **_kwargs: (
        Image.new("RGB", (100, 100)), Image.new("RGB", (100, 100))))
    monkeypatch.setattr(ocr.pytesseract, "get_tesseract_version", lambda: "5.0")
    monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda **_kwargs: ["eng"])
    values = iter((_data("PAGE_ONE"), _data("PAGE_TWO")))
    monkeypatch.setattr(ocr.pytesseract, "image_to_data", lambda *_args, **_kwargs: next(values))
    result = extract_ocr(_white_pdf())
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.normalized_text == "PAGE_ONE PAGE_TWO"
    assert result.engine_version == "5.0"
    assert OcrDocument.model_validate_json(result.model_dump_json()) == result


def test_real_tesseract_fixture() -> None:
    path = Path(__file__).resolve().parents[1] / "output/evaluation/scanned/fixture_a_clean_01.pdf"
    source = path.read_bytes()
    result = extract_ocr(source)
    assert result.source_sha256 == sha256(source).hexdigest()
    assert len(result.pages) == 1
    assert result.pages[0].words
    assert "Imaginary" in result.normalized_text
    assert result.engine_version
    assert all(0 <= word.confidence <= 100 for word in result.pages[0].words)
