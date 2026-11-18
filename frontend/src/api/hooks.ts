import { useQuery } from '@tanstack/react-query'
import { useSession } from '../auth/Session'
import { retryRead } from './client'

export function useInvoices(params: { limit?: number; offset?: number; status?: string; supplier_id?: string }) {
  const { client } = useSession()
  return useQuery({ queryKey: ['invoices', params], queryFn: ({ signal }) => client.invoices(params, signal), retry: retryRead })
}
export function useInvoice(id: string) { const { client } = useSession(); return useQuery({ queryKey: ['invoice', id], queryFn: ({ signal }) => client.invoice(id, signal), retry: retryRead }) }
export function useReview(id: string) { const { client } = useSession(); return useQuery({ queryKey: ['review', id], queryFn: ({ signal }) => client.review(id, signal), retry: retryRead }) }
export function useProcessing(id: string) { const { client } = useSession(); return useQuery({ queryKey: ['processing', id], queryFn: ({ signal }) => client.processing(id, signal), retry: retryRead }) }
export function useAudit(id: string) { const { client } = useSession(); return useQuery({ queryKey: ['audit', id], queryFn: ({ signal }) => client.audit(id, signal), retry: retryRead }) }
export function useMovements(id: string) { const { client } = useSession(); return useQuery({ queryKey: ['movements', id], queryFn: ({ signal }) => client.movements(id, signal), retry: retryRead }) }
