# Invoice ingestion and staff review API (Task 15)

## Run locally

1. Copy `.env.example` to `.env` and set local PostgreSQL credentials.
2. Apply migrations: `docker compose run --rm test alembic upgrade head`.
3. For a local demo only, set `MERIDIAN_DEV_AUTH=1` and a long random `MERIDIAN_DEV_STAFF_TOKEN` in `.env`.
4. Start `docker compose --profile api up --build api`. Open `http://127.0.0.1:8000/docs` for OpenAPI. The API binds only to local loopback.
5. Run the disposable HTTP demo with `docker compose run --rm test python scripts/run_api_demo.py`. The demo uses its own temporary PostgreSQL database and local temporary PDF directory.

Importing `meridian.api.app` does not connect to PostgreSQL or require an OpenAI key. `GET /health` reports process availability. `GET /ready` checks PostgreSQL and the configured PDF storage directory. All `/api/v1` routes, including read-only lists, audit history, processing evidence, and document downloads, require the same verified staff identity in this assessment implementation. OpenAPI declares HTTP bearer authentication on those routes. With development authentication disabled (the default), requests fail closed unless a deployment supplies `app.state.staff_authenticator`, a bearer-token verifier returning a nonempty staff actor ID. That production verifier must validate token expiry and staff authorization; the API does not infer an actor from a request header or body. The local development token is an explicitly enabled demo mode, not a production identity provider. Set `MERIDIAN_DEV_TOKEN_EXPIRES_AT` to an ISO 8601 timestamp with timezone to expire it; without that setting, the local token has no application-level expiry. Do not expose that mode beyond local development. No CORS origins are enabled.

## Endpoint flow

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/invoices/upload` | Multipart `file` PDF; returns document and upload-attempt IDs. No invoice identity is assumed yet. |
| POST | `/api/v1/uploads/{upload_id}/process` | OCR, extraction, validation, trusted reconciliation, and persisted processing state. `?retry_failed=true` explicitly retries a terminal failure. |
| GET | `/api/v1/uploads/{upload_id}/processing` | Processing evidence even when no invoice can yet be identified. |
| POST | `/api/v1/invoices/{invoice_id}/process` | Process the latest linked upload; completed same-config results are reused. |
| GET | `/api/v1/invoices` | `limit`, `offset`, `status`, and `supplier_id` filtering; deterministic creation/ID order and pagination metadata. |
| GET | `/api/v1/invoices/{invoice_id}` | Persisted header, lines, validation, reconciliation, source, revision, and posting status. |
| GET | `/api/v1/invoices/{invoice_id}/processing` | Original OCR and extraction, provider/model, stage outcomes, failures, and timestamps. |
| GET | `/api/v1/invoices/{invoice_id}/audit` | System and staff audit history. |
| GET | `/api/v1/invoices/{invoice_id}/movements` | Receipt movements linked to this invoice, with exact unit-cost strings. |
| GET | `/api/v1/invoices/{invoice_id}/document` | Original PDF bytes, selected by linked hash; no filesystem path parameter. |
| GET | `/api/v1/invoices/{invoice_id}/review` | Original and current values, confidence evidence, findings, unperformed checks, PO-line allocation candidates, and minimal persisted approval details when present. |
| PATCH | `/api/v1/invoices/{invoice_id}` | Staff correction with `expected_revision`; returns new revision and fresh checks. |
| POST | `/api/v1/invoices/{invoice_id}/approve` | Staff approval with revision, location ID, and explicit invoice-line-to-PO-line allocations. |
| POST | `/api/v1/invoices/{invoice_id}/reject` | Staff rejection with revision and reason. |
| POST | `/api/v1/invoices/{invoice_id}/post` | Task 14 transaction with stable `idempotency_key`; returns `posted` or `already_posted`. |

Example requests (the IDs come from earlier responses):

```sh
curl -H "Authorization: Bearer $MERIDIAN_DEV_STAFF_TOKEN" \
  -F 'file=@fictional-invoice.pdf;type=application/pdf' \
  http://127.0.0.1:8000/api/v1/invoices/upload
curl -X POST -H "Authorization: Bearer $MERIDIAN_DEV_STAFF_TOKEN" \
  http://127.0.0.1:8000/api/v1/uploads/UPLOAD_ID/process
curl -X PATCH -H "Authorization: Bearer $MERIDIAN_DEV_STAFF_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision":1,"lines":[{"source_row_order":2,"quantity":3}]}' \
  http://127.0.0.1:8000/api/v1/invoices/INVOICE_ID
curl -X POST -H "Authorization: Bearer $MERIDIAN_DEV_STAFF_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"idempotency_key":"stable-request-1"}' \
  http://127.0.0.1:8000/api/v1/invoices/INVOICE_ID/post
