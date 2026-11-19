"""Generate twice in isolated directories and compare every artifact byte."""

from hashlib import sha256
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from meridian.corpus import generate_corpus, validate_manifest
from meridian.corpus_evaluation import evaluate_corpus, write_report


def hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluate", action="store_true", help="also compare OCR/mock evaluation report bytes")
    args = parser.parse_args()
    with TemporaryDirectory() as temporary:
        first = Path(temporary) / "first" / "corpus"
        second = Path(temporary) / "second" / "corpus"
        generate_corpus(first)
        generate_corpus(second)
        validate_manifest(first)
        validate_manifest(second)
        left, right = hashes(first), hashes(second)
        if left != right:
            raise SystemExit("generation was not byte-identical")
        print(f"byte-identical artifacts={len(left)} aggregate_sha256=" + sha256(
            "\n".join(f"{key} {value}" for key, value in sorted(left.items())).encode()).hexdigest())
        if args.evaluate:
            write_report(first, evaluate_corpus(first))
            write_report(second, evaluate_corpus(second))
            for name in ("metrics.json", "analysis.md"):
                if (first / "reports" / name).read_bytes() != (second / "reports" / name).read_bytes():
                    raise SystemExit(f"evaluation report differed: {name}")
            print("byte-identical reports=metrics.json,analysis.md " + " ".join(
                f"{name}:{sha256((first / 'reports' / name).read_bytes()).hexdigest()}"
                for name in ("metrics.json", "analysis.md")))


if __name__ == "__main__":
    main()
