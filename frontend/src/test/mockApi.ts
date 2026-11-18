import { vi } from 'vitest'
import type { Allocation, AuditEvent, Extraction, Invoice, Movement, OcrDocument, ProcessingAttempt, Review } from '../types'

export const IDS = {
  invoice: '11111111-1111-4111-8111-111111111111', supplier: '22222222-2222-4222-8222-222222222222',
  upload: '33333333-3333-4333-8333-333333333333', document: '44444444-4444-4444-8444-444444444444',
  line1: '55555555-5555-4555-8555-555555555551', line2: '55555555-5555-4555-8555-555555555552',
  po1: '66666666-6666-4666-8666-666666666661', po2: '66666666-6666-4666-8666-666666666662',
  location: '77777777-7777-4777-8777-777777777777', authorization: '88888888-8888-4888-8888-888888888888',
}
const SHA = 'a'.repeat(64)
const field = <T>(value: T | null, indexes: number[] = []) => ({ value, uncertainty: null, evidence: indexes.length ? [{ page_number: 1, word_indexes: indexes }] : [] })
export const originalExtraction: Extraction = { source_sha256: SHA, provider: 'mock', model_name: 'ocr-only-v1', invoice: {
  supplier_name: field('Imaginary Harbor Wholesale', [0]), invoice_number: field('FICT_A-INV-0001', [1]), invoice_date: field('2026-01-08', [2]), purchase_order_reference: field('FICT_A-PO-0001', [3]),
  lines: [
    { source_row_order: 1, line_number: field(1), description: field('Imaginary violet sleeve', [4]), upc: field('000000010115', [5]), ndc: field<string>(null), quantity: field(2, [6]), unit_cost: field('4.1250', [7]), printed_line_total: field('8.2500', [8]) },
    { source_row_order: 2, line_number: field(2), description: field('Fable teal bundle', [9]), upc: field<string>(null), ndc: field('00000010235', [10]), quantity: field(53, [11]), unit_cost: field('7.5000', [12]), printed_line_total: field('22.5000', [13]) },
  ], printed_subtotal: field('30.7500', [14]), printed_tax: field('0.5000', [15]), printed_total: field('31.2500', [16]),
} }
export const ocr: OcrDocument = { source_sha256: SHA, pages: [{ page_number: 1, text: 'Imaginary Harbor Wholesale invoice', words: ['Imaginary', 'FICT_A-INV-0001', '2026-01-08', 'FICT_A-PO-0001', 'sleeve', '000000010115', '2', '4.1250', '8.2500', 'bundle', '00000010235', '53', '7.5000', '22.5000', '30.7500', '0.5000', '31.2500'].map(text => ({ text, confidence: 94, left: 0, top: 0, width: 10, height: 10 })) }] }
export const baseInvoice: Invoice = { id: IDS.invoice, status: 'review_required', supplier_id: IDS.supplier, invoice_number: 'FICT_A-INV-0001', invoice_number_normalized: 'FICT_A-INV-0001', invoice_date: '2026-01-08', subtotal: '30.7500', tax: '0.5000', total: '31.2500', lines: [{ id: IDS.line1, line_number: 1, product_id: null, description: 'Imaginary violet sleeve', upc: '000000010115', ndc: null, quantity: 2, unit_cost: '4.1250', line_total: '8.2500' }, { id: IDS.line2, line_number: 2, product_id: null, description: 'Fable teal bundle', upc: null, ndc: '00000010235', quantity: 53, unit_cost: '7.5000', line_total: '22.5000' }], upload_attempt_ids: [IDS.upload], source_document: { id: IDS.document, sha256: SHA }, processing_status: 'completed', revision: 1, validation: { decision: 'needs_review', findings: [{ code: 'line_extension_mismatch', severity: 'error', manual_review_required: true, field: { field: 'printed_line_total', source_row_order: 2 } }] }, reconciliation: { decision: 'needs_review', findings: [], unperformed_checks: [] }, posting_status: null }
export const baseReview: Review = { invoice: baseInvoice, original_extraction: originalExtraction, current_extraction: originalExtraction, ocr, validation: baseInvoice.validation!, reconciliation: baseInvoice.reconciliation!, corrected_fields: [], allocation_candidates: [{ source_row_order: 1, po_line_ids: [IDS.po1] }, { source_row_order: 2, po_line_ids: [IDS.po2] }], authorization_ready: false, approved_authorization: null }
export const processing: ProcessingAttempt = { id: IDS.upload, upload_attempt_id: IDS.upload, document_id: IDS.document, invoice_id: IDS.invoice, source_sha256: SHA, config_fingerprint: SHA, status: 'completed', attempt_count: 1, retryable: false, provider: 'mock', model_name: 'ocr-only-v1', ocr, extraction: originalExtraction, validation: baseReview.validation, reconciliation: baseReview.reconciliation, failure: null, started_at: '2026-01-08T10:00:00Z', completed_at: '2026-01-08T10:01:00Z' }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', 'X-Correlation-ID': 'test-correlation' } })
const problem = (code: string, status: number) => json({ error: { code, message: `${code} from API`, correlation_id: 'test-correlation' } }, status)

