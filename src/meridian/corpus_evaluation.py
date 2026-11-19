"""Offline Task 17 scoring. Evaluation truth never enters OCR or the mock provider."""

from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

import pytesseract

from meridian.corpus import CorpusTruth, validate_manifest
from meridian.extraction import (
    CountValue, DateValue, ExtractedInvoice, ExtractedLine, ExtractionResult,
    MoneyValue, TextValue, extract_invoice,
)
from meridian.extraction_evaluation import aggregate_extractions, evaluate_extraction
from meridian.extraction_providers import MockProvider
from meridian.ocr import extract_ocr
from meridian.ocr_evaluation import aggregate_evaluations, evaluate_invoice
from meridian.reconciliation import reconcile_invoice
from meridian.synthetic import SyntheticInvoiceInput
from meridian.validation import (
    FieldReference, FindingCode, ReviewDecision, Severity, ValidationFinding,
    ValidationResult, validate_invoice,
)


def controlled_extraction(invoice: SyntheticInvoiceInput, source_hash: str) -> ExtractionResult:
    """Invoice-visible transcription with no invented OCR words or confidence."""
    products = {p.sku: p for p in invoice.products}
    text = lambda value: TextValue(value=value)
    money = lambda value: MoneyValue(value=value)
    lines = tuple(ExtractedLine(
        source_row_order=i, line_number=CountValue(value=line.line_number),
        description=text(products[line.product_sku].description),
        upc=text(products[line.product_sku].upc), ndc=text(products[line.product_sku].ndc),
        quantity=CountValue(value=line.quantity), unit_cost=money(line.unit_cost),
        printed_line_total=money(line.line_total),
    ) for i, line in enumerate(invoice.lines, 1))
    totals = invoice.displayed_totals
    return ExtractionResult(
        source_sha256=source_hash, provider="controlled-transcription", model_name="none",
        repair_attempted=False,
        invoice=ExtractedInvoice(
            supplier_name=text(invoice.supplier.name), invoice_number=text(invoice.invoice_number),
            invoice_date=DateValue(value=invoice.invoice_date),
            purchase_order_reference=text(invoice.purchase_order_reference), lines=lines,
            printed_subtotal=money(totals.subtotal if totals else None),
            printed_tax=money(totals.tax if totals else None),
            printed_total=money(totals.total if totals else None),
        ),
    )


def controlled_validation(extraction: ExtractionResult) -> ValidationResult:
    """Invoice-local arithmetic oracle for controlled transcription, without OCR claims."""
    invoice = extraction.invoice
    findings = []

    def add(code: FindingCode, field: str, row: int | None = None) -> None:
        findings.append(ValidationFinding(code=code, severity=Severity.ERROR,
                                          field=FieldReference(field=field, source_row_order=row),
                                          explanation="Controlled invoice-visible rule check."))

    for name, code in (("supplier_name", FindingCode.MISSING_SUPPLIER),
                       ("invoice_number", FindingCode.MISSING_INVOICE_NUMBER),
                       ("invoice_date", FindingCode.MISSING_INVOICE_DATE),
                       ("purchase_order_reference", FindingCode.MISSING_PO_REFERENCE)):
        if getattr(invoice, name).value is None:
            add(code, name)
    if not invoice.lines:
        add(FindingCode.MISSING_LINES, "lines")
    for line in invoice.lines:
        row = line.source_row_order
        if line.upc.value is None and line.ndc.value is None:
            add(FindingCode.MISSING_PRODUCT_IDENTIFIER, "product_identifier", row)
        if line.quantity.value is None:
            add(FindingCode.MISSING_QUANTITY, "quantity", row)
        elif line.quantity.value <= 0:
            add(FindingCode.NONPOSITIVE_QUANTITY, "quantity", row)
        if line.unit_cost.value is None:
            add(FindingCode.MISSING_UNIT_COST, "unit_cost", row)
        if line.printed_line_total.value is None:
            add(FindingCode.MISSING_LINE_TOTAL, "printed_line_total", row)
        if all(value is not None for value in (line.quantity.value, line.unit_cost.value,
                                               line.printed_line_total.value)) and (
                line.quantity.value * line.unit_cost.value != line.printed_line_total.value):
            add(FindingCode.LINE_EXTENSION_MISMATCH, "printed_line_total", row)
    for name, code in (("printed_subtotal", FindingCode.MISSING_SUBTOTAL),
                       ("printed_tax", FindingCode.MISSING_TAX),
                       ("printed_total", FindingCode.MISSING_TOTAL)):
        if getattr(invoice, name).value is None:
            add(code, name)
    subtotal = invoice.printed_subtotal.value
    if subtotal is not None and all(line.printed_line_total.value is not None for line in invoice.lines):
        if subtotal != sum((line.printed_line_total.value for line in invoice.lines), Decimal(0)):
            add(FindingCode.SUBTOTAL_MISMATCH, "printed_subtotal")
    if all(value is not None for value in (subtotal, invoice.printed_tax.value, invoice.printed_total.value)):
        if subtotal + invoice.printed_tax.value != invoice.printed_total.value:
            add(FindingCode.TOTAL_MISMATCH, "printed_total")
    return ValidationResult(source_sha256=extraction.source_sha256,
                            findings=tuple(findings), confidence=(),
                            decision=(ReviewDecision.NEEDS_REVIEW if findings
                                      else ReviewDecision.READY_FOR_PO_MATCHING))


