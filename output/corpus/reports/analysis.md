# Task 17 corpus evaluation

Version 1; generator task17-v1; seed 2029; scan 100 DPI.
Tesseract 5.5.0; extraction provider: deterministic mock (ocr-only-v1).
Python 3.12.16; Pydantic 2.14.3; ReportLab 4.5.1; pypdfium2 5.15.2; Pillow 13.0.0; pytesseract 0.3.13.

## Distribution

Categories: {'clean': 10, 'missing_totals': 10, 'blurry_scan': 10, 'quantity_mismatch': 10, 'duplicate_invoice': 10, 'unknown_product': 10, 'price_anomaly': 10}.
Suppliers: {'FICT_A': 24, 'FICT_B': 23, 'FICT_C': 23}.
Layouts: {'traditional': 24, 'compact': 23, 'purchase_order': 23}.

## Finding and review confusion

Each finding is scored as a `(case_id, finding_code)` pair. Review is a binary case decision.

| Mode / group | Cases | Finding TP | FP | FN | Precision | Recall | F1 | Review TP | FP | FN | TN | Skipped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| controlled / category:blurry_scan | 10 | 0 | 0 | 0 | n/a | n/a | n/a | 0 | 0 | 0 | 10 | 0 |
| controlled / category:clean | 10 | 0 | 0 | 0 | n/a | n/a | n/a | 0 | 0 | 0 | 10 | 0 |
| controlled / category:duplicate_invoice | 10 | 30 | 0 | 0 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 0 |
| controlled / category:missing_totals | 10 | 30 | 0 | 0 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 0 |
| controlled / category:price_anomaly | 10 | 10 | 0 | 0 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 0 |
| controlled / category:quantity_mismatch | 10 | 10 | 0 | 0 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 0 |
| controlled / category:unknown_product | 10 | 10 | 0 | 0 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 30 |
| controlled / overall | 70 | 90 | 0 | 0 | 1.000 | 1.000 | 1.000 | 50 | 0 | 0 | 20 | 30 |
| controlled / scan_profile:clean_raster | 16 | 17 | 0 | 0 | 1.000 | 1.000 | 1.000 | 13 | 0 | 0 | 3 | 12 |
| controlled / scan_profile:combined | 6 | 0 | 0 | 0 | n/a | n/a | n/a | 0 | 0 | 0 | 6 | 0 |
| controlled / scan_profile:jpeg_artifacts | 12 | 17 | 0 | 0 | 1.000 | 1.000 | 1.000 | 9 | 0 | 0 | 3 | 3 |
| controlled / scan_profile:low_contrast | 14 | 25 | 0 | 0 | 1.000 | 1.000 | 1.000 | 11 | 0 | 0 | 3 | 3 |
| controlled / scan_profile:mild_blur | 6 | 10 | 0 | 0 | 1.000 | 1.000 | 1.000 | 6 | 0 | 0 | 0 | 6 |
| controlled / scan_profile:moderate_blur | 4 | 0 | 0 | 0 | n/a | n/a | n/a | 0 | 0 | 0 | 4 | 0 |
| controlled / scan_profile:rotation | 12 | 21 | 0 | 0 | 1.000 | 1.000 | 1.000 | 11 | 0 | 0 | 1 | 6 |
| controlled / supplier:FICT_A | 24 | 29 | 0 | 0 | 1.000 | 1.000 | 1.000 | 17 | 0 | 0 | 7 | 9 |
| controlled / supplier:FICT_B | 23 | 33 | 0 | 0 | 1.000 | 1.000 | 1.000 | 17 | 0 | 0 | 6 | 9 |
| controlled / supplier:FICT_C | 23 | 28 | 0 | 0 | 1.000 | 1.000 | 1.000 | 16 | 0 | 0 | 7 | 12 |
| ocr_mock / category:blurry_scan | 10 | 0 | 63 | 0 | 0.000 | n/a | n/a | 0 | 10 | 0 | 0 | 31 |
| ocr_mock / category:clean | 10 | 0 | 24 | 0 | 0.000 | n/a | n/a | 0 | 10 | 0 | 0 | 68 |
| ocr_mock / category:duplicate_invoice | 10 | 24 | 34 | 6 | 0.414 | 0.800 | 0.545 | 10 | 0 | 0 | 0 | 81 |
| ocr_mock / category:missing_totals | 10 | 30 | 32 | 0 | 0.484 | 1.000 | 0.652 | 10 | 0 | 0 | 0 | 97 |
| ocr_mock / category:price_anomaly | 10 | 0 | 23 | 10 | 0.000 | 0.000 | 0.000 | 10 | 0 | 0 | 0 | 82 |
| ocr_mock / category:quantity_mismatch | 10 | 1 | 29 | 9 | 0.033 | 0.100 | 0.050 | 10 | 0 | 0 | 0 | 77 |
| ocr_mock / category:unknown_product | 10 | 0 | 31 | 10 | 0.000 | 0.000 | 0.000 | 10 | 0 | 0 | 0 | 91 |
| ocr_mock / overall | 70 | 55 | 236 | 35 | 0.189 | 0.611 | 0.289 | 50 | 20 | 0 | 0 | 527 |
| ocr_mock / scan_profile:clean_raster | 16 | 7 | 45 | 10 | 0.135 | 0.412 | 0.203 | 13 | 3 | 0 | 0 | 141 |
| ocr_mock / scan_profile:combined | 6 | 0 | 35 | 0 | 0.000 | n/a | n/a | 0 | 6 | 0 | 0 | 15 |
| ocr_mock / scan_profile:jpeg_artifacts | 12 | 12 | 30 | 5 | 0.286 | 0.706 | 0.407 | 9 | 3 | 0 | 0 | 95 |
| ocr_mock / scan_profile:low_contrast | 14 | 21 | 39 | 4 | 0.350 | 0.840 | 0.494 | 11 | 3 | 0 | 0 | 107 |
| ocr_mock / scan_profile:mild_blur | 6 | 3 | 23 | 7 | 0.115 | 0.300 | 0.167 | 6 | 0 | 0 | 0 | 61 |
| ocr_mock / scan_profile:moderate_blur | 4 | 0 | 28 | 0 | 0.000 | n/a | n/a | 0 | 4 | 0 | 0 | 16 |
| ocr_mock / scan_profile:rotation | 12 | 12 | 36 | 9 | 0.250 | 0.571 | 0.348 | 11 | 1 | 0 | 0 | 92 |
| ocr_mock / supplier:FICT_A | 24 | 15 | 59 | 14 | 0.203 | 0.517 | 0.291 | 17 | 7 | 0 | 0 | 153 |
| ocr_mock / supplier:FICT_B | 23 | 22 | 96 | 11 | 0.186 | 0.667 | 0.291 | 17 | 6 | 0 | 0 | 207 |
| ocr_mock / supplier:FICT_C | 23 | 18 | 81 | 10 | 0.182 | 0.643 | 0.283 | 16 | 7 | 0 | 0 | 167 |

