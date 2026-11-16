"""Benchmark generic OCR settings against every case in an evaluation manifest."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from meridian.evaluation_fixtures import FixtureGroundTruth, FixtureManifest, FixtureScenario
from meridian.ocr import PreprocessingConfig, extract_ocr
from meridian.ocr_evaluation import aggregate_evaluations, evaluate_invoice


VARIANTS = {
    "baseline": {},
    "contrast_1_5": {"contrast_factor": 1.5},
    "psm_6": {"page_segmentation_mode": 6},
    "psm_11": {"page_segmentation_mode": 11},
    "contrast_1_5_psm_6": {"contrast_factor": 1.5, "page_segmentation_mode": 6},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("output/evaluation/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("output/ocr/benchmark.json"))
    args = parser.parse_args()
    manifest = FixtureManifest.model_validate_json(args.fixtures.read_text())
    root = args.fixtures.parent
    results = {}
    for label, settings in VARIANTS.items():
        cases = []
        for entry in manifest.entries:
            start = perf_counter()
            observation = extract_ocr((root / entry.scanned_pdf_path).read_bytes(),
                                      PreprocessingConfig(**settings))
            truth = FixtureGroundTruth.model_validate_json((root / entry.ground_truth_path).read_text())
            cases.append(evaluate_invoice(
                entry.case_id, observation, truth, (root / entry.original_pdf_path).read_bytes(),
                perf_counter() - start,
            ))
        report = aggregate_evaluations(tuple(cases))
        clean_ids = {entry.case_id for entry in manifest.entries if entry.scenario == FixtureScenario.CLEAN}
        results[label] = {
            "configuration": settings,
            "character_error_rate": report.character_error_rate,
            "word_error_rate": report.word_error_rate,
            "token_multiset_error_rate": report.token_multiset_error_rate,
            "field_hits": sum(value.recognized for case in cases for value in case.fields.values()),
            "field_expected": sum(value.expected for case in cases for value in case.fields.values()),
            "clean_field_hits": sum(value.recognized for case in cases if case.case_id in clean_ids
                                    for value in case.fields.values()),
            "clean_field_expected": sum(value.expected for case in cases if case.case_id in clean_ids
                                        for value in case.fields.values()),
            "per_case": {
                case.case_id: {
                    "cer": case.character_error_rate,
                    "wer": case.word_error_rate,
                    "token_multiset_error_rate": case.token_multiset_error_rate,
                    "field_hits": sum(value.recognized for value in case.fields.values()),
                    "field_expected": sum(value.expected for value in case.fields.values()),
                    "seconds": case.processing_seconds,
                } for case in cases
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "method": "same manifest, English Tesseract, 200 DPI; one setting applied to every case",
        "results": results,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
