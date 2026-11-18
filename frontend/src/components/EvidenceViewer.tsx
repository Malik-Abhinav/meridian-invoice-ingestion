import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSession } from '../auth/Session'
import { retryRead } from '../api/client'
import { ErrorMessage, Loading } from './Common'
import type { EvidenceRef, FieldValue, Review } from '../types'

type CitedField = { label: string; value: string; evidence: EvidenceRef[]; staffConfirmed: boolean }
function display(value: unknown): string { return value == null || value === '' ? '—' : String(value) }

export function EvidenceViewer({ invoiceId, review }: { invoiceId: string; review: Review }) {
  const { client } = useSession()
  const document = useQuery({ queryKey: ['document', invoiceId], queryFn: ({ signal }) => client.document(invoiceId, signal), retry: retryRead })
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(100)
  const [selected, setSelected] = useState(0)
  useEffect(() => { if (!document.data) return; const url = URL.createObjectURL(document.data); setBlobUrl(url); return () => { URL.revokeObjectURL(url); setBlobUrl(null) } }, [document.data])
  const fields = useMemo(() => {
    const original = review.original_extraction.invoice
    const current = review.current_extraction.invoice
    const list: CitedField[] = []
    const append = (label: string, currentField: FieldValue<unknown>, sourceField: FieldValue<unknown>, key: string) => list.push({ label, value: display(currentField.value), evidence: sourceField.evidence || [], staffConfirmed: review.corrected_fields.includes(key) })
    for (const key of ['supplier_name', 'invoice_number', 'invoice_date', 'purchase_order_reference', 'printed_subtotal', 'printed_tax', 'printed_total'] as const) append(key.replaceAll('_', ' '), current[key], original[key], key)
    current.lines.forEach((line, index) => { const source = original.lines[index]; if (!source) return; for (const key of ['description', 'upc', 'ndc', 'quantity', 'unit_cost', 'printed_line_total'] as const) append(`Row ${line.source_row_order} · ${key.replaceAll('_', ' ')}`, line[key], source[key], `line[${line.source_row_order}].${key}`) })
    return list
  }, [review])
  const active = fields[selected] || fields[0]
  const citations = active?.evidence || []
  const currentPage = review.ocr.pages.find(item => item.page_number === page)
  const citedIndexes = new Set(citations.filter(item => item.page_number === page).flatMap(item => item.word_indexes))
  return <div className="evidence-layout"><section className="panel pdf-panel"><div className="section-heading"><div><p className="eyebrow">Authenticated source</p><h2>Original PDF</h2></div><span className="muted">{review.ocr.pages.length} page{review.ocr.pages.length === 1 ? '' : 's'}</span></div><div className="viewer-controls"><button className="button ghost" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous PDF page">←</button><span>Page {page} / {review.ocr.pages.length}</span><button className="button ghost" disabled={page >= review.ocr.pages.length} onClick={() => setPage(page + 1)} aria-label="Next PDF page">→</button><label>Zoom<select aria-label="PDF zoom" value={zoom} onChange={event => setZoom(Number(event.target.value))}><option value={75}>75%</option><option value={100}>100%</option><option value={125}>125%</option><option value={150}>150%</option></select></label></div>{document.isPending ? <Loading label="Loading authenticated PDF…" /> : document.error ? <ErrorMessage error={document.error} /> : blobUrl ? <iframe className="pdf-frame" title="Authenticated source invoice PDF" src={`${blobUrl}#page=${page}&zoom=${zoom}`} /> : <p className="muted">PDF preview is unavailable.</p>}</section>
    <section className="panel ocr-panel"><p className="eyebrow">Source observation</p><h2>OCR citation inspector</h2><p className="muted">Select a field to inspect its original cited words. A citation identifies source text; it does not prove the value was assigned correctly.</p><label htmlFor="evidence-field">Invoice field</label><select id="evidence-field" value={selected} onChange={event => { const index = Number(event.target.value); setSelected(index); const next = fields[index]?.evidence[0]?.page_number; if (next) setPage(next) }}>{fields.map((field, index) => <option key={`${field.label}-${index}`} value={index}>{field.label}</option>)}</select><div className="evidence-value"><span>Current value</span><strong>{active?.value || '—'}</strong><small>{active?.staffConfirmed ? 'Staff-confirmed value · original OCR citation shown below' : 'Automated extraction · original OCR citation'}</small></div>{citations.length ? citations.map((citation, index) => { const source = review.ocr.pages.find(item => item.page_number === citation.page_number); const words = citation.word_indexes.map(wordIndex => source?.words[wordIndex]).filter(Boolean); return <div className="citation" key={`${citation.page_number}-${index}`}><strong>Page {citation.page_number} · {words.length} cited words</strong><div className="word-chips">{words.map((word, wordIndex) => word && <span key={wordIndex} className="word-chip">{word.text}<small>{word.confidence.toFixed(0)}%</small></span>)}</div></div> }) : <div className="notice warning">No OCR words were cited for this field. Staff-confirmed values remain separate from source evidence.</div>}<h3>Page {page} OCR words</h3><div className="ocr-words">{currentPage?.words.map((word, index) => <span key={index} className={citedIndexes.has(index) ? 'highlighted' : ''} title={`OCR confidence ${word.confidence.toFixed(0)}%`}>{word.text}</span>)}</div></section></div>
}
