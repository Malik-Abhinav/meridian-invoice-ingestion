import { z } from 'zod'
import type { Allocation, Approval, ApprovalResult, AuditEvent, Correction, Invoice, InvoiceList, Movement, PostingResult, ProcessingAttempt, Review, Upload } from '../types'

const uuid = z.uuid()
const uploadSchema = z.object({ document_id: uuid, upload_attempt_id: uuid, invoice_id: uuid.nullable(), identity_status: z.string(), source_sha256: z.string(), size_bytes: z.number(), correlation_id: z.string() })
const postingSchema = z.object({ status: z.enum(['posted', 'already_posted']), invoice_id: uuid, authorization_id: uuid, idempotency_key: z.string(), movement_ids: z.array(uuid) })
const paginationSchema = z.object({ limit: z.number(), offset: z.number(), total: z.number() })
const invoiceSchema = z.object({ id: uuid, status: z.string(), supplier_id: uuid, invoice_number: z.string().nullable(), invoice_number_normalized: z.string().nullable(), invoice_date: z.string().nullable(), subtotal: z.string().nullable(), tax: z.string().nullable(), total: z.string().nullable(), lines: z.array(z.object({ id: uuid, line_number: z.number(), product_id: uuid.nullable(), description: z.string(), upc: z.string().nullable(), ndc: z.string().nullable(), quantity: z.number().nullable(), unit_cost: z.string().nullable(), line_total: z.string().nullable() }).loose()), upload_attempt_ids: z.array(uuid), source_document: z.object({ id: uuid, sha256: z.string() }).nullable(), processing_status: z.string().nullable(), revision: z.number().nullable(), validation: z.unknown().nullable(), reconciliation: z.unknown().nullable(), posting_status: z.string().nullable() }).loose()
const listSchema = z.object({ items: z.array(invoiceSchema), pagination: paginationSchema })
const reviewSchema = z.object({ invoice: invoiceSchema, original_extraction: z.object({ invoice: z.object({ lines: z.array(z.unknown()) }).loose() }).loose(), current_extraction: z.object({ invoice: z.object({ lines: z.array(z.unknown()) }).loose() }).loose(), ocr: z.object({ pages: z.array(z.unknown()) }).loose(), validation: z.object({ findings: z.array(z.unknown()) }).loose(), reconciliation: z.object({ findings: z.array(z.unknown()), unperformed_checks: z.array(z.string()) }).loose(), corrected_fields: z.array(z.string()), allocation_candidates: z.array(z.object({ source_row_order: z.number(), po_line_ids: z.array(uuid) })), authorization_ready: z.boolean(), approved_authorization: z.object({ authorization_id: uuid, location_id: uuid, allocations: z.array(z.object({ invoice_line_id: uuid, purchase_order_line_id: uuid, source_row_order: z.number() })) }).nullable() }).loose()

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public correlationId?: string) { super(message); this.name = 'ApiError' }
}

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
type Validator = { parse(value: unknown): unknown }

export class ApiClient {
  constructor(private token: () => string | null, private unauthorized: () => void) {}

  private async request<T>(path: string, options: RequestInit = {}, schema?: Validator): Promise<T> {
    const token = this.token()
    if (!token) throw new ApiError(401, 'authentication_required', 'Enter a staff token to continue.')
    const response = await fetch(`${base}${path}`, {
      ...options,
      headers: { Authorization: `Bearer ${token}`, ...options.headers },
      cache: 'no-store',
    })
    const correlationId = response.headers.get('X-Correlation-ID') || undefined
    if (!response.ok) {
      const data = await response.json().catch(() => null) as { error?: { code?: string; message?: string; correlation_id?: string } } | null
      if (response.status === 401) this.unauthorized()
      throw new ApiError(response.status, data?.error?.code || 'http_error', data?.error?.message || `Request failed (${response.status}).`, data?.error?.correlation_id || correlationId)
    }
    const data: unknown = await response.json()
    try { return schema ? schema.parse(data) as T : data as T }
    catch { throw new ApiError(502, 'invalid_response', 'The API returned an unexpected response.', correlationId) }
  }

  private json<T>(path: string, method: string, body: unknown, schema?: Validator): Promise<T> {
    return this.request(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, schema)
  }

