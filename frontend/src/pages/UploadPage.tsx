import { useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useSession } from '../auth/Session'
import { ApiError, retryRead } from '../api/client'
import { Empty, ErrorMessage, Loading, Status } from '../components/Common'
import type { ProcessingAttempt, Upload } from '../types'

const configuredLimit = Number(import.meta.env.VITE_MAX_UPLOAD_BYTES || 12 * 1024 * 1024)
function checkPdf(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.pdf') || (file.type && file.type !== 'application/pdf')) return 'Choose a PDF file.'
  if (file.size === 0) return 'Choose a nonempty PDF.'
  if (file.size > configuredLimit) return `The file exceeds the configured browser limit of ${(configuredLimit / 1024 / 1024).toFixed(0)} MiB.`
  return null
}

export function UploadPage() {
  const { client } = useSession()
  const queries = useQueryClient()
  const picker = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState<Upload | null>(null)
  const [attemptInput, setAttemptInput] = useState('')
  const [result, setResult] = useState<ProcessingAttempt | null>(null)
  const [dragging, setDragging] = useState(false)
  const upload = useMutation({ mutationFn: (pdf: File) => client.upload(pdf), onSuccess: data => { setAttempt(data); setAttemptInput(data.upload_attempt_id); setResult(null); queries.invalidateQueries({ queryKey: ['invoices'] }) } })
  const process = useMutation({ mutationFn: ({ id, retry }: { id: string; retry: boolean }) => client.processUpload(id, retry), onSuccess: data => { setResult(data); queries.invalidateQueries({ queryKey: ['invoices'] }); queries.invalidateQueries({ queryKey: ['upload-processing', attemptInput] }) } })
  const processing = useQuery({ queryKey: ['upload-processing', attemptInput], enabled: Boolean(attemptInput), queryFn: ({ signal }) => client.uploadProcessing(attemptInput, signal), retry: retryRead })
  function choose(next: File | undefined) { if (!next) return; const issue = checkPdf(next); setFileError(issue); setFile(issue ? null : next); setAttempt(null); setResult(null); upload.reset(); process.reset() }
  function drop(event: DragEvent) { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files[0]) }
  function picked(event: ChangeEvent<HTMLInputElement>) { choose(event.target.files?.[0]); event.target.value = '' }
  const current = result || processing.data?.items.at(-1) || null
  return <div className="page"><div className="page-intro"><div><p className="eyebrow">Source intake</p><h1>Upload an invoice</h1><p className="muted">Keep the source document intact, then start processing as a separate action.</p></div></div><div className="two-column"><section className="panel"><h2>1 · Add source PDF</h2><p className="muted">The API validates PDF contents and enforces its own upload limit. Browser guidance: {(configuredLimit / 1024 / 1024).toFixed(0)} MiB.</p><div className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={drop} role="group" aria-label="PDF upload area"><span className="drop-icon">⇧</span><strong>Drop a PDF here</strong><span>or choose a file from your device</span><button className="button ghost" type="button" onClick={() => picker.current?.click()}>Choose PDF</button><input ref={picker} type="file" accept=".pdf,application/pdf" onChange={picked} className="sr-only" aria-label="Choose invoice PDF" /></div>{file && <div className="file-chip"><strong>{file.name}</strong><span>{(file.size / 1024).toFixed(1)} KiB</span></div>}{fileError && <p className="field-error" role="alert">{fileError}</p>}<ErrorMessage error={upload.error} /><button className="button primary" disabled={!file || upload.isPending} onClick={() => file && upload.mutate(file)}>{upload.isPending ? 'Uploading…' : 'Upload PDF'}</button><p className="small muted">Upload progress is indeterminate because the browser Fetch API does not expose upload progress here.</p></section>
      <section className="panel"><h2>2 · Process upload attempt</h2>{attempt && <div className="notice success" role="status">Source stored. Document hash: <code>{attempt.source_sha256}</code>. Identity is <strong>{attempt.identity_status}</strong>; an invoice exists only if the API returns an invoice ID.</div>}<label htmlFor="attempt-id">Upload-attempt ID</label><input id="attempt-id" value={attemptInput} onChange={event => { setAttemptInput(event.target.value.trim()); setResult(null) }} placeholder="Paste an upload-attempt UUID" /><div className="button-row"><button className="button primary" disabled={!attemptInput || process.isPending} onClick={() => process.mutate({ id: attemptInput, retry: false })}>{process.isPending ? 'Processing…' : 'Process invoice'}</button>{current?.status === 'failed' && <button className="button ghost" disabled={process.isPending} onClick={() => process.mutate({ id: attemptInput, retry: true })}>Explicit retry</button>}</div><ErrorMessage error={process.error} />{processing.isPending && attemptInput && <Loading label="Loading saved attempts…" />}{processing.error && <ErrorMessage error={processing.error} />}{current && <div className="processing-result"><div><span className="eyebrow">Durable processing</span><Status value={current.status} /></div><p>Provider: <strong>{current.provider}</strong> · Model: <strong>{current.model_name}</strong></p><p>Attempts: {current.attempt_count}{current.failure && ` · ${current.failure.code}`}</p>{current.invoice_id ? <Link className="button primary" to={`/invoices/${current.invoice_id}`}>Open invoice →</Link> : <div className="notice warning">No canonical invoice is linked. Supplier identity or document conflict may need a separate manual workflow. The upload remains inspectable by attempt ID.</div>}{current.validation && <p className="small muted">Local findings: {current.validation.findings.length} · Trusted findings: {current.reconciliation?.findings.length ?? '—'}</p>}</div>}{!current && !attemptInput && <Empty title="Waiting for an upload">Upload a PDF or enter a prior attempt ID.</Empty>}</section></div></div>
}
