"""Generate or validate the isolated Task 17 corpus."""

import argparse
from pathlib import Path

from meridian.corpus import generate_corpus, validate_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/corpus"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    manifest = validate_manifest(args.output) if args.validate_only else generate_corpus(args.output)
    print(f"{manifest.case_count} cases; {manifest.category_counts}; {manifest.supplier_counts}")


if __name__ == "__main__":
    main()