The controlled 90 finding true positives are 30 missing-total field codes, 10 PO quantity, 10 unknown-product, 10 PO price, and 30 duplicate-policy codes (three codes for each of ten underlying duplicate cases). They are 90 case/code pairs, not 90 independent business defects.

| Mode | Local validation TP/FP/FN | Trusted reconciliation TP/FP/FN | Primary defect cases detected/expected |
| --- | --- | --- | ---: |
| controlled | 30/0/0 | 60/0/0 | 50/50 |
| ocr_mock | 30/207/0 | 25/87/35 | 19/50 |

## OCR on scanned PDFs

CER and WER use selectable original-PDF text as denominators; token multiset dissimilarity uses reference plus observed token counts. Exact-value presence checks only printed values.

| Group | Cases | Failures | CER | WER | Token dissimilarity | Exact identifier presence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| category:blurry_scan | 10 | 0 | 0.720 | 0.862 | 0.776 | 0.000 |
| category:clean | 10 | 0 | 0.317 | 0.387 | 0.151 | 0.241 |
| category:duplicate_invoice | 10 | 0 | 0.265 | 0.310 | 0.149 | 0.222 |
| category:missing_totals | 10 | 0 | 0.329 | 0.376 | 0.172 | 0.250 |
| category:price_anomaly | 10 | 0 | 0.225 | 0.267 | 0.144 | 0.115 |
| category:quantity_mismatch | 10 | 0 | 0.271 | 0.326 | 0.166 | 0.138 |
| category:unknown_product | 10 | 0 | 0.272 | 0.313 | 0.148 | 0.133 |
| overall | 70 | 0 | 0.346 | 0.410 | 0.227 | 0.158 |
| scan_profile:clean_raster | 16 | 0 | 0.253 | 0.306 | 0.139 | 0.341 |
| scan_profile:combined | 6 | 0 | 0.589 | 0.799 | 0.703 | 0.000 |
| scan_profile:jpeg_artifacts | 12 | 0 | 0.287 | 0.335 | 0.171 | 0.114 |
| scan_profile:low_contrast | 14 | 0 | 0.277 | 0.328 | 0.152 | 0.237 |
| scan_profile:mild_blur | 6 | 0 | 0.327 | 0.376 | 0.197 | 0.000 |
| scan_profile:moderate_blur | 4 | 0 | 0.900 | 0.949 | 0.909 | 0.000 |
| scan_profile:rotation | 12 | 0 | 0.281 | 0.332 | 0.141 | 0.108 |
| supplier:FICT_A | 24 | 0 | 0.322 | 0.381 | 0.225 | 0.059 |
| supplier:FICT_B | 23 | 0 | 0.431 | 0.483 | 0.209 | 0.406 |
| supplier:FICT_C | 23 | 0 | 0.308 | 0.384 | 0.241 | 0.028 |

