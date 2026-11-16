"""Run controlled-visible and stored-extraction reconciliation for nine fixtures."""

import json
from pathlib import Path

from meridian.evaluation_fixtures import FixtureGroundTruth
from meridian.extraction import ExtractionResult
from meridian.ocr import OcrDocument
from meridian.reconciliation import reconcile_invoice
from meridian.reconciliation_evaluation import controlled_extraction, fixture_repository
from meridian.validation import validate_invoice


ROOT = Path(__file__).resolve().parents[1]


def evaluate(mode: str) -> list[dict]:
    manifest = json.loads((ROOT / "output/evaluation/manifest.json").read_text())
    truths = tuple(FixtureGroundTruth.model_validate_json(
        (ROOT / "output/evaluation/ground_truth" / f"{entry['case_id']}.json").read_text())
        for entry in manifest["entries"])
    rows = []
    for truth in truths:
        case = truth.case_id
        if mode == "controlled":
            extraction, ocr = controlled_extraction(truth)
        else:
            extraction = ExtractionResult.model_validate_json(
                (ROOT / "output/extraction" / f"{case}.extraction.json").read_text())
            ocr = OcrDocument.model_validate_json(
                (ROOT / "output/ocr" / f"{case}.ocr.json").read_text())
        validation = validate_invoice(extraction, ocr)
        result = reconcile_invoice(extraction, validation, fixture_repository(truth, truths))
        rows.append({"mode": mode, "case_id": case, "validation_decision": validation.decision.value,
                     "validation_findings": [f.code.value for f in validation.findings],
                     "decision": result.decision.value,
                     "finding_codes": [f.code.value for f in result.findings],
                     "unperformed_checks": list(result.unperformed_checks)})
    return rows


def main() -> None:
    for mode in ("controlled", "stored"):
        for row in evaluate(mode):
            print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    main()
