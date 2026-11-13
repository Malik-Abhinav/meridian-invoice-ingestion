# Database design: invoice identity (Task 3)

This document describes the proposed PostgreSQL schema. No migration or live database verification exists yet.

## Identity and upload history

`invoice_documents` stores one metadata row per SHA-256 hash and a storage URI; file bytes are stored outside the database. `invoice_upload_attempts` stores every upload, including repeats. Its `document_id` is required; its `invoice_id` is nullable until identity is resolved. An attempt may later be marked `linked`, `duplicate`, `conflict`, or `missing_number`. A linked attempt must have an invoice ID. Multiple attempts may point to one document and one canonical invoice.

`invoices` represents a canonical supplier invoice and carries the reviewed header fields and lifecycle status. The unique partial index on `(supplier_id, invoice_number_normalized)` applies when the number is present. A missing number remains `NULL` and does not gain a fabricated identity. A second document with the same supplier and normalized number can be retained as another upload attempt and flagged as a conflict; it cannot create a second canonical invoice with that identity. A repeated upload of the same bytes points to the existing document hash. Matching, normalization, and conflict classification are **future service logic**; the schema does not infer them.

The current model requires a known supplier before creating a canonical invoice. Upload attempts and documents can exist without one. That supports OCR failures and incomplete identity without creating duplicate inventory obligations. Inventory posting is outside this task.

## Tables and constraints

| Table | Main columns | Guarantees and indexes |
| --- | --- | --- |
| `suppliers` | UUID ID, code, name, creation time | Unique code; nonblank code/name |
| `products` | UUID ID, name, optional normalized UPC and NDC | At least one identifier; unique partial index for each present identifier; nonblank values |
| `invoices` | UUID ID, supplier FK, optional raw/normalized number and date, status, optional totals | Unique supplier/normalized number when present; status and nonnegative money checks; `(status, created_at)` index |
| `invoice_lines` | UUID ID, invoice FK, line number, description, optional product FK, optional quantity/cost/total | Unique `(invoice_id, line_number)`; positive line number/quantity, nonnegative money; product lookup index |
| `invoice_documents` | UUID ID, SHA-256, URI, content type, creation time | Unique 64-character lowercase hexadecimal hash; nonblank URI |
| `invoice_upload_attempts` | UUID ID, document FK, optional invoice FK, original filename, identity status, upload time | Linked status requires invoice ID; indexes on document/time and invoice/time |

Money uses `NUMERIC(18,4)` with Python `Decimal`. All event timestamps use timezone-aware `TIMESTAMP WITH TIME ZONE`, with UTC defaults in Python and PostgreSQL. UUIDs are generated in Python. Foreign keys on invoice lines and upload attempts restrict deletion of referenced records; preserving their history is expected. Product matching is optional on invoice lines so an unknown product can reach review.

UPC and NDC are stored in separate nullable normalized columns. A product needs one identifier, not both. The exact normalization rules, including NDC segment ambiguity and UPC format validation, belong to a later task. This schema only rejects blank normalized values and prevents duplicates for present values.

## Boundaries of verification

Tests inspect ORM metadata, relationship configuration, and PostgreSQL DDL compilation. They do not execute constraints in PostgreSQL. Task 5 will add migrations and live database constraint tests. Application code must validate `Decimal` and UTC-aware timestamps before persistence; `NUMERIC` and `TIMESTAMP WITH TIME ZONE` alone do not reject Python floats or naive datetimes at the ORM boundary.
