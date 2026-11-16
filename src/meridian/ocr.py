"""Page-wise OCR observations from scanned invoices; no invoice interpretation."""

from hashlib import sha256
import re

from PIL import Image, ImageEnhance, ImageFilter
from pydantic import Field, ValidationError, model_validator
import pytesseract
from pytesseract import Output, TesseractError, TesseractNotFoundError

from meridian.scanned_invoices import rasterize_pdf
from meridian.synthetic import SyntheticModel


class OcrError(Exception):
    """Base error for an OCR request."""


class InvalidDocumentError(OcrError):
    """Input is not a readable PDF."""


class EmptyScanError(OcrError):
    """No words were found in the scanned document."""


class OcrEngineError(OcrError):
    """Tesseract is unavailable or failed."""


class PreprocessingConfig(SyntheticModel):
    dpi: int = Field(default=200, ge=72, le=300)
    grayscale: bool = True
    contrast_factor: float = Field(default=1.0, gt=0, le=3)
    denoise: bool = False
    threshold: bool = False
    deskew: bool = False
    page_segmentation_mode: int = Field(default=3, ge=3, le=13)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class OcrWord(SyntheticModel):
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=100)
    left: int = Field(ge=0)
    top: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    block_number: int = Field(ge=0)
    paragraph_number: int = Field(ge=0)
    line_number: int = Field(ge=0)
    word_number: int = Field(ge=0)


class OcrPage(SyntheticModel):
    page_number: int = Field(gt=0)
    text: str
    words: tuple[OcrWord, ...]
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    deskew_angle_degrees: float

    @model_validator(mode="after")
    def boxes_fit_image(self):
        if any(word.left + word.width > self.image_width or
               word.top + word.height > self.image_height for word in self.words):
            raise ValueError("OCR word bounding box exceeds page image")
        return self


class OcrDocument(SyntheticModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine: str = "tesseract"
    engine_version: str
    language: str = "eng"
    configuration: PreprocessingConfig
    pages: tuple[OcrPage, ...] = Field(min_length=1)
    normalized_text: str


def normalize_text(text: str) -> str:
    """Collapse whitespace only; leave recognized characters untouched."""
    return re.sub(r"\s+", " ", text).strip()


def _deskew_angle(image: Image.Image) -> float:
    """Estimate small skew by maximizing horizontal dark-pixel alignment."""
    sample = image.convert("L")
    sample.thumbnail((700, 700))
    binary = sample.point(lambda value: 0 if value < 170 else 255)
    best_angle, best_score = 0.0, -1
    for step in range(-6, 7):
        angle = step * 0.5
        rotated = binary.rotate(angle, expand=True, fillcolor=255)
        pixels = rotated.load()
        score = 0
        for y in range(rotated.height):
            count = sum(pixels[x, y] == 0 for x in range(rotated.width))
            score += count * count
        if score > best_score or score == best_score and abs(angle) < abs(best_angle):
            best_angle, best_score = angle, score
    return best_angle


def preprocess_image(image: Image.Image, config: PreprocessingConfig) -> tuple[Image.Image, float]:
    """Return a detached image and applied angle; transformations are opt-in."""
    result = image.convert("L" if config.grayscale else "RGB")
    if config.contrast_factor != 1.0:
        result = ImageEnhance.Contrast(result).enhance(config.contrast_factor)
    if config.denoise:
        result = result.filter(ImageFilter.MedianFilter(size=3))
    if config.threshold:
        result = result.convert("L").point(lambda value: 255 if value >= 170 else 0)
    angle = _deskew_angle(result) if config.deskew else 0.0
    if angle:
        result = result.rotate(angle, resample=Image.Resampling.BICUBIC,
                               expand=True, fillcolor=255 if result.mode == "L" else (255, 255, 255))
    return result, angle


def _page_text(words: tuple[OcrWord, ...]) -> str:
    lines: list[list[str]] = []
    previous: tuple[int, int, int] | None = None
    for word in words:
        key = (word.block_number, word.paragraph_number, word.line_number)
        if key != previous:
            lines.append([])
            previous = key
        lines[-1].append(word.text)
    return "\n".join(" ".join(line) for line in lines)


def _observations(data: dict, image: Image.Image) -> tuple[OcrWord, ...]:
    words = []
    for index, raw_text in enumerate(data["text"]):
        value = str(raw_text).strip()
        if not value:
            continue
        confidence = float(data["conf"][index])
        if confidence < 0:
            continue
        word = OcrWord(
            text=value, confidence=confidence,
            left=int(data["left"][index]), top=int(data["top"][index]),
            width=int(data["width"][index]), height=int(data["height"][index]),
            block_number=int(data["block_num"][index]),
            paragraph_number=int(data["par_num"][index]),
            line_number=int(data["line_num"][index]),
            word_number=int(data["word_num"][index]),
        )
        if word.left + word.width > image.width or word.top + word.height > image.height:
            raise OcrEngineError(f"Tesseract returned an out-of-bounds word at row {index}")
        words.append(word)
    return tuple(sorted(words, key=lambda w: (w.block_number, w.paragraph_number,
                                              w.line_number, w.word_number)))


def extract_ocr(pdf_bytes: bytes, config: PreprocessingConfig | None = None) -> OcrDocument:
    """Rasterize all pages and return faithful Tesseract words and line text."""
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
        raise InvalidDocumentError("input must be nonempty PDF bytes")
    if config is not None and not isinstance(config, PreprocessingConfig):
        raise TypeError("config must be PreprocessingConfig")
    config = config or PreprocessingConfig()
    try:
        images = rasterize_pdf(pdf_bytes, dpi=config.dpi)
    except Exception as exc:
        raise InvalidDocumentError(f"cannot rasterize PDF: {exc}") from exc
    try:
        version = str(pytesseract.get_tesseract_version())
        if "eng" not in pytesseract.get_languages(config=""):
            raise OcrEngineError("English Tesseract language data is unavailable")
        pages = []
        for number, image in enumerate(images, 1):
            processed, angle = preprocess_image(image, config)
            data = pytesseract.image_to_data(
                processed, lang="eng", config=f"--psm {config.page_segmentation_mode}",
                output_type=Output.DICT, timeout=config.timeout_seconds,
            )
            words = _observations(data, processed)
            pages.append(OcrPage(page_number=number, text=_page_text(words), words=words,
                                 image_width=processed.width, image_height=processed.height,
                                 deskew_angle_degrees=angle))
    except OcrError:
        raise
    except (TesseractError, TesseractNotFoundError, RuntimeError, OSError,
            KeyError, ValueError, ValidationError) as exc:
        raise OcrEngineError(str(exc)) from exc
    if not any(page.words for page in pages):
        raise EmptyScanError("no words recognized on any page")
    return OcrDocument(source_sha256=sha256(pdf_bytes).hexdigest(),
                       engine_version=version, configuration=config, pages=tuple(pages),
                       normalized_text=normalize_text("\n\n".join(page.text for page in pages)))