  upload(file: File): Promise<Upload> { const body = new FormData(); body.append('file', file); return this.request('/api/v1/invoices/upload', { method: 'POST', body }, uploadSchema) }
  processUpload(id: string, retryFailed = false): Promise<ProcessingAttempt> { return this.request(`/api/v1/uploads/${encodeURIComponent(id)}/process${retryFailed ? '?retry_failed=true' : ''}`, { method: 'POST' }) }
  uploadProcessing(id: string, signal?: AbortSignal): Promise<{ identity_status: string; items: ProcessingAttempt[] }> { return this.request(`/api/v1/uploads/${encodeURIComponent(id)}/processing`, { signal }) }
  processInvoice(id: string): Promise<ProcessingAttempt> { return this.request(`/api/v1/invoices/${encodeURIComponent(id)}/process`, { method: 'POST' }) }
  invoices(params: { limit?: number; offset?: number; status?: string; supplier_id?: string } = {}, signal?: AbortSignal): Promise<InvoiceList> {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') search.set(key, String(value))
    return this.request(`/api/v1/invoices?${search}`, { signal }, listSchema)
  }
  invoice(id: string, signal?: AbortSignal): Promise<Invoice> { return this.request(`/api/v1/invoices/${encodeURIComponent(id)}`, { signal }, invoiceSchema) }
  processing(id: string, signal?: AbortSignal): Promise<{ items: ProcessingAttempt[] }> { return this.request(`/api/v1/invoices/${encodeURIComponent(id)}/processing`, { signal }) }
  audit(id: string, signal?: AbortSignal): Promise<{ items: AuditEvent[] }> { return this.request(`/api/v1/invoices/${encodeURIComponent(id)}/audit`, { signal }) }
  movements(id: string, signal?: AbortSignal): Promise<{ items: Movement[] }> { return this.request(`/api/v1/invoices/${encodeURIComponent(id)}/movements`, { signal }) }
  review(id: string, signal?: AbortSignal): Promise<Review> { return this.request(`/api/v1/invoices/${encodeURIComponent(id)}/review`, { signal }, reviewSchema) }
  correction(id: string, body: Correction): Promise<{ invoice_id: string; status: string; revision: number }> { return this.json(`/api/v1/invoices/${encodeURIComponent(id)}`, 'PATCH', body) }
  approve(id: string, body: Approval): Promise<ApprovalResult> { return this.json(`/api/v1/invoices/${encodeURIComponent(id)}/approve`, 'POST', body) }
  reject(id: string, revision: number, reason: string): Promise<{ invoice_id: string; status: string; revision: number }> { return this.json(`/api/v1/invoices/${encodeURIComponent(id)}/reject`, 'POST', { expected_revision: revision, reason }) }
  post(id: string, key: string): Promise<PostingResult> { return this.json(`/api/v1/invoices/${encodeURIComponent(id)}/post`, 'POST', { idempotency_key: key }, postingSchema) }
  async document(id: string, signal?: AbortSignal): Promise<Blob> {
    const token = this.token()
    if (!token) throw new ApiError(401, 'authentication_required', 'Enter a staff token to continue.')
    const response = await fetch(`${base}/api/v1/invoices/${encodeURIComponent(id)}/document`, { headers: { Authorization: `Bearer ${token}` }, signal, cache: 'no-store' })
    if (!response.ok) {
      if (response.status === 401) this.unauthorized()
      const data = await response.json().catch(() => null) as { error?: { code?: string; message?: string; correlation_id?: string } } | null
      throw new ApiError(response.status, data?.error?.code || 'document_failed', data?.error?.message || 'Document could not be loaded.', data?.error?.correlation_id || response.headers.get('X-Correlation-ID') || undefined)
    }
    return response.blob()
  }
}

export function retryRead(failures: number, error: Error): boolean { return failures < 1 && error instanceof ApiError && error.status >= 500 }
export function newPostingKey(): string { return crypto.randomUUID() }
export function allocationSummary(allocations: Allocation[]): string { return allocations.map(item => `Row ${item.source_row_order} → ${item.purchase_order_line_id}`).join('; ') }