def _empty_invoice() -> ExtractedInvoice:
    return ExtractedInvoice(supplier_name=TextValue(), invoice_number=TextValue(),
                            invoice_date=DateValue(), purchase_order_reference=TextValue(),
                            lines=(), printed_subtotal=MoneyValue(), printed_tax=MoneyValue(),
                            printed_total=MoneyValue())


def _observe_scanned(scanned: bytes):
    """OCR/mock boundary has no truth argument or truth-bearing local state."""
    return extract_ocr(scanned)


def _extract_mock(observation):
    """The mock sees only OCR observations, never evaluation labels."""
    return extract_invoice(observation, MockProvider())


def _classification(expected: set[str], observed: set[str]) -> dict:
    tp = len(expected & observed)
    fp = len(observed - expected)
    fn = len(expected - observed)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (None if precision is None or recall is None else
          0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": precision, "recall": recall, "f1": f1}


def _aggregate_rows(rows: list[dict], *, mode: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for key in ("overall", f"category:{row['category']}", f"supplier:{row['supplier']}",
                    f"scan_profile:{row['scan_profile']}"):
            groups[key].append(row)
    result = {}
    for name, items in sorted(groups.items()):
        expected = {(r["case_id"], code) for r in items for code in r["expected_findings"]}
        observed = {(r["case_id"], code) for r in items for code in r["observed_findings"]}
        expected_local = {(r["case_id"], code) for r in items for code in r["expected_validation_findings"]}
        observed_local = {(r["case_id"], code) for r in items for code in r["validation_findings"]}
        expected_external = {(r["case_id"], code) for r in items for code in r["expected_reconciliation_findings"]}
        observed_external = {(r["case_id"], code) for r in items for code in r["reconciliation_findings"]}
        expected_review = {r["case_id"] for r in items if r["expected_review"]}
        observed_review = {r["case_id"] for r in items if r["review_required"]}
        reviews = _classification(expected_review, observed_review)
        reviews["true_negatives"] = len(items) - reviews["true_positives"] - reviews["false_positives"] - reviews["false_negatives"]
        result[name] = {"cases": len(items), "findings": _classification(expected, observed),
                        "validation_findings": _classification(expected_local, observed_local),
                        "reconciliation_findings": _classification(expected_external, observed_external),
                        "review_decisions": reviews,
                        "unperformed_check_count": sum(len(r["unperformed_checks"]) for r in items)}
    return result


def evaluate_corpus(root: Path, *, run_ocr: bool = True) -> dict:
    root = Path(root)
    manifest = validate_manifest(root)
    controlled_rows = []
    pipeline_rows = []
    ocr_scores = []
    extraction_scores = []
    ocr_failures = []
    score_groups: dict[str, dict[str, list]] = defaultdict(lambda: {"ocr": [], "extraction": []})
    for entry in manifest.entries:
        invoice = SyntheticInvoiceInput.model_validate_json((root / entry.input_path).read_text())
        source = (root / entry.original_pdf_path).read_bytes()
        scanned = (root / entry.scanned_pdf_path).read_bytes()
        source_hash = sha256(scanned).hexdigest()
        # Neither OCR nor the mock provider receives truth. The helper also
        # prevents a prior loop iteration's truth local from entering their frame.
        preparation_error = None
        ocr_score = None
        if run_ocr:
            ocr_started = perf_counter()
            try:
                observation = _observe_scanned(scanned)
            except Exception as exc:
                preparation_error = exc
                ocr_failures.append({"case_id": entry.case_id,
                                     "error": f"{type(exc).__name__}: {exc}"})
            if preparation_error is None:
                try:
                    ocr_score = evaluate_invoice(entry.case_id, observation,
                                                 SimpleNamespace(case_id=entry.case_id,
                                                                 displayed_invoice=invoice),
                                                 source, perf_counter() - ocr_started)
                    ocr_scores.append(ocr_score)
                    for group in ("overall", f"category:{entry.category.value}",
                                  f"supplier:{entry.supplier_code}", f"scan_profile:{entry.scan_profile.value}"):
                        score_groups[group]["ocr"].append(ocr_score)
                except Exception as exc:
                    ocr_failures.append({"case_id": entry.case_id,
                                         "error": f"{type(exc).__name__}: {exc}"})
                try:
                    extraction = _extract_mock(observation)
                except Exception as exc:
                    preparation_error = exc
        truth = CorpusTruth.model_validate_json((root / entry.ground_truth_path).read_text())
        expected_local = set(truth.expected_validation_codes)
        expected_external = set(truth.expected_reconciliation_codes)
        expected = expected_local | expected_external
        expected_review = bool(expected)
        controlled = controlled_extraction(invoice, source_hash)
        controlled_local = controlled_validation(controlled)
        controlled_external = reconcile_invoice(controlled, controlled_local, truth.repository())
        controlled_local_codes = {f.code.value for f in controlled_local.findings}
        controlled_external_codes = {f.code.value for f in controlled_external.findings}
        controlled_findings = controlled_local_codes | controlled_external_codes
        base = {"case_id": entry.case_id, "category": entry.category.value,
                "supplier": entry.supplier_code, "layout": entry.layout.value,
                "scan_profile": entry.scan_profile.value, "expected_findings": sorted(expected),
                "expected_validation_findings": sorted(expected_local),
                "expected_reconciliation_findings": sorted(expected_external),
                "expected_review": expected_review}
        controlled_rows.append({**base, "observed_findings": sorted(controlled_findings),
                                "validation_findings": sorted(controlled_local_codes),
                                "reconciliation_findings": sorted(controlled_external_codes),
                                "validation_decision": controlled_local.decision.value,
                                "reconciliation_decision": controlled_external.decision.value,
                                "review_required": controlled_external.decision.value != "ready_for_posting_review",
                                "unperformed_checks": list(controlled_external.unperformed_checks),
                                "false_positives": sorted(controlled_findings - expected),
                                "false_negatives": sorted(expected - controlled_findings)})
        if not run_ocr:
            continue
        failure = None
        extraction_scored = False
        try:
            if preparation_error is not None:
                raise preparation_error
            extraction_score = evaluate_extraction(extraction.invoice,
                                                   SimpleNamespace(case_id=entry.case_id,
                                                                   displayed_invoice=invoice))
            extraction_scores.append(extraction_score)
            extraction_scored = True
            for group in ("overall", f"category:{entry.category.value}",
                          f"supplier:{entry.supplier_code}", f"scan_profile:{entry.scan_profile.value}"):
                score_groups[group]["extraction"].append(extraction_score)
            local = validate_invoice(extraction, observation)
            external = reconcile_invoice(extraction, local, truth.repository())
            local_codes = {f.code.value for f in local.findings}
            external_codes = {f.code.value for f in external.findings}
            observed = local_codes | external_codes
            pipeline_rows.append({**base, "observed_findings": sorted(observed),
                                  "validation_findings": sorted(local_codes),
                                  "reconciliation_findings": sorted(external_codes),
                                  "mock_extraction": extraction.invoice.model_dump(mode="json"),
                                  "ocr_text": observation.normalized_text,
                                  "validation_decision": local.decision.value,
                                  "reconciliation_decision": external.decision.value,
                                  "review_required": external.decision.value != "ready_for_posting_review",
                                  "unperformed_checks": list(external.unperformed_checks),
                                  "false_positives": sorted(observed - expected),
                                  "false_negatives": sorted(expected - observed)})
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
            if not extraction_scored:
                extraction_score = evaluate_extraction(_empty_invoice(),
                                                       SimpleNamespace(case_id=entry.case_id,
                                                                       displayed_invoice=invoice))
                extraction_scores.append(extraction_score)
                for group in ("overall", f"category:{entry.category.value}",
                              f"supplier:{entry.supplier_code}", f"scan_profile:{entry.scan_profile.value}"):
                    score_groups[group]["extraction"].append(extraction_score)
            pipeline_rows.append({**base, "observed_findings": [],
                                  "validation_findings": [], "reconciliation_findings": [],
                                  "validation_decision": "not_run",
                                  "reconciliation_decision": "not_run", "review_required": True,
                                  "unperformed_checks": ["validation", "reconciliation"],
                                  "false_positives": [], "false_negatives": sorted(expected),
                                  "failure": failure})
    extraction_aggregate = aggregate_extractions(tuple(extraction_scores)) if extraction_scores else None
    entry_by_id = {entry.case_id: entry for entry in manifest.entries}

    def ocr_snapshot(scores, group_name="overall"):
        aggregate = aggregate_evaluations(tuple(scores)).model_dump(mode="json")
        aggregate["reference"] = "selectable source PDF printed text; fields from corpus invoice input"
        if group_name == "overall":
            aggregate["failures"] = len(ocr_failures)
        else:
            kind, value = group_name.split(":", 1)
            def belongs(item):
                entry = entry_by_id[item["case_id"]]
                return {"category": entry.category.value, "supplier": entry.supplier_code,
                        "scan_profile": entry.scan_profile.value}[kind] == value
            aggregate["failures"] = sum(belongs(item) for item in ocr_failures)
        for item in aggregate["invoices"]:
            item.pop("processing_seconds", None)
        return aggregate

    report = {
        "configuration": {"corpus_version": manifest.version, "generator_version": manifest.generator_version,
                          "master_seed": manifest.master_seed, "dpi": manifest.dpi,
                          "provider": "deterministic mock", "controlled": "invoice-visible transcription; no OCR or model accuracy claim"},
        "engine_versions": {"tesseract": str(pytesseract.get_tesseract_version()).splitlines()[0] if run_ocr else None,
                            "mock": MockProvider.model_name,
                            "python": sys.version.split()[0],
                            "pydantic": version("pydantic"), "reportlab": version("reportlab"),
                            "pypdfium2": version("pypdfium2"), "pillow": version("pillow"),
                            "pytesseract": version("pytesseract")},
        "distribution": {"categories": manifest.category_counts, "suppliers": manifest.supplier_counts,
                         "layouts": manifest.layout_counts},
        "controlled": {"rows": controlled_rows, "groups": _aggregate_rows(controlled_rows, mode="controlled")},
        "ocr_mock": {"rows": pipeline_rows, "groups": _aggregate_rows(pipeline_rows, mode="ocr_mock"),
                     "processing_failures": sum(bool(row.get("failure")) for row in pipeline_rows),
                     "ocr_failures": ocr_failures,
                     "ocr": ocr_snapshot(ocr_scores) if ocr_scores else None,
                     "extraction": extraction_aggregate.model_dump(mode="json") if extraction_aggregate else None},
        "duplicate_detection": {
            mode: {code: sum(code in row["observed_findings"] for row in rows
                             if row["category"] == "duplicate_invoice")
                   for code in ("duplicate_invoice_identity", "exact_repeat_submission",
                                "conflicting_invoice_document", "duplicate_document_hash")}
            for mode, rows in (("controlled", controlled_rows), ("ocr_mock", pipeline_rows))},
        "pipeline_failure_codes": {
            kind: dict(sorted(Counter(code for row in pipeline_rows for code in row[kind]).items()))
            for kind in ("false_positives", "false_negatives")},
        "primary_defects": {
            mode: {"expected_cases": sum(bool(row["expected_findings"]) for row in rows),
                   "detected_cases": sum(bool(set(row["observed_findings"]) & set(row["expected_findings"]))
                                         for row in rows if row["expected_findings"])}
            for mode, rows in (("controlled", controlled_rows), ("ocr_mock", pipeline_rows))},
        "ocr_groups": {name: ocr_snapshot(group["ocr"], name) for name, group in sorted(score_groups.items()) if group["ocr"]},
        "extraction_groups": {name: aggregate_extractions(tuple(group["extraction"])).model_dump(mode="json")
                              for name, group in sorted(score_groups.items()) if group["extraction"]},
        "limitations": ["Controlled transcription tests business rules and is not an OCR or model score.",
                        "The controlled local validation oracle is a separate subset of rules, not production Task 12 validation; its 30 missing-total matches do not establish Task 12 accuracy.",
                        "Mock extraction is deterministic and is not a live LLM score.",
                        (f"Mock product-identifier accuracy is only "
                         f"{extraction_aggregate.fields['product_identifier'].correct}/"
                         f"{extraction_aggregate.fields['product_identifier'].expected} expected printed identifiers; "
                         "this is a major offline-baseline limitation.") if extraction_aggregate else
                        "Mock extraction was not run.",
                        "OCR and mock errors can obscure business findings; skipped checks never count as passed.",
                        "PDF text and OCR reading order can differ, inflating CER and WER.",
                        "Repeated equal printed values can share one OCR presence hit; presence is not one-to-one alignment.",
                        "Identifier syntax does not establish registration or real product identity."],
    }
    by_case = {row["case_id"]: row for row in pipeline_rows}
    examples = [
        ("clean_controlled", "corpus_clean_01"),
        ("clean_pipeline_flagged", "corpus_clean_04"),
        ("missing_totals", "corpus_missing_totals_01"),
        ("severe_scan_degradation", "corpus_blurry_scan_01"),
        ("quantity_mismatch", "corpus_quantity_mismatch_01"),
        ("duplicate_invoice", "corpus_duplicate_invoice_01"),
        ("unknown_product", "corpus_unknown_product_01"),
        ("price_anomaly", "corpus_price_anomaly_01"),
    ]
    if run_ocr:
        for row in pipeline_rows:
            if (row["category"] == "duplicate_invoice" and
                    "duplicate_document_hash" in row["observed_findings"] and
                    row["case_id"] != "corpus_duplicate_invoice_01"):
                examples.append(("duplicate_hash_only_identity_misread", row["case_id"]))
        for code, label in (("missing_product_identifier", "missing_identifier"),
                            ("missing_unit_cost", "missing_unit_cost")):
            match = next((row for row in pipeline_rows if code in row["observed_findings"]), None)
            if match is not None:
                examples.append((label, match["case_id"]))
    report["representative_cases"] = []
    for label, case_id in examples:
        entry = entry_by_id[case_id]
        visible = SyntheticInvoiceInput.model_validate_json((root / entry.input_path).read_text())
        truth = CorpusTruth.model_validate_json((root / entry.ground_truth_path).read_text())
        row = by_case.get(case_id)
        report["representative_cases"].append({
            "reason": label, "case_id": case_id,
            "invoice_visible": {
                "supplier": visible.supplier.name, "invoice_number": visible.invoice_number,
                "invoice_date": visible.invoice_date.isoformat(),
                "po_reference": visible.purchase_order_reference,
                "lines": [{"row": line.line_number, "identifier": next(
                    (product.upc or product.ndc for product in visible.products if product.sku == line.product_sku)),
                           "quantity": line.quantity, "unit_cost": str(line.unit_cost),
                           "line_total": str(line.line_total)} for line in visible.lines],
                "printed_totals": (visible.displayed_totals.model_dump(mode="json")
                                   if visible.displayed_totals else None),
            },
            "controlled": next(item for item in controlled_rows if item["case_id"] == case_id),
            "trusted_snapshot": {
                "supplier": truth.trusted_supplier.model_dump(mode="json"),
                "catalog_identifiers": [product.upc or product.ndc for product in truth.trusted_products],
                "po_lines": [line.model_dump(mode="json") for line in truth.trusted_po.lines],
                "prior_invoices": [prior.model_dump(mode="json") for prior in truth.prior_invoices],
            },
            "ocr_mock": row,
        })
    return report


def write_report(root: Path, report: dict) -> None:
    reports = Path(root) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    temporary = reports / "metrics.json.tmp"
    temporary.write_bytes(payload)
    temporary.replace(reports / "metrics.json")
    configuration = report["configuration"]
    lines = ["# Task 17 corpus evaluation", "",
             f"Version {configuration['corpus_version']}; generator {configuration['generator_version']}; "
             f"seed {configuration['master_seed']}; scan {configuration['dpi']} DPI.",
             f"Tesseract {report['engine_versions']['tesseract'] or 'not run'}; "
             f"extraction provider: {configuration['provider']} ({report['engine_versions']['mock']}).",
             f"Python {report['engine_versions']['python']}; Pydantic {report['engine_versions']['pydantic']}; "
             f"ReportLab {report['engine_versions']['reportlab']}; pypdfium2 {report['engine_versions']['pypdfium2']}; "
             f"Pillow {report['engine_versions']['pillow']}; pytesseract {report['engine_versions']['pytesseract']}.",
             "", "## Distribution", "",
             f"Categories: {report['distribution']['categories']}.",
             f"Suppliers: {report['distribution']['suppliers']}.",
             f"Layouts: {report['distribution']['layouts']}.",
             "", "## Finding and review confusion", "",
             "Each finding is scored as a `(case_id, finding_code)` pair. Review is a binary case decision.",
             "", "| Mode / group | Cases | Finding TP | FP | FN | Precision | Recall | F1 | Review TP | FP | FN | TN | Skipped |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    def ratio(value):
        return "n/a" if value is None else f"{value:.3f}"
    for mode in ("controlled", "ocr_mock"):
        for name, group in report[mode]["groups"].items():
            f, r = group["findings"], group["review_decisions"]
            lines.append(f"| {mode} / {name} | {group['cases']} | {f['true_positives']} | "
                         f"{f['false_positives']} | {f['false_negatives']} | {ratio(f['precision'])} | "
                         f"{ratio(f['recall'])} | {ratio(f['f1'])} | {r['true_positives']} | "
                         f"{r['false_positives']} | {r['false_negatives']} | {r['true_negatives']} | "
                         f"{group['unperformed_check_count']} |")
    lines.extend(["", "The controlled 90 finding true positives are 30 missing-total field codes, "
                  "10 PO quantity, 10 unknown-product, 10 PO price, and 30 duplicate-policy codes "
                  "(three codes for each of ten underlying duplicate cases). They are 90 case/code pairs, "
                  "not 90 independent business defects.",
                  "", "| Mode | Local validation TP/FP/FN | Trusted reconciliation TP/FP/FN | "
                  "Primary defect cases detected/expected |",
                  "| --- | --- | --- | ---: |"])
    for mode in ("controlled", "ocr_mock"):
        overall = report[mode]["groups"].get("overall")
        if overall is None:
            lines.append(f"| {mode} | not run | not run | not run |")
            continue
        local = overall["validation_findings"]
        external = overall["reconciliation_findings"]
        defects = report["primary_defects"][mode]
        lines.append(f"| {mode} | {local['true_positives']}/{local['false_positives']}/{local['false_negatives']} | "
                     f"{external['true_positives']}/{external['false_positives']}/{external['false_negatives']} | "
                     f"{defects['detected_cases']}/{defects['expected_cases']} |")
    lines.extend(["", "## OCR on scanned PDFs", "",
                  "CER and WER use selectable original-PDF text as denominators; token multiset dissimilarity "
                  "uses reference plus observed token counts. Exact-value presence checks only printed values.",
                  "", "| Group | Cases | Failures | CER | WER | Token dissimilarity | Exact identifier presence |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for name, ocr in report["ocr_groups"].items():
        identifiers = ocr["field_accuracy"]["product_identifiers"]
        lines.append(f"| {name} | {len(ocr['invoices'])} | {ocr['failures']} | "
                     f"{ratio(ocr['character_error_rate'])} | {ratio(ocr['word_error_rate'])} | "
                     f"{ratio(ocr['token_multiset_error_rate'])} | {ratio(identifiers)} |")
    if not report["ocr_groups"]:
        lines.append("| Not run | 0 | 0 | n/a | n/a | n/a | n/a |")
    lines.extend(["", "## Deterministic mock extraction", "",
                  "Correct and missing counts use expected invoice-visible field values. Unsupported includes "
                  "incorrect values and values printed nowhere; staff corrections are excluded.",
                  "", "| Field | Correct | Expected | Missing | Incorrect/unsupported |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    extraction = report["ocr_mock"]["extraction"]
    if extraction:
        for field, score in sorted(extraction["fields"].items()):
            lines.append(f"| {field} | {score['correct']} | {score['expected']} | "
                         f"{score['missing']} | {score['unsupported']} |")
    else:
        lines.append("| Not run | 0 | 0 | 0 | 0 |")
    lines.extend(["", "## Duplicate identity detection", "",
                  "Counts below are restricted to the ten intentional duplicate cases. The controlled mode "
                  "uses known invoice-visible identity; OCR/mock misses remain separate.",
                  "", "| Mode | Same supplier/number | Exact hash repeat | Conflicting bytes | Other hash collision |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for mode, counts in report["duplicate_detection"].items():
        lines.append(f"| {mode} | {counts['duplicate_invoice_identity']} | "
                     f"{counts['exact_repeat_submission']} | {counts['conflicting_invoice_document']} | "
                     f"{counts['duplicate_document_hash']} |")
    lines.extend(["", "## Leading pipeline errors", "",
                  f"False-positive codes: {report['pipeline_failure_codes']['false_positives']}.",
                  f"False-negative codes: {report['pipeline_failure_codes']['false_negatives']}.",
                  "Missing dates, identifiers, lines, or totals and low OCR confidence are pipeline-input "
                  "problems on otherwise clean or unrelated cases. Missed product, price, and quantity findings "
                  "usually follow missing or unusable identifiers/lines; skipped comparisons remain visible. "
                  "A hash-only duplicate finding does not establish that supplier or invoice number was extracted.", ""])
    lines.extend(["", "## Case traces", "",
                  "These samples show printed values, OCR transcript, deterministic mock observations, "
                  "separate local/trusted findings, expected codes, skipped checks, and review decisions. "
                  "The full OCR text and structured mock extraction are in `metrics.json`.", ""])
    if report["ocr_mock"]["rows"]:
        clean_ready = sum(row["category"] == "clean" and row["reconciliation_decision"] == "ready_for_posting_review"
                          for row in report["ocr_mock"]["rows"])
        lines.extend([f"Fully ready clean invoices in scanned/mock mode: {clean_ready}/10. "
                      "The controlled clean sample is ready.", ""])
    else:
        lines.extend(["Scanned/mock mode was not run; the controlled clean sample is ready.", ""])
    for example in report["representative_cases"]:
        visible = example["invoice_visible"]
        trusted = example["trusted_snapshot"]
        observed = example["ocr_mock"]
        controlled = example["controlled"]
        lines.extend([f"### {example['reason']}: {example['case_id']}", "",
                      f"Printed: {visible['supplier']}; invoice {visible['invoice_number']}; "
                      f"date {visible['invoice_date']}; PO {visible['po_reference']}; "
                      f"lines {visible['lines']}; totals {visible['printed_totals']}.", "",
                      f"Trusted: catalog identifiers {trusted['catalog_identifiers']}; "
                      f"PO lines {[(line['product_id'], line['ordered_quantity'], line['expected_unit_cost']) for line in trusted['po_lines']]}; "
                      f"prior invoice hashes {[prior['source_sha256'] for prior in trusted['prior_invoices']] }.", "",
                      f"Controlled local {controlled['validation_findings']}; trusted "
                      f"{controlled['reconciliation_findings']}; decision "
                      f"{controlled['reconciliation_decision']}.", ""])
        if observed:
            extraction = observed.get("mock_extraction")
            if extraction:
                lines.append(f"Mock: supplier {extraction['supplier_name']['value']}; "
                             f"invoice {extraction['invoice_number']['value']}; "
                             f"PO {extraction['purchase_order_reference']['value']}; "
                             f"lines {[(row['quantity']['value'], row['unit_cost']['value'], row['upc']['value'] or row['ndc']['value']) for row in extraction['lines']]}.")
            lines.extend([f"OCR: {observed.get('ocr_text', '')[:400].rstrip()}",
                          f"Expected {observed['expected_findings']}; local {observed['validation_findings']}; "
                          f"trusted {observed['reconciliation_findings']}; skipped "
                          f"{observed['unperformed_checks']}; decision {observed['reconciliation_decision']}.", ""])
    lines.extend(["", "## Representative false positives, false negatives, and skipped checks", ""])
    shown = 0
    for mode in ("controlled", "ocr_mock"):
        for row in report[mode]["rows"]:
            if row["false_positives"] or row["false_negatives"] or row.get("failure"):
                failure = f"; {row['failure']}" if row.get("failure") else ""
                lines.append(f"- {mode} {row['case_id']}: false positives {row['false_positives']}; "
                             f"false negatives {row['false_negatives']}; "
                             f"unperformed {row['unperformed_checks']}{failure}")
                shown += 1
            if shown >= 15:
                break
    lines.extend(["", "Full per-case outcomes, group metrics, and failure reasons are in `metrics.json`.",
                  "", "## Limits", "", *[f"- {item}" for item in report["limitations"]], ""])
    temporary = reports / "analysis.md.tmp"
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(reports / "analysis.md")
