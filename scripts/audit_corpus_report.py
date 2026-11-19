"""Independently recompute every published corpus aggregate from per-case JSON."""

import json
from pathlib import Path

from meridian.corpus_report_audit import audit_report


if __name__ == "__main__":
    path = Path("output/corpus/reports/metrics.json")
    print(json.dumps(audit_report(json.loads(path.read_text())), sort_keys=True))