## Deterministic mock extraction

Correct and missing counts use expected invoice-visible field values. Unsupported includes incorrect values and values printed nowhere; staff corrections are excluded.

| Field | Correct | Expected | Missing | Incorrect/unsupported |
| --- | ---: | ---: | ---: | ---: |
| description | 136 | 203 | 63 | 4 |
| invoice_date | 24 | 70 | 45 | 1 |
| invoice_number | 57 | 70 | 8 | 5 |
| line_number | 16 | 71 | 53 | 2 |
| printed_line_total | 133 | 203 | 70 | 0 |
| printed_subtotal | 48 | 60 | 12 | 0 |
| printed_tax | 48 | 60 | 12 | 0 |
| printed_total | 50 | 60 | 10 | 0 |
| product_identifier | 29 | 203 | 155 | 19 |
| purchase_order_reference | 48 | 70 | 13 | 9 |
| quantity | 135 | 203 | 68 | 0 |
| supplier_name | 42 | 70 | 2 | 26 |
| unit_cost | 133 | 203 | 70 | 0 |

## Duplicate identity detection

Counts below are restricted to the ten intentional duplicate cases. The controlled mode uses known invoice-visible identity; OCR/mock misses remain separate.

| Mode | Same supplier/number | Exact hash repeat | Conflicting bytes | Other hash collision |
| --- | ---: | ---: | ---: | ---: |
| controlled | 10 | 10 | 10 | 0 |
| ocr_mock | 8 | 8 | 8 | 2 |

## Leading pipeline errors

False-positive codes: {'duplicate_document_hash': 2, 'low_ocr_confidence': 24, 'malformed_product_identifier': 15, 'missing_invoice_date': 45, 'missing_invoice_number': 8, 'missing_line_total': 5, 'missing_lines': 14, 'missing_match_input': 1, 'missing_po_reference': 13, 'missing_product_identifier': 43, 'missing_quantity': 6, 'missing_subtotal': 12, 'missing_supplier': 2, 'missing_tax': 12, 'missing_total': 10, 'missing_unit_cost': 5, 'subtotal_mismatch': 1, 'unknown_po_reference': 9, 'unknown_supplier': 2, 'unreadable_field': 7}.
False-negative codes: {'conflicting_invoice_document': 2, 'duplicate_invoice_identity': 2, 'exact_repeat_submission': 2, 'po_price_mismatch': 10, 'po_quantity_mismatch': 9, 'unknown_product': 10}.
Missing dates, identifiers, lines, or totals and low OCR confidence are pipeline-input problems on otherwise clean or unrelated cases. Missed product, price, and quantity findings usually follow missing or unusable identifiers/lines; skipped comparisons remain visible. A hash-only duplicate finding does not establish that supplier or invoice number was extracted.


## Case traces

These samples show printed values, OCR transcript, deterministic mock observations, separate local/trusted findings, expected codes, skipped checks, and review decisions. The full OCR text and structured mock extraction are in `metrics.json`.

Fully ready clean invoices in scanned/mock mode: 0/10. The controlled clean sample is ready.

### clean_controlled: corpus_clean_01

Printed: Imaginary Harbor Wholesale; invoice FICT_A-INV-60E3D154; date 2029-08-13; PO FICT_A-PO-24817E2F; lines [{'row': 1, 'identifier': '000616157204', 'quantity': 3, 'unit_cost': '49.12', 'line_total': '147.36'}, {'row': 2, 'identifier': '86445260102', 'quantity': 5, 'unit_cost': '7.8', 'line_total': '39.0'}, {'row': 3, 'identifier': '000773378061', 'quantity': 8, 'unit_cost': '81.51', 'line_total': '652.08'}, {'row': 4, 'identifier': '60020533537', 'quantity': 1, 'unit_cost': '64.58', 'line_total': '64.58'}]; totals {'subtotal': '903.02', 'tax': '5.3600', 'total': '908.3800'}.

Trusted: catalog identifiers ['000616157204', '86445260102', '000773378061', '60020533537']; PO lines [('FICT_A01_0_P1', 3, '49.12'), ('FICT_A01_0_P2', 5, '7.8'), ('FICT_A01_0_P3', 8, '81.51'), ('FICT_A01_0_P4', 1, '64.58')]; prior invoice hashes [].

