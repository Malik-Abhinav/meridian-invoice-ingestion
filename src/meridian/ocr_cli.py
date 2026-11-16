"""Command-line OCR for one scan or all nine evaluation fixtures."""

import argparse
from pathlib import Path
from time import perf_counter

from meridian.evaluation_fixtures import FixtureGroundTruth, FixtureManifest
from meridian.ocr import OcrError, PreprocessingConfig, extract_ocr
from meridian.ocr_evaluation import InvoiceOcrEvaluation, aggregate_evaluations, evaluate_invoice


def _write_observation(path: Path, source: Path, config: PreprocessingConfig):
    observation = extract_ocr(source.read_bytes(), config)
    (path / f"{source.stem}.ocr.json").write_text(
        observation.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (path / f"{source.stem}.txt").write_text(
        "\n\n".join(page.text for page in observation.pages).rstrip() + "\n", encoding="utf-8")
    return observation


def run_fixtures(manifest_path: Path, output_dir: Path, config: PreprocessingConfig):
    manifest = FixtureManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluations = []
    for entry in manifest.entries:
        start = perf_counter()
        try:
            scan = root / entry.scanned_pdf_path
            observation = _write_observation(output_dir, scan, config)
            truth = FixtureGroundTruth.model_validate_json(
                (root / entry.ground_truth_path).read_text(encoding="utf-8"))
            result = evaluate_invoice(entry.case_id, observation, truth,
                                      (root / entry.original_pdf_path).read_bytes(),
                                      perf_counter() - start)
        except Exception as exc:
            result = InvoiceOcrEvaluation(
                case_id=entry.case_id, status="error", processing_seconds=perf_counter() - start,
                error=f"{type(exc).__name__}: {exc}",
            )
        (output_dir / f"{entry.case_id}.evaluation.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        evaluations.append(result)
    report = aggregate_evaluations(tuple(evaluations))
    (output_dir / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="one scanned PDF")
    source.add_argument("--fixtures", type=Path, help="Task 9 manifest.json")
    parser.add_argument("--output-dir", type=Path, default=Path("output/ocr"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--contrast", type=float, default=1.0)
    parser.add_argument("--denoise", action="store_true")
    parser.add_argument("--threshold", action="store_true")
    parser.add_argument("--deskew", action="store_true")
    parser.add_argument("--no-grayscale", action="store_true")
    parser.add_argument("--psm", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        config = PreprocessingConfig(
            dpi=args.dpi, grayscale=not args.no_grayscale,
            contrast_factor=args.contrast, denoise=args.denoise,
            threshold=args.threshold, deskew=args.deskew,
            page_segmentation_mode=args.psm,
        )
        if args.input:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            result = _write_observation(args.output_dir, args.input, config)
            print(f"OCR: {len(result.pages)} page(s), {len(result.normalized_text)} characters")
            return 0
        report = run_fixtures(args.fixtures, args.output_dir, config)
        print(f"OCR: {len(report.invoices)} cases, {report.failures} failures; "
              f"CER={report.character_error_rate}, WER={report.word_error_rate}")
        return 1 if report.failures else 0
    except (OcrError, OSError, ValueError) as exc:
        parser.exit(1, f"OCR failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
