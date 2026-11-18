import { useEffect, useRef, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import type { Finding, Invoice } from '../types'

export function Status({ value }: { value: string | null | undefined }) { return <span className={`status ${String(value || 'unknown').replace(/[^a-z_]/g, '')}`}>{String(value || 'unknown').replaceAll('_', ' ')}</span> }
export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null
  const api = error instanceof ApiError ? error : null
  return <div className="notice error" role="alert"><strong>{api?.code === 'revision_conflict' ? 'This invoice changed.' : 'Request could not be completed.'}</strong> {error instanceof Error ? error.message : 'Please try again.'}{api?.correlationId && <span className="reference">Reference: {api.correlationId}</span>}</div>
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty"><div className="empty-icon">◇</div><h3>{title}</h3>{children && <p>{children}</p>}</div> }
export function Loading({ label = 'Loading…' }: { label?: string }) { return <div className="loading" role="status"><span className="spinner" />{label}</div> }
export function SectionHeading({ eyebrow, title, aside }: { eyebrow?: string; title: string; aside?: ReactNode }) { return <div className="section-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2></div>{aside}</div> }
export function Findings({ title, findings, empty = 'No findings.' }: { title: string; findings: Finding[]; empty?: string }) {
  return <section className="panel"><h3>{title} <span className="muted count">{findings.length}</span></h3>{findings.length === 0 ? <p className="muted">{empty}</p> : <div className="finding-list">{findings.map((finding, index) => <div className="finding" key={`${finding.code}-${index}`}><span className={`finding-dot ${finding.severity || 'warning'}`} /><div><strong>{finding.code.replaceAll('_', ' ')}</strong><p>{finding.message || finding.detail || 'Review this finding against the source and trusted records.'}</p><small>{finding.field?.source_row_order != null ? `Row ${finding.field.source_row_order} · ` : ''}{finding.field?.field || 'Invoice'}{finding.manual_review_required === false ? ' · informational' : ' · review required'}</small></div></div>)}</div>}</section>
}
export function InvoiceTable({ items }: { items: Invoice[] }) {
  return <div className="table-scroll"><table><thead><tr><th>Invoice</th><th>Supplier ID</th><th>Date</th><th>Status</th><th>Review</th><th>Total</th><th /></tr></thead><tbody>{items.map(invoice => <tr key={invoice.id}><td><strong>{invoice.invoice_number || 'Number unavailable'}</strong><small className="cell-sub">{invoice.id}</small></td><td className="mono compact">{invoice.supplier_id}</td><td>{invoice.invoice_date || '—'}</td><td><Status value={invoice.status} /></td><td>{invoice.validation?.findings?.filter(item => item.manual_review_required !== false).length || 0} local · {invoice.reconciliation?.findings?.filter(item => item.manual_review_required !== false).length || 0} trusted</td><td className="mono">{invoice.total ?? '—'}</td><td><Link className="text-link" to={`/invoices/${invoice.id}`}>Open <span aria-hidden>→</span></Link></td></tr>)}</tbody></table></div>
}

export function ConfirmDialog({ title, description, confirmLabel, destructive = false, onConfirm, onCancel, busy = false, children }: { title: string; description: string; confirmLabel: string; destructive?: boolean; onConfirm: () => void; onCancel: () => void; busy?: boolean; children?: ReactNode }) {
  const dialog = useRef<HTMLDivElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    cancel.current?.focus()
    function keys(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) onCancel()
      if (event.key === 'Tab') {
        const focusable = [...(dialog.current?.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href]') || [])].filter(item => !item.hasAttribute('disabled'))
        if (!focusable.length) return
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', keys)
    return () => { document.removeEventListener('keydown', keys); previous?.focus() }
  }, [busy, onCancel])
  return <div className="dialog-backdrop" role="presentation"><div className="dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="dialog-title" aria-describedby="dialog-description"><p className="eyebrow">Staff confirmation</p><h2 id="dialog-title">{title}</h2><p id="dialog-description">{description}</p>{children}<div className="dialog-actions"><button ref={cancel} className="button ghost" onClick={onCancel} disabled={busy}>Cancel</button><button className={`button ${destructive ? 'danger' : 'primary'}`} onClick={onConfirm} disabled={busy}>{busy ? 'Working…' : confirmLabel}</button></div></div></div>
}
