"""Extract one OCR JSON document or evaluate the nine existing fixtures offline."""

import argparse
import json
from pathlib import Path

from meridian.evaluation_fixtures import FixtureGroundTruth, FixtureManifest
from meridian.extraction import ExtractionError, extract_invoice
from meridian.extraction_evaluation import aggregate_extractions, evaluate_extraction
from meridian.extraction_providers import MockProvider, OpenAIProvider
from meridian.ocr import OcrDocument


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="one OCR JSON file")
    source.add_argument("--fixtures", type=Path, help="evaluation manifest")
    parser.add_argument("--output", type=Path, help="single extraction JSON path")
    parser.add_argument("--output-dir", type=Path, default=Path("output/extraction"))
    parser.add_argument("--ocr-dir", type=Path, default=Path("output/ocr"))
    parser.add_argument("--provider", choices=("mock", "openai"), default="mock")
    parser.add_argument("--model", help="OpenAI model name; defaults to OPENAI_MODEL or gpt-4o-mini")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args(argv)
    if args.input and not args.output:
        parser.error("--input requires --output")
    provider = (MockProvider() if args.provider == "mock" else
                OpenAIProvider(model_name=args.model, timeout_seconds=args.timeout))

    def run(path: Path):
        document = OcrDocument.model_validate_json(path.read_text(encoding="utf-8"))
        return extract_invoice(document, provider, max_retries=args.retries)

    try:
        if args.input:
            result = run(args.input)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
            print(f"Extracted {args.input.name} with {provider.name}")
            return 0
        manifest = FixtureManifest.model_validate_json(args.fixtures.read_text(encoding="utf-8"))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        cases, failures = [], {}
        for entry in manifest.entries:
            try:
                result = run(args.ocr_dir / f"{entry.case_id}.ocr.json")
                (args.output_dir / f"{entry.case_id}.extraction.json").write_text(
                    result.model_dump_json(indent=2) + "\n", encoding="utf-8")
                # Ground truth enters evaluation only after the provider has returned.
                truth = FixtureGroundTruth.model_validate_json(
                    (args.fixtures.parent / entry.ground_truth_path).read_text(encoding="utf-8"))
                cases.append(evaluate_extraction(result.invoice, truth))
            except (ExtractionError, OSError, ValueError) as exc:
                failures[entry.case_id] = f"{type(exc).__name__}: {exc}"
        report = aggregate_extractions(tuple(cases))
        (args.output_dir / "report.json").write_text(json.dumps({
            "evaluation": report.model_dump(mode="json"), "failures": failures,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"Extraction: {len(cases)} cases, {len(failures)} failures")
        return 1 if failures else 0
    except (ExtractionError, OSError, ValueError) as exc:
        parser.exit(1, f"Extraction failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
