import { Fragment, useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useBlocker } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useSession } from '../auth/Session'
import { ApiError } from '../api/client'
import type { Correction, CorrectionLine, Review } from '../types'
import { ConfirmDialog, ErrorMessage } from './Common'

const uuid = z.union([z.literal(''), z.uuid()])
const printedMoney = z.union([z.literal(''), z.string().regex(/^-?(0|[1-9]\d*)(\.\d{1,4})?$/, 'Use an exact decimal with up to four places.')])
const unitCost = z.union([z.literal(''), z.string().regex(/^(0|[1-9]\d*)(\.\d{1,4})?$/, 'Use a nonnegative decimal with up to four places.')])
const quantity = z.union([z.literal(''), z.string().regex(/^[1-9]\d*$/, 'Enter a positive whole number.').refine(value => Number.isSafeInteger(Number(value)), 'Quantity is too large.')])
const schema = z.object({ supplier_id: uuid, invoice_number: z.string().max(120), invoice_date: z.union([z.literal(''), z.iso.date()]), purchase_order_reference: z.string().max(120), printed_subtotal: printedMoney, printed_tax: printedMoney, printed_total: printedMoney, lines: z.array(z.object({ source_row_order: z.number(), product_id: uuid, upc: z.string(), ndc: z.string(), quantity, unit_cost: unitCost, printed_line_total: printedMoney })) })
type Values = z.infer<typeof schema>
const text = (value: unknown) => value == null ? '' : String(value)
function defaults(review: Review): Values { const current = review.current_extraction.invoice; return { supplier_id: review.invoice.supplier_id, invoice_number: text(current.invoice_number.value), invoice_date: text(current.invoice_date.value), purchase_order_reference: text(current.purchase_order_reference.value), printed_subtotal: text(current.printed_subtotal.value), printed_tax: text(current.printed_tax.value), printed_total: text(current.printed_total.value), lines: current.lines.map((line, index) => ({ source_row_order: line.source_row_order, product_id: review.invoice.lines[index]?.product_id || '', upc: text(line.upc.value), ndc: text(line.ndc.value), quantity: text(line.quantity.value), unit_cost: text(line.unit_cost.value), printed_line_total: text(line.printed_line_total.value) })) } }
function changes(before: Values, after: Values, revision: number): Correction | null {
  const payload: Correction = { expected_revision: revision }
  for (const key of ['supplier_id', 'invoice_number', 'invoice_date', 'purchase_order_reference', 'printed_subtotal', 'printed_tax', 'printed_total'] as const) if (before[key] !== after[key] && after[key] !== '') Object.assign(payload, { [key]: after[key] })
  const lines: CorrectionLine[] = []
  after.lines.forEach((line, index) => { const old = before.lines[index]; const changed: CorrectionLine = { source_row_order: line.source_row_order }; for (const key of ['product_id', 'upc', 'ndc', 'quantity', 'unit_cost', 'printed_line_total'] as const) if (line[key] !== old?.[key] && line[key] !== '') Object.assign(changed, { [key]: key === 'quantity' ? Number(line[key]) : line[key] }); if (Object.keys(changed).length > 1) lines.push(changed) })
  if (lines.length) payload.lines = lines
  return Object.keys(payload).length > 1 ? payload : null
}