export function mockApi(options: { invalidToken?: boolean; expireAfterProbe?: boolean; unlinked?: boolean; uploadError?: boolean; correctionConflict?: boolean; unperformed?: boolean; initialStatus?: string; invoiceCount?: number; forbiddenDocument?: boolean; postConflict?: boolean } = {}) {
  let status = options.initialStatus || 'review_required'
  let revision = 1
  let quantity = 53
  let corrected = false
  let postedKey: string | null = null
  let lastApproval: unknown = null
  let lastCorrection: unknown = null
  const audit: AuditEvent[] = [{ id: IDS.document, actor_type: 'system', actor_id: null, action: 'process', before: null, after: { status }, created_at: '2026-01-08T10:01:00Z' }]
  const movements: Movement[] = []
  const calls: { path: string; method: string; body?: unknown; authorization: string | null }[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), 'http://localhost')
    const path = url.pathname
    const method = init?.method || 'GET'
    const authorization = new Headers(init?.headers).get('Authorization')
    const body = init?.body && !(init.body instanceof FormData) ? JSON.parse(String(init.body)) as unknown : undefined
    calls.push({ path, method, body, authorization })
    if (path === '/ready') return json({ status: 'ready' })
    if (!authorization || authorization === 'Bearer invalid' || options.invalidToken) return problem('invalid_credentials', 401)
    if (options.expireAfterProbe && path === '/api/v1/invoices' && method === 'GET' && calls.filter(call => call.path === path).length > 1) return problem('credentials_expired', 401)
    const invoice: Invoice = { ...baseInvoice, status, revision, posting_status: status === 'posted' ? 'posted' : status === 'approved' ? 'authorized' : null, lines: baseInvoice.lines.map((line, index) => index === 1 ? { ...line, quantity } : line) }
    const current: Extraction = JSON.parse(JSON.stringify(originalExtraction)) as Extraction
    current.invoice.lines[1].quantity = { ...current.invoice.lines[1].quantity, value: quantity, evidence: corrected ? [] : current.invoice.lines[1].quantity.evidence }
    const validation = corrected ? { decision: 'needs_review', findings: [{ code: 'missing_evidence', severity: 'warning', manual_review_required: true, field: { field: 'quantity', source_row_order: 2 } }] } : baseReview.validation
    const reconciliation = options.unperformed ? { ...baseReview.reconciliation, unperformed_checks: ['supplier_verification'] } : baseReview.reconciliation
    const approvedAuthorization: Review['approved_authorization'] = status === 'approved' || status === 'posted' ? { authorization_id: IDS.authorization, location_id: IDS.location, allocations: (lastApproval as { allocations?: Allocation[] } | null)?.allocations || [{ invoice_line_id: IDS.line1, purchase_order_line_id: IDS.po1, source_row_order: 1 }, { invoice_line_id: IDS.line2, purchase_order_line_id: IDS.po2, source_row_order: 2 }] } : null
    const review: Review = { ...baseReview, invoice: { ...invoice, validation, reconciliation }, current_extraction: current, validation, reconciliation, corrected_fields: corrected ? ['line[2].quantity'] : [], authorization_ready: corrected && !options.unperformed, approved_authorization: approvedAuthorization }
    if (path === '/api/v1/invoices' && method === 'GET') {
      const filter = url.searchParams.get('status')
      const rows: Invoice[] = filter && filter !== status ? [] : Array.from({ length: options.invoiceCount || 1 }, (_, index) => ({ ...invoice, id: `${(index + 1).toString(16).padStart(8, '0')}-1111-4111-8111-111111111111`, invoice_number: `${invoice.invoice_number}-${index + 1}` }))
      if (!options.invoiceCount) rows[0] = invoice
      const offset = Number(url.searchParams.get('offset') || '0'), limit = Number(url.searchParams.get('limit') || '20')
      return json({ items: rows.slice(offset, offset + limit), pagination: { limit, offset, total: rows.length } })
    }
    if (path === '/api/v1/invoices/upload' && method === 'POST') return options.uploadError ? problem('invalid_pdf', 400) : json({ document_id: IDS.document, upload_attempt_id: IDS.upload, invoice_id: null, identity_status: 'pending', source_sha256: SHA, size_bytes: 1000, correlation_id: 'test-correlation' }, 201)
    if (path === `/api/v1/uploads/${IDS.upload}/process` && method === 'POST') return json({ ...processing, invoice_id: options.unlinked ? null : IDS.invoice, status: 'completed', upload_identity_status: options.unlinked ? 'conflict' : 'linked' })
    if (path === `/api/v1/uploads/${IDS.upload}/processing` && method === 'GET') return json({ identity_status: options.unlinked ? 'conflict' : 'linked', items: [{ ...processing, invoice_id: options.unlinked ? null : IDS.invoice }] })
    if (path === `/api/v1/invoices/${IDS.invoice}` && method === 'GET') return json(invoice)
    if (path === `/api/v1/invoices/${IDS.invoice}/review` && method === 'GET') return json(review)
    if (path === `/api/v1/invoices/${IDS.invoice}/processing` && method === 'GET') return json({ items: [processing] })
    if (path === `/api/v1/invoices/${IDS.invoice}/audit` && method === 'GET') return json({ items: audit })
    if (path === `/api/v1/invoices/${IDS.invoice}/movements` && method === 'GET') return json({ items: movements })
    if (path === `/api/v1/invoices/${IDS.invoice}/document` && method === 'GET') return options.forbiddenDocument ? problem('staff_forbidden', 403) : new Response(new Blob(['%PDF-1.4'], { type: 'application/pdf' }), { status: 200 })
    if (path === `/api/v1/invoices/${IDS.invoice}` && method === 'PATCH') {
      lastCorrection = body
      if (options.correctionConflict) return problem('revision_conflict', 409)
      const payload = body as { expected_revision: number; lines?: { source_row_order: number; quantity?: number }[] }
      if (payload.expected_revision !== revision) return problem('revision_conflict', 409)
      quantity = payload.lines?.find(line => line.source_row_order === 2)?.quantity ?? quantity
      corrected = true; revision += 1
      audit.push({ id: IDS.line1, actor_type: 'staff', actor_id: 'local-reviewer', action: 'correct', before: { quantity: 53 }, after: { quantity }, created_at: '2026-01-08T10:02:00Z' })
      return json({ invoice_id: IDS.invoice, status, revision, validation, reconciliation: review.reconciliation, corrected_fields: ['line[2].quantity'] })
    }
    if (path === `/api/v1/invoices/${IDS.invoice}/approve` && method === 'POST') {
      lastApproval = body
      if (!corrected) return problem('missing_approval_evidence', 409)
      status = 'approved'
      audit.push({ id: IDS.line2, actor_type: 'staff', actor_id: 'local-reviewer', action: 'approve', before: null, after: { status }, created_at: '2026-01-08T10:03:00Z' })
      return json({ invoice_id: IDS.invoice, status, revision, authorization_id: IDS.authorization, approval_event_id: IDS.line2, location_id: IDS.location, evidence: { location_id: IDS.location } })
    }
    if (path === `/api/v1/invoices/${IDS.invoice}/reject` && method === 'POST') { status = 'rejected'; audit.push({ id: IDS.line2, actor_type: 'staff', actor_id: 'local-reviewer', action: 'reject', before: null, after: body as Record<string, unknown>, created_at: '2026-01-08T10:03:00Z' }); return json({ invoice_id: IDS.invoice, status, revision }) }
    if (path === `/api/v1/invoices/${IDS.invoice}/post` && method === 'POST') {
      if (options.postConflict) return problem('po_ineligible', 409)
      const key = (body as { idempotency_key: string }).idempotency_key
      if (postedKey && postedKey !== key) return problem('idempotency_conflict', 409)
      if (!postedKey) { postedKey = key; status = 'posted'; movements.push({ id: IDS.line1, movement_type: 'receipt', invoice_line_id: IDS.line1, purchase_order_line_id: IDS.po1, product_id: IDS.document, location_id: IDS.location, quantity_delta: 2, unit_cost: '4.1250', posting_key: key, created_at: '2026-01-08T10:04:00Z' }, { id: IDS.line2, movement_type: 'receipt', invoice_line_id: IDS.line2, purchase_order_line_id: IDS.po2, product_id: IDS.document, location_id: IDS.location, quantity_delta: 3, unit_cost: '7.5000', posting_key: key, created_at: '2026-01-08T10:04:00Z' }); audit.push({ id: IDS.authorization, actor_type: 'staff', actor_id: 'local-reviewer', action: 'post', before: null, after: { status }, created_at: '2026-01-08T10:04:00Z' }) }
      return json({ status: postedKey === key && calls.filter(call => call.path.endsWith('/post')).length > 1 ? 'already_posted' : 'posted', invoice_id: IDS.invoice, authorization_id: IDS.authorization, idempotency_key: key, movement_ids: [IDS.line1, IDS.line2] })
    }
    return problem('not_found', 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock, state: () => ({ status, revision, quantity, lastApproval, lastCorrection, postedKey, movements, audit }) }
}
