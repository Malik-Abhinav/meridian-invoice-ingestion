"""Independent arithmetic audit of persisted corpus evaluation rows."""

from collections import Counter


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _check_close(actual: float | None, expected: float | None, name: str) -> None:
    if actual is None or expected is None:
        if actual != expected:
            raise ValueError(f"{name} denominator mismatch")
    elif abs(actual - expected) > 1e-12:
        raise ValueError(f"{name} arithmetic mismatch")


def _finding_counts(rows: list[dict], expected_key: str, observed_key: str) -> tuple[int, int, int]:
    expected = {(row["case_id"], code) for row in rows for code in row[expected_key]}
    observed = {(row["case_id"], code) for row in rows for code in row[observed_key]}
    return len(expected & observed), len(observed - expected), len(expected - observed)


def audit_report(report: dict) -> dict:
    """Recompute published numerators and denominators without importing scorer code."""
    for mode in ("controlled", "ocr_mock"):
        rows = report[mode]["rows"]
        for row in rows:
            expected, observed = set(row["expected_findings"]), set(row["observed_findings"])
            if set(row["false_positives"]) != observed - expected or set(row["false_negatives"]) != expected - observed:
                raise ValueError(f"incorrect per-case findings: {mode}/{row['case_id']}")
            if observed != set(row["validation_findings"]) | set(row["reconciliation_findings"]):
                raise ValueError(f"finding source split differs: {mode}/{row['case_id']}")
            if expected != set(row["expected_validation_findings"]) | set(row["expected_reconciliation_findings"]):
                raise ValueError(f"expected source split differs: {mode}/{row['case_id']}")
            if row["review_required"] != (row["reconciliation_decision"] != "ready_for_posting_review"):
                raise ValueError(f"review decision differs: {mode}/{row['case_id']}")
        for group_name, group in report[mode]["groups"].items():
            if group_name == "overall":
                subset = rows
            else:
                kind, value = group_name.split(":", 1)
                column = {"category": "category", "supplier": "supplier", "scan_profile": "scan_profile"}[kind]
                subset = [row for row in rows if row[column] == value]
            if len(subset) != group["cases"]:
                raise ValueError(f"case count differs: {mode}/{group_name}")
            for metric, expected_key, observed_key in (
                ("findings", "expected_findings", "observed_findings"),
                ("validation_findings", "expected_validation_findings", "validation_findings"),
                ("reconciliation_findings", "expected_reconciliation_findings", "reconciliation_findings"),
            ):
                counts = _finding_counts(subset, expected_key, observed_key)
                score = group[metric]
                if counts != (score["true_positives"], score["false_positives"], score["false_negatives"]):
                    raise ValueError(f"finding counts differ: {mode}/{group_name}/{metric}")
                tp, fp, fn = counts
                precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
                f1 = (None if precision is None or recall is None else
                      0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
                for name, value in (("precision", precision), ("recall", recall), ("f1", f1)):
                    _check_close(score[name], value, f"{mode}/{group_name}/{metric}/{name}")
            tp = sum(row["expected_review"] and row["review_required"] for row in subset)
            fp = sum(not row["expected_review"] and row["review_required"] for row in subset)
            fn = sum(row["expected_review"] and not row["review_required"] for row in subset)
            tn = len(subset) - tp - fp - fn
            review = group["review_decisions"]
            if (tp, fp, fn, tn) != tuple(review[key] for key in
                    ("true_positives", "false_positives", "false_negatives", "true_negatives")):
                raise ValueError(f"review confusion differs: {mode}/{group_name}")
            precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
            f1 = (None if precision is None or recall is None else
                  0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
            for name, value in (("precision", precision), ("recall", recall), ("f1", f1)):
                _check_close(review[name], value, f"{mode}/{group_name}/review/{name}")
            if group["unperformed_check_count"] != sum(len(row["unperformed_checks"]) for row in subset):
                raise ValueError(f"skipped checks differ: {mode}/{group_name}")
        defect = report["primary_defects"][mode]
        if defect != {"expected_cases": sum(bool(row["expected_findings"]) for row in rows),
                      "detected_cases": sum(bool(set(row["expected_findings"]) & set(row["observed_findings"]))
                                            for row in rows if row["expected_findings"])}:
            raise ValueError(f"primary-defect counts differ: {mode}")
        for code, count in report["duplicate_detection"][mode].items():
            actual = sum(code in row["observed_findings"] for row in rows
                         if row["category"] == "duplicate_invoice")
            if count != actual:
                raise ValueError(f"duplicate count differs: {mode}/{code}")
    pipeline = report["ocr_mock"]
    if not pipeline["rows"]:
        return {"cases": len(report["controlled"]["rows"]),
                "finding_pairs": report["controlled"]["groups"]["overall"]["findings"],
                "ocr_reference_characters": 0, "ocr_reference_words": 0,
                "ocr_token_denominator": 0}
    if pipeline["processing_failures"] != sum("failure" in row for row in pipeline["rows"]):
        raise ValueError("pipeline failure count differs")
    ocr = pipeline["ocr"]
    successful = ocr["invoices"] if ocr is not None else []
    if len(successful) + len(pipeline["ocr_failures"]) != len(pipeline["rows"]):
        raise ValueError("OCR case denominator differs")
    if ocr is None:
        ocr = {"invoices": [], "failures": len(pipeline["ocr_failures"]),
               "character_error_rate": None, "word_error_rate": None,
               "token_multiset_error_rate": None, "field_accuracy": {}}
    if ocr["failures"] != len(pipeline["ocr_failures"]):
        raise ValueError("OCR failure count differs")
    chars = sum(row["reference_characters"] for row in successful)
    words = sum(row["reference_words"] for row in successful)
    tokens = words + sum(row["observed_tokens"] for row in successful)
    for name, numerator, denominator in (
        ("character_error_rate", sum(row["character_edits"] for row in successful), chars),
        ("word_error_rate", sum(row["word_edits"] for row in successful), words),
        ("token_multiset_error_rate", sum(row["token_multiset_missing"] + row["token_multiset_extra"]
                                          for row in successful), tokens),
    ):
        _check_close(ocr[name], _ratio(numerator, denominator), name)
    field_totals: dict[str, Counter] = {}
    for row in successful:
        for name, field in row["fields"].items():
            field_totals.setdefault(name, Counter()).update({"hit": field["recognized"],
                                                               "expected": field["expected"]})
    for name, totals in field_totals.items():
        _check_close(ocr["field_accuracy"][name], _ratio(totals["hit"], totals["expected"]),
                     f"OCR field {name}")
    for group_name, group in report["ocr_groups"].items():
        case_ids = {row["case_id"] for row in pipeline["rows"] if group_name == "overall" or
                    (group_name.split(":", 1)[0] in ("category", "supplier", "scan_profile") and
                     row[group_name.split(":", 1)[0]] == group_name.split(":", 1)[1])}
        invoices = [row for row in successful if row["case_id"] in case_ids]
        if {row["case_id"] for row in group["invoices"]} != {row["case_id"] for row in invoices}:
            raise ValueError(f"OCR group cases differ: {group_name}")
        if group_name == "overall":
            failures = len(pipeline["ocr_failures"])
        else:
            kind, value = group_name.split(":", 1)
            failures = sum(next(row[kind] for row in pipeline["rows"]
                                if row["case_id"] == failure["case_id"]) == value
                           for failure in pipeline["ocr_failures"])
        if group["failures"] != failures:
            raise ValueError(f"OCR group failure count differs: {group_name}")
        for name, edits, denominator in (
            ("character_error_rate", sum(row["character_edits"] for row in invoices),
             sum(row["reference_characters"] for row in invoices)),
            ("word_error_rate", sum(row["word_edits"] for row in invoices),
             sum(row["reference_words"] for row in invoices)),
            ("token_multiset_error_rate", sum(row["token_multiset_missing"] + row["token_multiset_extra"]
                                             for row in invoices),
             sum(row["reference_words"] + row["observed_tokens"] for row in invoices)),
        ):
            _check_close(group[name], _ratio(edits, denominator), f"OCR group {group_name}/{name}")
        group_totals: dict[str, Counter] = {}
        for row in invoices:
            for name, field in row["fields"].items():
                group_totals.setdefault(name, Counter()).update({"hit": field["recognized"],
                                                                   "expected": field["expected"]})
        for name, totals in group_totals.items():
            _check_close(group["field_accuracy"][name], _ratio(totals["hit"], totals["expected"]),
                         f"OCR group {group_name}/{name}")
    extraction = pipeline["extraction"]
    for name, totals in extraction["fields"].items():
        summed = Counter()
        for row in extraction["invoices"]:
            summed.update(row["fields"][name])
        if dict(summed) != totals:
            raise ValueError(f"extraction denominator differs: {name}")
    for group_name, group in report["extraction_groups"].items():
        case_ids = {row["case_id"] for row in pipeline["rows"] if group_name == "overall" or
                    row[group_name.split(":", 1)[0]] == group_name.split(":", 1)[1]}
        invoices = [row for row in extraction["invoices"] if row["case_id"] in case_ids]
        if {row["case_id"] for row in group["invoices"]} != {row["case_id"] for row in invoices}:
            raise ValueError(f"extraction group cases differ: {group_name}")
        for name, totals in group["fields"].items():
            summed = Counter()
            for row in invoices:
                summed.update(row["fields"][name])
            if dict(summed) != totals:
                raise ValueError(f"extraction group denominator differs: {group_name}/{name}")
    return {"cases": len(report["controlled"]["rows"]),
            "finding_pairs": report["controlled"]["groups"]["overall"]["findings"],
            "ocr_reference_characters": chars, "ocr_reference_words": words,
            "ocr_token_denominator": tokens}
