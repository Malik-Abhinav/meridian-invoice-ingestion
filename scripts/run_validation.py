"""Evaluate invoice-local validation on the nine stored offline extractions."""

import json
import argparse
from decimal import Decimal
from pathlib import Path

from meridian.extraction import ExtractionResult
from meridian.ocr import OcrDocument
from meridian.validation import ValidationPolicy, validate_invoice


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-floor", type=Decimal, default=Decimal("50"),
                        help="Minimum cited OCR word confidence (0–100; default 50)")
    args = parser.parse_args()
    policy = ValidationPolicy(minimum_ocr_confidence=args.ocr_floor)
    manifest = json.loads((ROOT / "output/evaluation/manifest.json").read_text())
    for entry in manifest["entries"]:
        case = entry["case_id"]
        extraction = ExtractionResult.model_validate_json(
            (ROOT / "output/extraction" / f"{case}.extraction.json").read_text())
        ocr = OcrDocument.model_validate_json(
            (ROOT / "output/ocr" / f"{case}.ocr.json").read_text())
        result = validate_invoice(extraction, ocr, policy)
        print(json.dumps({"case_id": case, "decision": result.decision.value,
                          "finding_codes": [finding.code.value for finding in result.findings]},
                         separators=(",", ":")))


if __name__ == "__main__":
    main()
