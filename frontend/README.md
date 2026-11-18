# Staff review dashboard (Task 16)

This independently runnable React/TypeScript application uses the existing FastAPI staff routes. It does not supply an identity provider or bypass backend approval, reconciliation, or posting rules.

## Run locally

Use Node.js 20.19 or later. Start the backend as described in [API.md](../docs/API.md), including a development bearer token only for a disposable local environment. Then:

```sh
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Enter the configured staff token in the explicit sign-in screen. The token stays in React memory; refreshing or signing out discards it. The Vite development server proxies `/api` and `/ready` to `http://127.0.0.1:8000`. Override the proxy with `API_PROXY_TARGET` before starting Vite if the API listens elsewhere. For a same-origin deployment or an API behind another path, set `VITE_API_BASE_URL` at build time. Do not put a token in a Vite environment variable or URL: Vite embeds `VITE_` values in the browser bundle. Production deployment must provide a trusted staff identity verifier and transport security.

```sh
npm run typecheck
npm run build
npm test
```

The upload screen defaults to 12 MiB guidance. `VITE_MAX_UPLOAD_BYTES` can change its client-side limit; the backend remains authoritative. Upload uses `fetch`, which cannot report byte-level progress here, so the UI shows an indeterminate upload state and preserves the attempt ID when the API returns it. Processing is an explicit second action. An upload may remain unlinked until supplier identification succeeds.

## Workflow

The dashboard and review queue aggregate the first 100 invoices returned by the API and label their counts as a fetched sample. The invoice register uses server pagination and filters by status and exact supplier UUID; invoice-number search applies only to the visible page. Invoice detail separates original extraction from current staff values, displays local and trusted findings, and lists skipped checks. PDF bytes are fetched with the bearer token and displayed through a temporary blob URL. Original OCR words, confidence, page, and field citations are inspectable alongside the PDF. Citations identify source text, not semantic correctness; exact bounding-box overlays are not implemented.

Corrections submit changed values and the current revision, then refresh validation and reconciliation. The UI warns before leaving or discarding unsaved edits. Approval requires an explicit location UUID and every trusted PO-line candidate, then a confirmation. The API does not expose a location-list route, so no location names are invented. Approval and posting have separate confirmations. Posting uses one in-memory idempotency key for an intended operation and reuses it on intentional retry. The backend computes inventory movements and rejects stale or ineligible requests.

Authenticated `GET /review` returns the persisted approval location and exact PO-line allocations. After a page refresh, staff re-enter the bearer token and can inspect those records before a separate posting confirmation. The dashboard checks that every approved allocation still identifies a current invoice line; the backend remains authoritative for freshness and inventory writes. No approval or posting mutation is retried automatically. A pending posting operation keeps its idempotency key in `sessionStorage` across refresh, bound to invoice and authorization IDs; the bearer token never enters browser storage. Sign-out or HTTP 401 clears that key and all sensitive query caches.

## Disposable browser verification

`scripts/serve_frontend_e2e.py` seeds a temporary PostgreSQL database with fictional supplier, PO, catalog, and location rows, serves the real FastAPI app with a random local development token, and drops the database on exit. It leaves invoice upload and processing for the browser. The token is written to a temporary file rather than logs. Because the Compose network is internal, expose only the disposable test container to loopback through a temporary second Docker network when running a browser outside Docker. Bind-mount a host-owned `0600` token file into the container and set `E2E_TOKEN_FILE` to its path. With the API at port 8000 and Vite at 5173, upload `output/evaluation/scanned/fixture_a_clean_01.pdf`, then process, review, correct, approve, refresh and reauthenticate, post, retry, and inspect audit and movements. The original OCR and extraction retain quantity `53`; the audited staff value becomes `3`; posting creates two receipts with balances `2` and `3`; same-key retry returns `already_posted`. This checks the real API and disposable PostgreSQL, not live model accuracy.

The backend HTTP demo provides a repeatable nonbrowser check:

```sh
docker compose run --rm test python scripts/run_api_demo.py
```

## Current limits

- Authentication is an explicit development token entry. Production needs a real identity provider and trusted verifier; the application does not persist credentials.
- Dashboard counts and queue indicators cover a fetched sample, not all database records. There is no server-side invoice-number search or location list.
- The PDF viewer offers page/word citation inspection without bounding-box overlays.
- Upload progress is indeterminate with `fetch`.
- The pending posting key survives refresh in session storage, but a new browser profile or cleared storage loses it. A posted invoice is then read-only; the receipt ledger remains available through the API.
- The current audit reports five high findings in the development toolchain: `picomatch@4.0.7` (GHSA-fc5q-39xc-vjpf) and dependent `fdir`, `tinyglobby`, Vite, and Vitest. The advisory lists no patched `picomatch` release, and npm still reports 4.0.7 as latest. `npm audit --omit=dev` reports zero production-runtime findings. npm's suggested fix downgrades Vite/Vitest across major versions, so no safe compatible security update was applied. Recheck when upstream publishes a patched version.
