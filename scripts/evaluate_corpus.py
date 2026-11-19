"""Score the isolated corpus without touching historical reports."""

import argparse
from pathlib import Path
from time import perf_counter

from meridian.corpus_evaluation import evaluate_corpus, write_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("output/corpus"))
    parser.add_argument("--controlled-only", action="store_true")
    args = parser.parse_args()
    started = perf_counter()
    report = evaluate_corpus(args.corpus, run_ocr=not args.controlled_only)
    write_report(args.corpus, report)
    print(f"controlled={report['controlled']['groups']['overall']}")
    print(f"ocr_mock={report['ocr_mock']['groups'].get('overall')}")
    print(f"elapsed_seconds={perf_counter() - started:.2f}")


if __name__ == "__main__":
    main()