Controlled local []; trusted []; decision ready_for_posting_review.

Mock: supplier Imaginary Harbor Wholesale; invoice FICT_A-INV-60E3D154; PO FICT_A-PO-24817E2F; lines [].
OCR: Imaginary Harbor Wholesale INVOICE FICT_A-INV-60E3D154 2029-08-13 Purchase orde FICT_A-PO-24817E2F Product / identifier Imaginary nitrile sample box UPC 6157204 Fable practice dressing kit NDC 86445260102 Fable blue exam sleeve Fable practice dressing kit NDC 60020533537 Unit cost Line total 3 49,1200 147.3600 5 7.8000 39.0000 8 81.5100 652.0800 1 64.5800 64.5800 Subtotal 903.0200 Tax 5.3600 Total
Expected []; local ['missing_lines']; trusted []; skipped ['invoice_lines']; decision needs_review.

### clean_pipeline_flagged: corpus_clean_04

Printed: Imaginary Harbor Wholesale; invoice FICT_A-INV-8003FC0D; date 2029-11-11; PO FICT_A-PO-D8E15ABA; lines [{'row': 1, 'identifier': '000119575390', 'quantity': 7, 'unit_cost': '61.14', 'line_total': '427.98'}, {'row': 2, 'identifier': '44333162974', 'quantity': 8, 'unit_cost': '14.65', 'line_total': '117.20'}]; totals {'subtotal': '545.18', 'tax': '8.4500', 'total': '553.6300'}.

Trusted: catalog identifiers ['000119575390', '44333162974']; PO lines [('FICT_A04_0_P1', 7, '61.14'), ('FICT_A04_0_P2', 8, '14.65')]; prior invoice hashes [].

Controlled local []; trusted []; decision ready_for_posting_review.

Mock: supplier Imaginary Harbor Wholesale; invoice FICT_A-INV-8003FCOD; PO FICT_A-PO-D8E15ABA; lines [(7, '61.1400', None), (8, '14.6500', None)].
OCR: Imaginary Harbor Wholesale INVOICE Invoice number FICT_A-INV-8003FCOD vice date 2029-11-11 FICT_A-PO-D8E15ABA Product / identifier Qty Unit cost Line total Fictional amber storage vial 7 61.1400 427.9800 Fictional absorbent pad case 8 14.6500 117.2000 Subtotal 545.1800 Tax 8.4500 Total 553.6300
Expected []; local ['missing_product_identifier']; trusted ['missing_product_identifier']; skipped ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']; decision needs_review.

### missing_totals: corpus_missing_totals_01

Printed: Fable Ridge Distribution; invoice FICT_B-INV-B028612A; date 2029-02-04; PO FICT_B-PO-14CEE8A6; lines [{'row': 1, 'identifier': '000983526467', 'quantity': 6, 'unit_cost': '58.86', 'line_total': '353.16'}, {'row': 2, 'identifier': '19551903425', 'quantity': 6, 'unit_cost': '12.87', 'line_total': '77.22'}]; totals None.

Trusted: catalog identifiers ['000983526467', '19551903425']; PO lines [('FICT_B01_1_P1', 6, '58.86'), ('FICT_B01_1_P2', 6, '12.87')]; prior invoice hashes [].

Controlled local ['missing_subtotal', 'missing_tax', 'missing_total']; trusted []; decision needs_review.

Mock: supplier 7; invoice FICT_B-INV-B028612A; PO None; lines [(6, None, None), (6, None, None), (4, None, None)].
OCR: 7 Fable Ridge Distribution Qty Description / code 6 Mock disposable drape packet 6 Imaginary specimen tube bundie 19551803425 INVOICE FICT_B-INV-B028612A 04 Feb 353.1600 77.2200 58.8600 12.8700
Expected ['missing_subtotal', 'missing_tax', 'missing_total']; local ['missing_invoice_date', 'missing_line_total', 'missing_po_reference', 'missing_product_identifier', 'missing_subtotal', 'missing_tax', 'missing_total', 'missing_unit_cost']; trusted ['missing_po_reference', 'missing_product_identifier', 'unknown_supplier']; skipped ['duplicate_identity', 'line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost', 'po_status', 'po_supplier_match', 'supplier_po_match']; decision needs_review.

### severe_scan_degradation: corpus_blurry_scan_01

Printed: Northstar Fictional Supply; invoice FICT_C-INV-DB161E21; date 2029-02-18; PO FICT_C-PO-9B52E89A; lines [{'row': 1, 'identifier': '000846811099', 'quantity': 2, 'unit_cost': '32.15', 'line_total': '64.30'}, {'row': 2, 'identifier': '16315943554', 'quantity': 9, 'unit_cost': '58.47', 'line_total': '526.23'}, {'row': 3, 'identifier': '000858758917', 'quantity': 3, 'unit_cost': '18.18', 'line_total': '54.54'}]; totals {'subtotal': '645.07', 'tax': '1.4900', 'total': '646.5600'}.