export function CorrectionForm({ review }: { review: Review }) {
  const { client } = useSession()
  const queries = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  const [discard, setDiscard] = useState(false)
  const initial = useMemo(() => defaults(review), [review])
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: initial, mode: 'onBlur' })
  const { register, handleSubmit, reset, formState: { errors, isDirty } } = form
  const editable = review.invoice.status === 'review_required' || review.invoice.status === 'validated'
  const save = useMutation({ mutationFn: (payload: Correction) => client.correction(review.invoice.id, payload), onSuccess: async () => { setNotice('Correction saved. Validation and reconciliation have been refreshed.'); await Promise.all(['review', 'invoice', 'audit', 'invoices'].map(key => queries.invalidateQueries({ queryKey: [key] }))) }, onError: async error => { if (error instanceof ApiError && error.code === 'revision_conflict') { setNotice('The invoice changed. Current review data has been reloaded; reapply your correction.'); await queries.invalidateQueries({ queryKey: ['review', review.invoice.id] }) } } })
  useEffect(() => { reset(initial) }, [initial, reset])
  useEffect(() => { if (!isDirty) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn) }, [isDirty])
  const blocker = useBlocker(isDirty && !save.isPending)
  function submit(values: Values) { const payload = changes(initial, values, review.invoice.revision || 0); if (!payload) { setNotice('No changed values to save.'); return } setNotice(null); save.mutate(payload) }
  if (!editable) return <div className="notice">Corrections are read-only for {review.invoice.status.replaceAll('_', ' ')} invoices.</div>
  const headerFields = [
    ['supplier_id', 'Supplier ID', review.invoice.supplier_id], ['invoice_number', 'Invoice number', review.original_extraction.invoice.invoice_number.value],
    ['invoice_date', 'Invoice date', review.original_extraction.invoice.invoice_date.value], ['purchase_order_reference', 'PO reference', review.original_extraction.invoice.purchase_order_reference.value],
    ['printed_subtotal', 'Printed subtotal', review.original_extraction.invoice.printed_subtotal.value], ['printed_tax', 'Printed tax', review.original_extraction.invoice.printed_tax.value], ['printed_total', 'Printed total', review.original_extraction.invoice.printed_total.value],
  ] as const
  return <section className="panel"><div className="section-heading"><div><p className="eyebrow">Revision {review.invoice.revision}</p><h2>Staff corrections</h2></div><span className="muted">Only changed, nonempty values are submitted</span></div><p className="muted">Original observations remain immutable. A saved correction is attributed to authenticated staff and checked again against the source and trusted records.</p>{notice && <div className="notice success" role="status">{notice}</div>}<ErrorMessage error={save.error} /><form onSubmit={handleSubmit(submit)}><div className="correction-grid"><div className="correction-label heading">Field</div><div className="correction-label heading">Original extraction</div><div className="correction-label heading">Current / staff value</div>{headerFields.map(([key, label, original]) => <div className="correction-row" key={key}><label htmlFor={`correction-${key}`}>{label}</label><span className="original-value">{text(original) || '—'}</span><div><input id={`correction-${key}`} {...register(key)} aria-invalid={Boolean(errors[key])} /><small className="field-error">{errors[key]?.message}</small>{review.corrected_fields.includes(key) && <small className="staff-tag">Staff confirmed</small>}</div></div>)}</div><h3>Line items</h3><div className="table-scroll"><table className="correction-table"><thead><tr><th>Row / field</th><th>Original extraction</th><th>Current / staff value</th></tr></thead><tbody>{review.current_extraction.invoice.lines.map((line, index) => { const original = review.original_extraction.invoice.lines[index]; return <Fragment key={line.source_row_order}>{(['product_id', 'upc', 'ndc', 'quantity', 'unit_cost', 'printed_line_total'] as const).map(key => <tr key={key} className={key === 'product_id' ? 'line-group' : ''}><th scope="row">Row {line.source_row_order} · {key.replaceAll('_', ' ')}</th><td className="original-value">{key === 'product_id' ? 'Trusted catalog selection' : text(original?.[key].value) || '—'}</td><td><input aria-label={`Row ${line.source_row_order} ${key.replaceAll('_', ' ')}`} {...register(`lines.${index}.${key}`)} aria-invalid={Boolean(errors.lines?.[index]?.[key])} /><small className="field-error">{errors.lines?.[index]?.[key]?.message}</small>{review.corrected_fields.includes(`line[${line.source_row_order}].${key}`) && <small className="staff-tag">Staff confirmed</small>}</td></tr>)}</Fragment> })}</tbody></table></div><div className="button-row end"><button type="button" className="button ghost" disabled={!isDirty || save.isPending} onClick={() => setDiscard(true)}>Discard edits</button><button className="button primary" type="submit" disabled={!isDirty || save.isPending}>{save.isPending ? 'Saving…' : 'Save correction'}</button></div></form>{discard && <ConfirmDialog title="Discard unsaved corrections?" description="Your edits have not been sent to the API." confirmLabel="Discard edits" destructive onCancel={() => setDiscard(false)} onConfirm={() => { reset(initial); setDiscard(false) }} />}{blocker.state === 'blocked' && <ConfirmDialog title="Leave with unsaved corrections?" description="Unsaved edits will be lost when you leave this invoice." confirmLabel="Leave page" destructive onCancel={() => blocker.reset()} onConfirm={() => blocker.proceed()} />}</section>
}
