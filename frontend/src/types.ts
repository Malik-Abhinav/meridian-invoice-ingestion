export type UUID = string
export type FieldValue<T> = { value: T | null; uncertainty: string | null; evidence: EvidenceRef[] }
export type EvidenceRef = { page_number: number; word_indexes: number[]; block_number?: number; paragraph_number?: number; line_number?: number }
export type ExtractedLine = {
  source_row_order: number
  line_number: FieldValue<number>
  description: FieldValue<string>
  upc: FieldValue<string>
  ndc: FieldValue<string>
  quantity: FieldValue<number>
  unit_cost: FieldValue<string>
  printed_line_total: FieldValue<string>
}
export type Extraction = {
  source_sha256: string
  provider: string
  model_name: string
  invoice: {
    supplier_name: FieldValue<string>
    invoice_number: FieldValue<string>
    invoice_date: FieldValue<string>
    purchase_order_reference: FieldValue<string>
    lines: ExtractedLine[]
    printed_subtotal: FieldValue<string>
    printed_tax: FieldValue<string>
    printed_total: FieldValue<string>
  }
}
export type OcrWord = { text: string; confidence: number; left: number; top: number; width: number; height: number }
export type OcrDocument = { source_sha256: string; pages: { page_number: number; text: string; words: OcrWord[] }[] }
export type Finding = { code: string; severity?: string; message?: string; detail?: string; manual_review_required?: boolean; field?: { field: string; source_row_order?: number | null } }
export type Validation = { decision: string; findings: Finding[]; field_assessments?: unknown[] }
export type Reconciliation = {
  decision: string
  findings: Finding[]
  unperformed_checks: string[]
  product_matches?: { source_row_order: number; po_line_ids: string[]; product_ids?: string[] }[]
}
export type InvoiceLine = { id: UUID; line_number: number; product_id: UUID | null; description: string; upc: string | null; ndc: string | null; quantity: number | null; unit_cost: string | null; line_total: string | null }
export type Invoice = {
  id: UUID; status: string; supplier_id: UUID; invoice_number: string | null; invoice_number_normalized: string | null
  invoice_date: string | null; subtotal: string | null; tax: string | null; total: string | null
  lines: InvoiceLine[]; upload_attempt_ids: UUID[]; source_document: { id: UUID; sha256: string } | null
  processing_status: string | null; revision: number | null; validation: Validation | null
  reconciliation: Reconciliation | null; posting_status: string | null
}
export type Pagination = { limit: number; offset: number; total: number }
export type InvoiceList = { items: Invoice[]; pagination: Pagination }
export type ProcessingAttempt = {
  id: UUID; upload_attempt_id: UUID; document_id: UUID; invoice_id: UUID | null; source_sha256: string
  config_fingerprint: string; status: string; attempt_count: number; retryable: boolean | null
  provider: string; model_name: string; ocr: OcrDocument | null; extraction: Extraction | null
  validation: Validation | null; reconciliation: Reconciliation | null
  failure: { code: string; detail: string | null } | null; started_at: string; completed_at: string | null
  requested_upload_attempt_id?: UUID; upload_identity_status?: string
}
export type Upload = { document_id: UUID; upload_attempt_id: UUID; invoice_id: UUID | null; identity_status: string; source_sha256: string; size_bytes: number; correlation_id: string }
export type Review = {
  invoice: Invoice; original_extraction: Extraction; current_extraction: Extraction; ocr: OcrDocument
  validation: Validation; reconciliation: Reconciliation; corrected_fields: string[]
  allocation_candidates: { source_row_order: number; po_line_ids: UUID[] }[]; authorization_ready: boolean
  approved_authorization: { authorization_id: UUID; location_id: UUID; allocations: Allocation[] } | null
}
export type AuditEvent = { id: UUID; actor_type: string; actor_id: string | null; action: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null; created_at: string }
export type Movement = { id: UUID; movement_type: string; invoice_line_id: UUID | null; purchase_order_line_id: UUID | null; product_id: UUID; location_id: UUID; quantity_delta: number; unit_cost: string | null; posting_key: string; created_at: string }
export type Allocation = { invoice_line_id: UUID; purchase_order_line_id: UUID; source_row_order: number }
export type CorrectionLine = { source_row_order: number; product_id?: UUID; upc?: string; ndc?: string; quantity?: number; unit_cost?: string; printed_line_total?: string }
export type Correction = { expected_revision: number; supplier_id?: UUID; invoice_number?: string; invoice_date?: string; purchase_order_reference?: string; printed_subtotal?: string; printed_tax?: string; printed_total?: string; lines?: CorrectionLine[] }
export type Approval = { expected_revision: number; location_id: UUID; allocations: Allocation[] }
export type ApprovalResult = { invoice_id: UUID; status: string; revision: number; authorization_id: UUID; approval_event_id: UUID; location_id: UUID; evidence: Record<string, unknown> }
export type PostingResult = { status: 'posted' | 'already_posted'; invoice_id: UUID; authorization_id: UUID; idempotency_key: string; movement_ids: UUID[] }