Trusted: catalog identifiers ['000846811099', '16315943554', '000858758917']; PO lines [('FICT_C01_2_P1', 2, '32.15'), ('FICT_C01_2_P2', 9, '58.47'), ('FICT_C01_2_P3', 3, '18.18')]; prior invoice hashes [].

Controlled local []; trusted []; decision ready_for_posting_review.

Mock: supplier NORTHSTAR FICTIONAL SUPPLY; invoice None; PO FICT_C-PO-9B52E89A; lines [].
OCR: NORTHSTAR FICTIONAL SUPPLY PURCHASE ORDER FICT_C-PO-9B52E89A SUPPLIER INVOICE FICT_C4INV-DB161E21 NDC-coded products Fichonal stevie sarngte tray 5B 47 ? 26 25K UPC-coded products Fictional absorbent pad case 1x Z 64 Or tmagewary trie sarnple bos 16 1ax } 4 5400 invoice surmmary
Expected []; local ['missing_invoice_date', 'missing_invoice_number', 'missing_lines', 'missing_subtotal', 'missing_tax', 'missing_total']; trusted []; skipped ['duplicate_identity', 'invoice_lines']; decision needs_review.

### quantity_mismatch: corpus_quantity_mismatch_01

Printed: Imaginary Harbor Wholesale; invoice FICT_A-INV-510FB5B0; date 2029-08-12; PO FICT_A-PO-8FC39EA8; lines [{'row': 1, 'identifier': '000294057049', 'quantity': 7, 'unit_cost': '26.53', 'line_total': '185.71'}, {'row': 2, 'identifier': '67199285825', 'quantity': 5, 'unit_cost': '65.17', 'line_total': '325.85'}, {'row': 3, 'identifier': '000391770513', 'quantity': 9, 'unit_cost': '20.75', 'line_total': '186.75'}, {'row': 4, 'identifier': '08588057710', 'quantity': 3, 'unit_cost': '60.57', 'line_total': '181.71'}]; totals {'subtotal': '880.02', 'tax': '5.3500', 'total': '885.3700'}.

Trusted: catalog identifiers ['000294057049', '67199285825', '000391770513', '08588057710']; PO lines [('FICT_A01_3_P1', 5, '26.53'), ('FICT_A01_3_P2', 5, '65.17'), ('FICT_A01_3_P3', 9, '20.75'), ('FICT_A01_3_P4', 3, '60.57')]; prior invoice hashes [].

Controlled local []; trusted ['po_quantity_mismatch']; decision needs_review.

Mock: supplier Imaginary Harbor Wholesale; invoice FICT_A-INV-510FB5B0; PO FICT_A-PO-8FC39EA8; lines [].
OCR: Imaginary Harbor Wholesale INVOICE Invoice number FICT_A-INV-510FB5B0 Invoice date 2029-08-12 Purchase FICT_A-PO-8FC39EA8 Product / identifier Imaginary gauze demonstration pack Mock clinical swab carton Mock clinical swab carton UPC 000391 Fictional sterile sample tray NDC 08588057710 26.5300 65.1700 20.7500 60.5700 Line total 185.7100 325.8500 186.7500 181.7100 880.0200 5.3500 885.3700
Expected ['po_quantity_mismatch']; local ['missing_lines', 'missing_subtotal', 'missing_tax', 'missing_total']; trusted []; skipped ['invoice_lines']; decision needs_review.

### duplicate_invoice: corpus_duplicate_invoice_01

Printed: Fable Ridge Distribution; invoice FICT_B-INV-15CB8262; date 2029-03-25; PO FICT_B-PO-7CE75F60; lines [{'row': 1, 'identifier': '000062285506', 'quantity': 8, 'unit_cost': '50.73', 'line_total': '405.84'}, {'row': 2, 'identifier': '58116400644', 'quantity': 3, 'unit_cost': '38.17', 'line_total': '114.51'}]; totals {'subtotal': '520.35', 'tax': '9.8000', 'total': '530.1500'}.

Trusted: catalog identifiers ['000062285506', '58116400644']; PO lines [('FICT_B02_0_P1', 8, '50.73'), ('FICT_B02_0_P2', 3, '38.17')]; prior invoice hashes ['d4acb2b827ca784270b2564fecfb15e13d05189977f598f5b576c6e16c0dcebb', 'd0546d9ed63928d38fdc429dd8e5f52ab5168de9999207741f2b13117584f793'].

