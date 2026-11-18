import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAudit, useInvoice, useMovements, useProcessing, useReview } from '../api/hooks'
import { ErrorMessage, Loading, Status } from '../components/Common'
import { CorrectionForm } from '../components/CorrectionForm'
import { EvidenceViewer } from '../components/EvidenceViewer'
import { ReviewActions } from '../components/ReviewActions'
import type { Review } from '../types'

const tabs = [ ['overview', 'Overview'], ['evidence', 'PDF & OCR'], ['corrections', 'Corrections'], ['review', 'Review & decisions'], ['audit', 'Audit'], ['movements', 'Movements'] ] as const
type Tab = typeof tabs[number][0]
function value(value: unknown) { return value == null || value === '' ? '—' : String(value) }

function Overview({ review, provider, model }: { review: Review; provider?: string; model?: string }) {
  const original = review.original_extraction.invoice
  const current = review.current_extraction.invoice
  const fields = [
    ['Supplier', current.supplier_name.value, original.supplier_name.value, 'supplier_name'],
    ['Invoice number', current.invoice_number.value, original.invoice_number.value, 'invoice_number'],
    ['Invoice date', current.invoice_date.value, original.invoice_date.value, 'invoice_date'],
    ['Purchase order', current.purchase_order_reference.value, original.purchase_order_reference.value, 'purchase_order_reference'],
    ['Printed subtotal', current.printed_subtotal.value, original.printed_subtotal.value, 'printed_subtotal'],
    ['Printed tax', current.printed_tax.value, original.printed_tax.value, 'printed_tax'],
    ['Printed total', current.printed_total.value, original.printed_total.value, 'printed_total'],
  ] as const
  return <div className="detail-stack"><section className="panel"><div className="section-heading"><div><p className="eyebrow">Current record</p><h2>Invoice details</h2></div><Status value={review.invoice.status} /></div><div className="detail-grid"><div><span>Supplier ID</span><strong className="mono">{review.invoice.supplier_id}</strong></div><div><span>Source SHA-256</span><strong className="mono">{review.invoice.source_document?.sha256 || '—'}</strong></div><div><span>Processing provider</span><strong>{provider || review.original_extraction.provider}</strong></div><div><span>Model</span><strong>{model || review.original_extraction.model_name}</strong></div><div><span>Review revision</span><strong>{review.invoice.revision}</strong></div><div><span>Posting authorization</span><strong>{review.invoice.posting_status || 'Not authorized'}</strong></div></div></section><section className="panel"><h2>Source and reviewed values</h2><p className="muted">Original OCR extraction is shown alongside the current staff review. Staff-confirmed fields have no invented OCR citation.</p><div className="table-scroll"><table><thead><tr><th>Field</th><th>Original extraction</th><th>Current reviewed value</th><th>Provenance</th></tr></thead><tbody>{fields.map(([label, now, before, key]) => <tr key={key}><th scope="row">{label}</th><td className="original-value">{value(before)}</td><td className="mono">{value(now)}</td><td>{review.corrected_fields.includes(key) ? <span className="staff-tag">Staff confirmed</span> : <span className="source-tag">OCR extraction</span>}</td></tr>)}</tbody></table></div></section><section className="panel"><h2>Line items</h2><div className="table-scroll"><table><thead><tr><th>Row</th><th>Description</th><th>Identifier</th><th>Original qty</th><th>Reviewed qty</th><th>Unit cost</th><th>Printed line total</th><th>Catalog product</th></tr></thead><tbody>{current.lines.map((line, index) => { const source = original.lines[index]; const stored = review.invoice.lines[index]; return <tr key={line.source_row_order}><td>{line.source_row_order}</td><td>{value(line.description.value)}</td><td className="mono">{value(line.upc.value || line.ndc.value)}</td><td className="original-value">{value(source?.quantity.value)}</td><td><strong className="mono">{value(line.quantity.value)}</strong>{review.corrected_fields.includes(`line[${line.source_row_order}].quantity`) && <small className="staff-tag">Staff confirmed</small>}</td><td className="mono">{value(line.unit_cost.value)}</td><td className="mono">{value(line.printed_line_total.value)}</td><td className="mono compact">{stored?.product_id || 'Unresolved'}</td></tr> })}</tbody></table></div></section></div>
}

