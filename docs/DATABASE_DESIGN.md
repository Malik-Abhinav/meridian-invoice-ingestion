# Database design: invoice identity, purchasing, inventory, and audit (Tasks 3–4)

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
| `purchase_orders` | UUID ID, supplier FK, supplier-scoped order number, status | Unique supplier/order number; `draft`, `open`, `closed`, or `cancelled`; supplier/status index |
| `purchase_order_lines` | UUID ID, order FK, line number, product FK, ordered quantity, expected unit cost | Unique order/line number; positive quantity; nonnegative cost |
| `locations` | UUID ID, code, name | Unique nonblank code; nonblank name |
| `inventory_balances` | UUID ID, product FK, location FK, available quantity | Unique product/location pair; nonnegative integer quantity; location/product lookup index |
| `inventory_movements` | UUID ID, product/location FKs, type, signed quantity, posting key, optional cost and source FKs | Unique posting key; one receipt per invoice line; one reversal per target; type-specific source and nonzero quantity checks |
| `audit_events` | UUID ID, actor, action, entity identity, optional JSONB before/after, time | Staff actor ID required; entity/time and actor/time indexes |

Money uses `NUMERIC(18,4)` with Python `Decimal`. All event timestamps use timezone-aware `TIMESTAMP WITH TIME ZONE`, with UTC defaults in Python and PostgreSQL. UUIDs are generated in Python. Foreign keys on invoice lines and upload attempts restrict deletion of referenced records; preserving their history is expected. Product matching is optional on invoice lines so an unknown product can reach review.

UPC and NDC are stored in separate nullable normalized columns. A product needs one identifier, not both. The exact normalization rules, including NDC segment ambiguity and UPC format validation, belong to a later task. This schema only rejects blank normalized values and prevents duplicates for present values.

## Boundaries of verification

Tests inspect ORM metadata, relationship configuration, and PostgreSQL DDL compilation. They do not execute constraints in PostgreSQL. Task 5 will add migrations and live database constraint tests. Application code must validate `Decimal` and UTC-aware timestamps before persistence; `NUMERIC` and `TIMESTAMP WITH TIME ZONE` alone do not reject Python floats or naive datetimes at the ORM boundary.

## Partial receipts and posting design

An `open` purchase order may be partly received. Ordered quantity is stored on its line; received quantity is **derived** from committed receipt movements associated with that line, adjusted for reversals. There is no received-quantity counter on the order. `closed` and `cancelled` are explicit workflow states, not automatic arithmetic results. The later reconciliation service will compare this derived quantity with the ordered quantity and decide whether another receipt is allowed.

A receipt movement has positive integer quantity, an invoice-line FK, and a PO-line FK. A unique partial index permits at most one receipt movement for an invoice line, and every movement has a unique posting key for retry detection. Adjustments have no invoice or PO source. A reversal references the original movement and has its own signed delta; the unique reversal-target index prevents the same movement from being reversed twice. The schema does **not** enforce that a reversal has the exact opposite quantity or the same product/location as its target; the future posting service must do that inside a transaction. It must also verify that the invoice line, PO line, and movement product agree.

Balances are cached by product and location for fast storefront reads. The posting service will lock or atomically update the affected balance row, insert the movement, and change invoice state in one PostgreSQL transaction. No posting or concurrency behavior is implemented or proven here. Movements and audit events are intended as append-only records. ORM models and foreign keys alone cannot prevent an administrator or unrestricted SQL writer from changing or deleting rows; later database permissions or triggers may enforce immutability if deployment requires it.

## Audit and money

Audit events record `system` or `staff` actors, an action, an entity type/UUID, a UTC-aware time, and optional JSONB before/after snapshots. The polymorphic entity identity has no FK; application code must ensure it names a real entity. Audit rows are independent of OCR output and extraction history.

Unit costs and monetary invoice fields use `NUMERIC(18,4)`. The working assumption is that accepted source values have no more than four fractional decimal places. Future input services should reject excess precision before persistence and use exact `Decimal` arithmetic; no implicit database rounding should decide a business amount. Currency-specific final rounding and tax rules are not implemented in this schema.