Controlled local []; trusted ['conflicting_invoice_document', 'duplicate_invoice_identity', 'exact_repeat_submission']; decision needs_review.

Mock: supplier None; invoice FICT_B-INV-15CB8262; PO FICT_B-PO-7CE75F60; lines [(8, '50.7300', '000062285506'), (3, None, '58116400644')].
OCR: INVOICE FICT_B-INV-15CB8262 Fable Ridge Distribution 25 Mar 2 PO FICT_B-PO-7CE75F60 Subtotal 520.3500 Tax 9.8000 Total 530.1500 Qty Description / code Extended Unit 8 Imaginary nitrile sample box 405.8400 50.7300 UPC 000062285506 114.5100 38.1700 3 Imaginary nitrile sample box NDC 58116400644
Expected ['conflicting_invoice_document', 'duplicate_invoice_identity', 'exact_repeat_submission']; local ['missing_invoice_date', 'missing_line_total', 'missing_supplier', 'missing_unit_cost']; trusted ['duplicate_document_hash', 'missing_match_input', 'missing_supplier']; skipped ['duplicate_identity', 'line[2].unit_cost', 'supplier_po_match']; decision needs_review.

### unknown_product: corpus_unknown_product_01

Printed: Northstar Fictional Supply; invoice FICT_C-INV-29BB2462; date 2029-01-29; PO FICT_C-PO-596E765D; lines [{'row': 1, 'identifier': '000308298307', 'quantity': 3, 'unit_cost': '76.32', 'line_total': '228.96'}, {'row': 2, 'identifier': '98297687700', 'quantity': 2, 'unit_cost': '44.99', 'line_total': '89.98'}, {'row': 3, 'identifier': '000897655307', 'quantity': 6, 'unit_cost': '52.65', 'line_total': '315.90'}]; totals {'subtotal': '634.84', 'tax': '7.2800', 'total': '642.1200'}.

Trusted: catalog identifiers ['98297687700', '000897655307']; PO lines [('FICT_C01_5_P2', 2, '44.99'), ('FICT_C01_5_P3', 6, '52.65')]; prior invoice hashes [].

Controlled local []; trusted ['unknown_product']; decision needs_review.

Mock: supplier NORTHSTAR FICTIONAL SUPPLY; invoice FICT_C-INV-29BB2462; PO FICT_C-PO-596E765D; lines [(2, '44.9900', None), (3, '76.3200', None), (6, '52.6500', None)].
OCR: NORTHSTAR FICTIONAL SUPPLY PURCHASE ORDER FICT_C-PO-596E765D SUPPLIER INVOICE FICT_C-INV-29BB2462 January 29, 202 NDC-coded products = Product / identifier Cost per unit Units Extended 2 Imaginary gauze demonstration pack 44.9900 2 89.9800 UPC-coded products = Product / identifier Cost per unit Units Extended Fictional amber storage vial 76.3200 3 228.9600 3 Fictional amber storage vial 52.6500 6
Expected ['unknown_product']; local ['missing_invoice_date', 'missing_product_identifier']; trusted ['missing_product_identifier']; skipped ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost']; decision needs_review.

### price_anomaly: corpus_price_anomaly_01

Printed: Imaginary Harbor Wholesale; invoice FICT_A-INV-EFA658E6; date 2029-03-29; PO FICT_A-PO-F8BFD464; lines [{'row': 1, 'identifier': '000967211914', 'quantity': 7, 'unit_cost': '30.96', 'line_total': '216.72'}, {'row': 2, 'identifier': '93075565692', 'quantity': 7, 'unit_cost': '30.89', 'line_total': '216.23'}, {'row': 3, 'identifier': '000278723137', 'quantity': 2, 'unit_cost': '15.71', 'line_total': '31.42'}]; totals {'subtotal': '464.37', 'tax': '4.2200', 'total': '468.5900'}.

Trusted: catalog identifiers ['000967211914', '93075565692', '000278723137']; PO lines [('FICT_A01_6_P1', 7, '31.9600'), ('FICT_A01_6_P2', 7, '30.89'), ('FICT_A01_6_P3', 2, '15.71')]; prior invoice hashes [].

Controlled local []; trusted ['po_price_mismatch']; decision needs_review.

Mock: supplier Imaginary Harbor Wholesale; invoice FICT_A-INV-EFA6S8E6; PO FICT_A-PO-F8BFD464; lines [(7, '30.9600', None), (7, '30.8900', None), (2, '15.7100', None)].
OCR: Imaginary Harbor Wholesale INVOICE FICT_A-INV-EFA6S8E6 ty 2029-03-29 Purchase orde FICT_A-PO-F8BFD464 Product / identifier Qty Unit cost Line total Fictional absorbent pad case 7 30.9600 216.7200 Fable practice dressing kit 7 30.8900 216.2300 Fictional amber storage vial 2 15.7100 31.4200 Subtotal 464.3700 Tax 4.2200 Total 468.5900
Expected ['po_price_mismatch']; local ['low_ocr_confidence', 'missing_product_identifier']; trusted ['missing_product_identifier']; skipped ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost']; decision needs_review.

