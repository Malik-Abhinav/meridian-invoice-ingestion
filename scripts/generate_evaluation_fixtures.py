"""Generate the nine reproducible Task 9 evaluation fixtures."""

from pathlib import Path

from meridian.evaluation_fixtures import generate_fixture_set


OUTPUT = Path(__file__).resolve().parents[1] / "output" / "evaluation"


if __name__ == "__main__":
    generate_fixture_set(OUTPUT)
