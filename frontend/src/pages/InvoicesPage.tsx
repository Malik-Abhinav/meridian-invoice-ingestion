import { useState } from 'react'
import { useInvoices } from '../api/hooks'
import { Empty, ErrorMessage, InvoiceTable, Loading } from '../components/Common'

const statuses = ['', 'uploaded', 'extracted', 'review_required', 'validated', 'approved', 'posted', 'rejected', 'failed']
export function InvoicesPage() {
  const [page, setPage] = useState(0)
  const [status, setStatus] = useState('')
  const [supplier, setSupplier] = useState('')
  const [search, setSearch] = useState('')
  const limit = 20
  const invoices = useInvoices({ limit, offset: page * limit, status: status || undefined, supplier_id: supplier || undefined })
  const rows = invoices.data?.items.filter(item => !search || (item.invoice_number || '').toLowerCase().includes(search.toLowerCase())) || []
  return <div className="page"><div className="page-intro"><div><p className="eyebrow">Invoice register</p><h1>Invoices</h1><p className="muted">Search applies to the current page. API order is creation time, then invoice ID.</p></div></div>
    <div className="filters panel"><label>Status<select value={status} onChange={event => { setStatus(event.target.value); setPage(0) }}>{statuses.map(value => <option value={value} key={value}>{value ? value.replaceAll('_', ' ') : 'All statuses'}</option>)}</select></label><label>Supplier ID<input value={supplier} onChange={event => { setSupplier(event.target.value); setPage(0) }} placeholder="Exact UUID" /></label><label>Invoice number on this page<input value={search} onChange={event => setSearch(event.target.value)} placeholder="Filter visible rows" /></label></div>
    <section className="panel"><div className="section-heading"><h2>Invoice records</h2><span className="muted">{invoices.data?.pagination.total ?? '—'} matching records</span></div>{invoices.isPending ? <Loading /> : invoices.error ? <ErrorMessage error={invoices.error} /> : rows.length ? <InvoiceTable items={rows} /> : <Empty title="No invoices on this page">Adjust filters or upload a new PDF.</Empty>}
      <div className="pagination"><button className="button ghost" disabled={page === 0 || invoices.isPending} onClick={() => setPage(page - 1)}>← Previous</button><span>Page {page + 1} · {invoices.data ? `${Math.min(page * limit + 1, invoices.data.pagination.total)}–${Math.min((page + 1) * limit, invoices.data.pagination.total)} of ${invoices.data.pagination.total}` : 'Loading'}</span><button className="button ghost" disabled={invoices.isPending || (page + 1) * limit >= (invoices.data?.pagination.total || 0)} onClick={() => setPage(page + 1)}>Next →</button></div>
    </section></div>
}

export function ReviewQueuePage() {
  const invoices = useInvoices({ limit: 100, offset: 0 })
  const rows = invoices.data?.items.filter(item => item.status === 'review_required' || (item.status === 'validated' && (item.validation?.findings.some(finding => finding.manual_review_required !== false) || item.reconciliation?.findings.some(finding => finding.manual_review_required !== false) || item.reconciliation?.unperformed_checks.length))) || []
  return <div className="page"><div className="page-intro"><div><p className="eyebrow">Human action</p><h1>Review queue</h1><p className="muted">Invoices with review-required lifecycle state or unresolved findings in the first 100 API records.</p></div></div><div className="scope-note">Queue shows the first 100 records in deterministic API order. An invoice is never considered ready to post from its status alone; open its review to see current eligibility.</div><section className="panel">{invoices.isPending ? <Loading /> : invoices.error ? <ErrorMessage error={invoices.error} /> : rows.length ? <InvoiceTable items={rows} /> : <Empty title="No review actions in this sample">The queue is clear for the fetched records.</Empty>}</section></div>
}