### duplicate_hash_only_identity_misread: corpus_duplicate_invoice_03

Printed: Imaginary Harbor Wholesale; invoice FICT_A-INV-8003FC0D; date 2029-05-15; PO FICT_A-PO-D8E15ABA; lines [{'row': 1, 'identifier': '000119575390', 'quantity': 7, 'unit_cost': '61.14', 'line_total': '427.98'}, {'row': 2, 'identifier': '44333162974', 'quantity': 8, 'unit_cost': '14.65', 'line_total': '117.20'}]; totals {'subtotal': '545.18', 'tax': '10.1500', 'total': '555.3300'}.

Trusted: catalog identifiers ['000119575390', '44333162974']; PO lines [('FICT_A04_0_P1', 7, '61.14'), ('FICT_A04_0_P2', 8, '14.65')]; prior invoice hashes ['aef23bb4e0ff662b1df411d8a498175e0eb566b799533a040addac909f94ca92', '64b9ad506e05a9e8f23473acb4c082f3eba3355149e88979c9d11a2f2e312dd4'].

Controlled local []; trusted ['conflicting_invoice_document', 'duplicate_invoice_identity', 'exact_repeat_submission']; decision needs_review.

Mock: supplier Imaginary Harbor Wholesale; invoice FICT_A-INV-8003FCOD; PO FICT_A-PO-D8E; lines [(7, '61.1400', None), (8, '14.6500', None)].
OCR: Imaginary Harbor Wholesale INVOICE FICT_A-INV-8003FCOD 2029-05-15 FICT_A-PO-D8E 15ABA Product / identifier Qty Unit cost Line total Fictional amber storage vial 7 61.1400 427.9800 Fictional absorbent pad case 8 14.6500 117.2000 Subtotal 545.1800 Tax 10.1500 Total 555.3300
Expected ['conflicting_invoice_document', 'duplicate_invoice_identity', 'exact_repeat_submission']; local ['missing_product_identifier']; trusted ['duplicate_document_hash', 'missing_product_identifier', 'unknown_po_reference']; skipped ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'po_status', 'po_supplier_match']; decision needs_review.

### missing_identifier: corpus_clean_04

Printed: Imaginary Harbor Wholesale; invoice FICT_A-INV-8003FC0D; date 2029-11-11; PO FICT_A-PO-D8E15ABA; lines [{'row': 1, 'identifier': '000119575390', 'quantity': 7, 'unit_cost': '61.14', 'line_total': '427.98'}, {'row': 2, 'identifier': '44333162974', 'quantity': 8, 'unit_cost': '14.65', 'line_total': '117.20'}]; totals {'subtotal': '545.18', 'tax': '8.4500', 'total': '553.6300'}.

Trusted: catalog identifiers ['000119575390', '44333162974']; PO lines [('FICT_A04_0_P1', 7, '61.14'), ('FICT_A04_0_P2', 8, '14.65')]; prior invoice hashes [].

Controlled local []; trusted []; decision ready_for_posting_review.

Mock: supplier Imaginary Harbor Wholesale; invoice FICT_A-INV-8003FCOD; PO FICT_A-PO-D8E15ABA; lines [(7, '61.1400', None), (8, '14.6500', None)].
OCR: Imaginary Harbor Wholesale INVOICE Invoice number FICT_A-INV-8003FCOD vice date 2029-11-11 FICT_A-PO-D8E15ABA Product / identifier Qty Unit cost Line total Fictional amber storage vial 7 61.1400 427.9800 Fictional absorbent pad case 8 14.6500 117.2000 Subtotal 545.1800 Tax 8.4500 Total 553.6300
Expected []; local ['missing_product_identifier']; trusted ['missing_product_identifier']; skipped ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']; decision needs_review.

### missing_unit_cost: corpus_missing_totals_01

Printed: Fable Ridge Distribution; invoice FICT_B-INV-B028612A; date 2029-02-04; PO FICT_B-PO-14CEE8A6; lines [{'row': 1, 'identifier': '000983526467', 'quantity': 6, 'unit_cost': '58.86', 'line_total': '353.16'}, {'row': 2, 'identifier': '19551903425', 'quantity': 6, 'unit_cost': '12.87', 'line_total': '77.22'}]; totals None.