export function InvoicePage() {
  const { id = '' } = useParams()
  const [tab, setTab] = useState<Tab>('overview')
  const invoice = useInvoice(id)
  const review = useReview(id)
  const processing = useProcessing(id)
  const audit = useAudit(id)
  const movements = useMovements(id)
  if (invoice.isPending || review.isPending) return <Loading label="Opening invoice workspace…" />
  if (invoice.error || review.error) return <div className="page"><Link className="text-link" to="/invoices">← Back to invoices</Link><ErrorMessage error={invoice.error || review.error} /></div>
  const data = review.data
  const run = processing.data?.items.at(-1)
  return <div className="page"><div className="page-intro"><div><Link className="back-link" to="/invoices">← Invoice register</Link><p className="eyebrow">Invoice workspace · revision {data.invoice.revision}</p><h1>{data.invoice.invoice_number || 'Invoice number unavailable'}</h1><p className="muted">Review original observations, resolve findings, and keep approval separate from inventory posting.</p></div><div className="intro-status"><Status value={data.invoice.status} /><span className="mono">{data.invoice.total || 'Total unavailable'}</span></div></div><div className="tab-list" role="tablist" aria-label="Invoice workspace sections">{tabs.map(([key, label]) => <button type="button" key={key} className={`tab ${tab === key ? 'active' : ''}`} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>)}</div>
    <div hidden={tab !== 'overview'} role="tabpanel"><Overview review={data} provider={run?.provider} model={run?.model_name} /></div>
    <div hidden={tab !== 'evidence'} role="tabpanel"><EvidenceViewer invoiceId={id} review={data} /></div>
    <div hidden={tab !== 'corrections'} role="tabpanel"><CorrectionForm review={data} /></div>
    <div hidden={tab !== 'review'} role="tabpanel"><ReviewActions review={data} /></div>
    <div hidden={tab !== 'audit'} role="tabpanel"><section className="panel"><h2>Audit history</h2>{audit.isPending ? <Loading /> : audit.error ? <ErrorMessage error={audit.error} /> : audit.data.items.length ? <ol className="audit-list">{audit.data.items.map(event => <li key={event.id}><span className="audit-marker" /><div><strong>{event.action.replaceAll('_', ' ')}</strong><span className="muted">{new Date(event.created_at).toLocaleString()}</span><p>{event.actor_type === 'staff' ? 'Verified staff' : 'System'}{event.actor_id ? ` · ${event.actor_id}` : ''}</p><details><summary>Recorded details</summary><pre>{JSON.stringify(event.after, null, 2)}</pre></details></div></li>)}</ol> : <p className="muted">No audit events have been recorded.</p>}</section></div>
    <div hidden={tab !== 'movements'} role="tabpanel"><section className="panel"><h2>Receipt movements</h2>{movements.isPending ? <Loading /> : movements.error ? <ErrorMessage error={movements.error} /> : movements.data.items.length ? <div className="table-scroll"><table><thead><tr><th>Movement</th><th>Type</th><th>Quantity</th><th>Unit cost</th><th>PO line</th><th>Location</th></tr></thead><tbody>{movements.data.items.map(item => <tr key={item.id}><td className="mono compact">{item.id}</td><td>{item.movement_type}</td><td className="mono">{item.quantity_delta}</td><td className="mono">{item.unit_cost || '—'}</td><td className="mono compact">{item.purchase_order_line_id || '—'}</td><td className="mono compact">{item.location_id}</td></tr>)}</tbody></table></div> : <p className="muted">No inventory movements are linked to this invoice.</p>}</section></div>
  </div>
}
