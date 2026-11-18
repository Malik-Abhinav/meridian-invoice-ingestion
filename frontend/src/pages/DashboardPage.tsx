import { Link } from 'react-router-dom'
import { useInvoices } from '../api/hooks'
import { Empty, ErrorMessage, InvoiceTable, Loading, SectionHeading } from '../components/Common'

export function DashboardPage() {
  const invoices = useInvoices({ limit: 100, offset: 0 })
  if (invoices.isPending) return <Loading label="Loading operations overview…" />
  if (invoices.error) return <ErrorMessage error={invoices.error} />
  const rows = invoices.data.items
  const statuses = [...new Set(rows.map(item => item.status))].sort()
  const review = rows.filter(item => item.status === 'review_required' || item.validation?.findings.some(finding => finding.manual_review_required !== false) || item.reconciliation?.findings.some(finding => finding.manual_review_required !== false) || item.reconciliation?.unperformed_checks.length)
  const approved = rows.filter(item => item.status === 'approved' && item.posting_status === 'authorized')
  const recent = rows.slice(-5).reverse()
  const failures = rows.filter(item => item.status === 'failed' || item.processing_status === 'failed').slice(-5).reverse()
  return <div className="page"><div className="page-intro"><div><p className="eyebrow">Operations overview</p><h1>Invoice command center</h1><p className="muted">A clear view of the work moving from source document to controlled posting.</p></div><Link className="button primary" to="/upload">＋ Upload invoice</Link></div>
    <div className="scope-note">Global invoice total: <strong>{invoices.data.pagination.total}</strong>. Status and action counts below cover the first <strong>{rows.length}</strong> invoices in API order (up to 100), not the entire database.</div>
    <div className="metric-grid"><div className="metric"><span>Fetched invoices</span><strong>{rows.length}</strong><small>of {invoices.data.pagination.total} total</small></div><div className="metric"><span>Human review signals</span><strong>{review.length}</strong><small>in fetched sample</small></div><div className="metric"><span>Approved, unposted</span><strong>{approved.length}</strong><small>in fetched sample</small></div><div className="metric"><span>Recent failures</span><strong>{failures.length}</strong><small>in fetched sample</small></div></div>
    <div className="dashboard-columns"><section className="panel"><SectionHeading eyebrow="Lifecycle" title="Status distribution" /><div className="status-bars">{statuses.length ? statuses.map(status => <div className="status-bar" key={status}><span>{status.replaceAll('_', ' ')}</span><div className="bar-track"><div style={{ width: `${Math.max(4, rows.filter(item => item.status === status).length / Math.max(rows.length, 1) * 100)}%` }} /></div><strong>{rows.filter(item => item.status === status).length}</strong></div>) : <Empty title="No invoices yet">Upload a PDF to start the workflow.</Empty>}</div></section><section className="panel"><SectionHeading eyebrow="Attention" title="Recent failures" />{failures.length ? failures.map(item => <p className="activity-item" key={item.id}><Link to={`/invoices/${item.id}`}>{item.invoice_number || item.id}</Link><small>{item.processing_status || item.status}</small></p>) : <Empty title="No failed invoices in this sample" />}</section></div>
    <section className="panel"><SectionHeading eyebrow="Latest in fetched sample" title="Recently processed" aside={<Link className="text-link" to="/invoices">View all invoices →</Link>} />{recent.length ? <InvoiceTable items={recent} /> : <Empty title="No invoices to show" />}</section>
  </div>
}