Trusted: catalog identifiers ['000983526467', '19551903425']; PO lines [('FICT_B01_1_P1', 6, '58.86'), ('FICT_B01_1_P2', 6, '12.87')]; prior invoice hashes [].

Controlled local ['missing_subtotal', 'missing_tax', 'missing_total']; trusted []; decision needs_review.

Mock: supplier 7; invoice FICT_B-INV-B028612A; PO None; lines [(6, None, None), (6, None, None), (4, None, None)].
OCR: 7 Fable Ridge Distribution Qty Description / code 6 Mock disposable drape packet 6 Imaginary specimen tube bundie 19551803425 INVOICE FICT_B-INV-B028612A 04 Feb 353.1600 77.2200 58.8600 12.8700
Expected ['missing_subtotal', 'missing_tax', 'missing_total']; local ['missing_invoice_date', 'missing_line_total', 'missing_po_reference', 'missing_product_identifier', 'missing_subtotal', 'missing_tax', 'missing_total', 'missing_unit_cost']; trusted ['missing_po_reference', 'missing_product_identifier', 'unknown_supplier']; skipped ['duplicate_identity', 'line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost', 'po_status', 'po_supplier_match', 'supplier_po_match']; decision needs_review.


## Representative false positives, false negatives, and skipped checks

- ocr_mock corpus_clean_01: false positives ['missing_lines']; false negatives []; unperformed ['invoice_lines']
- ocr_mock corpus_clean_02: false positives ['low_ocr_confidence', 'missing_invoice_date', 'missing_po_reference']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'po_status', 'po_supplier_match']
- ocr_mock corpus_clean_03: false positives ['missing_invoice_date', 'missing_lines']; false negatives []; unperformed ['invoice_lines']
- ocr_mock corpus_clean_04: false positives ['missing_product_identifier']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']
- ocr_mock corpus_clean_05: false positives ['malformed_product_identifier', 'missing_invoice_date', 'unknown_po_reference']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost', 'po_status', 'po_supplier_match']
- ocr_mock corpus_clean_06: false positives ['missing_invoice_date', 'missing_product_identifier', 'missing_quantity', 'unreadable_field']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost']
- ocr_mock corpus_clean_07: false positives ['missing_product_identifier', 'subtotal_mismatch']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']
- ocr_mock corpus_clean_08: false positives ['low_ocr_confidence', 'malformed_product_identifier', 'missing_invoice_date', 'missing_po_reference']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost', 'line[4].po_line', 'line[4].quantity', 'line[4].unit_cost', 'po_status', 'po_supplier_match']
- ocr_mock corpus_clean_09: false positives ['missing_invoice_date', 'missing_product_identifier']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']
- ocr_mock corpus_clean_10: false positives ['low_ocr_confidence', 'missing_product_identifier']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']
- ocr_mock corpus_missing_totals_01: false positives ['missing_invoice_date', 'missing_line_total', 'missing_po_reference', 'missing_product_identifier', 'missing_unit_cost', 'unknown_supplier']; false negatives []; unperformed ['duplicate_identity', 'line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost', 'po_status', 'po_supplier_match', 'supplier_po_match']
- ocr_mock corpus_missing_totals_02: false positives ['missing_invoice_date', 'missing_product_identifier']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost']
- ocr_mock corpus_missing_totals_03: false positives ['missing_product_identifier']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost']
- ocr_mock corpus_missing_totals_04: false positives ['low_ocr_confidence', 'missing_invoice_date', 'missing_po_reference']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost', 'line[4].po_line', 'line[4].quantity', 'line[4].unit_cost', 'po_status', 'po_supplier_match']
- ocr_mock corpus_missing_totals_05: false positives ['missing_invoice_date', 'missing_product_identifier']; false negatives []; unperformed ['line[1].po_line', 'line[1].quantity', 'line[1].unit_cost', 'line[2].po_line', 'line[2].quantity', 'line[2].unit_cost', 'line[3].po_line', 'line[3].quantity', 'line[3].unit_cost']

Full per-case outcomes, group metrics, and failure reasons are in `metrics.json`.

## Limits

- Controlled transcription tests business rules and is not an OCR or model score.
- The controlled local validation oracle is a separate subset of rules, not production Task 12 validation; its 30 missing-total matches do not establish Task 12 accuracy.
- Mock extraction is deterministic and is not a live LLM score.
- Mock product-identifier accuracy is only 29/203 expected printed identifiers; this is a major offline-baseline limitation.
- OCR and mock errors can obscure business findings; skipped checks never count as passed.
- PDF text and OCR reading order can differ, inflating CER and WER.
- Repeated equal printed values can share one OCR presence hit; presence is not one-to-one alignment.
- Identifier syntax does not establish registration or real product identity.