```

Get allocation candidates and persisted invoice-line IDs from `/review` before approval. The approval body has `expected_revision`, `location_id`, and `allocations`, each with `invoice_line_id`, `purchase_order_line_id`, and `source_row_order`. Posting accepts no quantity, price, product, or inventory delta from the client.

After approval, authenticated `GET /review` returns `approved_authorization` with `authorization_id`, `location_id`, and the exact persisted `allocations`; before approval it is `null`. This lets a staff client recover the posting confirmation after a browser refresh without creating another approval. It does not return internal evidence fingerprints or credentials. The posting endpoint still reloads authorization and allocations from PostgreSQL and applies all Task 14 checks. A missing or inconsistent authorization must block the client; this read response is not permission to bypass posting validation.

## Processing, evidence, and correction policy

The default provider is deterministic `MockProvider`. Set `MERIDIAN_EXTRACTION_PROVIDER=openai` and an explicit `OPENAI_API_KEY` to use the optional provider; a missing key fails without falling back to the mock. `MERIDIAN_OPENAI_MODEL` selects the model. The process identity is the PDF SHA-256 plus provider/model, OCR, and validation/reconciliation policy fingerprints. One processing row per document/configuration is enforced in PostgreSQL. A completed result is returned again without rerunning the provider, including for another upload of identical bytes. Concurrent claims return `processing_in_progress`. A stale running lease can be reclaimed after `MERIDIAN_PROCESSING_LEASE_SECONDS` (default 300). Completed OCR and extraction JSON are retained across retries. A retryable provider failure may be retried; `retry_failed=true` explicitly retries a terminal failure. A config change or staff correction cannot silently overwrite a reviewed invoice.

The Compose API service joins a separate outbound network so the explicitly configured OpenAI provider can call its endpoint. The default test service remains on the private database network and requires no external API credentials.

Original OCR and extraction JSON remain immutable in the processing row. Staff corrections update a separate review snapshot and persisted invoice fields, increment a revision, clear OCR citations for corrected values, and record before/after values in a staff audit event. Validation then reports missing OCR evidence for those values. At approval, Task 14 accepts only explicitly staff-corrected evidence/confidence findings, plus its existing repeated-product and explicit ambiguous-PO-line resolution policy. Arithmetic, missing values, supplier/PO/catalog discrepancies, duplicates, and unperformed checks still block. The staff event binds the corrected extraction, source, trusted records, location, allocations, and review resolution. Approval does not post stock. Posting uses Task 14's single locked transaction and writes the staff posting audit event atomically with receipt movements, balance increments, and invoice status.

A supplier that cannot be uniquely identified remains an upload-level processing result and needs a separate manual identity onboarding workflow; this API does not invent a supplier. Conflicting document bytes under one supplier/invoice identity remain an unlinked conflict upload and cannot replace the canonical invoice. Corrected invoices cannot be edited after approval or posting.

## Storage and security

`MERIDIAN_STORAGE_ROOT` defaults to `.meridian-storage/` (ignored by Git). `MERIDIAN_MAX_UPLOAD_BYTES` defaults to 12 MiB and `MERIDIAN_MAX_PDF_PAGES` to 30. An upload request is bounded while its ASGI body is read, before multipart parsing, at the PDF limit plus 64 KiB of multipart framing. The PDF file itself is bounded again while read from the parsed upload. Uploads require a plain filename and a readable unencrypted PDF structure. Original bytes are hashed and published to a hash-derived local path with a no-replace hard link; identical concurrent uploads share one blob but get distinct upload-attempt rows. Symlinks at the hash directory or PDF path are rejected; storage must be writable only by the service account to prevent filesystem races. PDFs are not stored in PostgreSQL. A crash after file publication but before the database commit may leave an orphaned hash file. Operators can periodically compare hash filenames with `invoice_documents.sha256` and remove only unreferenced files after a retention window; the service never deletes an existing blob on a failed transaction.

Read, review, and core lifecycle responses have explicit Pydantic response schemas in OpenAPI. Nested OCR, extraction, validation, and reconciliation evidence retains its own persisted contract structure. Response money values are decimal strings. Timestamps are UTC ISO 8601. Every response has `X-Correlation-ID`; errors use `{ "error": { "code", "message", "correlation_id" } }` and never expose stack traces or raw SQL. Common codes include `invalid_pdf` (400), `authentication_required` (401), `staff_forbidden` (403), `invoice_not_found` (404), `revision_conflict` or posting conflicts (409), `upload_too_large` (413), `invalid_request` (422), and `internal_error` (500). Production deployment still needs an authenticated staff verifier, TLS/reverse proxy controls, external request limits, durable shared file storage or an object-store adapter, and operational cleanup of orphaned files. The local demo cannot establish live OCR/model accuracy.

The current FastAPI/Starlette test dependency emits a `StarletteDeprecationWarning` for `httpx` in `TestClient` and recommends `httpx2`. This is an upstream test-client compatibility warning; it does not affect the running API. The review leaves the existing test transport in place until the supported dependency combination is verified separately.
