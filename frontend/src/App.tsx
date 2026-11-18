import { lazy, Suspense } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { createBrowserRouter, NavLink, Outlet, RouterProvider, useLocation } from 'react-router-dom'
import { SessionProvider, TokenGate, useSession } from './auth/Session'
import { retryRead } from './api/client'
const DashboardPage = lazy(() => import('./pages/DashboardPage').then(module => ({ default: module.DashboardPage })))
const InvoicesPage = lazy(() => import('./pages/InvoicesPage').then(module => ({ default: module.InvoicesPage })))
const ReviewQueuePage = lazy(() => import('./pages/InvoicesPage').then(module => ({ default: module.ReviewQueuePage })))
const UploadPage = lazy(() => import('./pages/UploadPage').then(module => ({ default: module.UploadPage })))
const InvoicePage = lazy(() => import('./pages/InvoicePage').then(module => ({ default: module.InvoicePage })))

export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: retryRead }, mutations: { retry: false } } })

function Connection() {
  const result = useQuery({ queryKey: ['connection'], queryFn: async ({ signal }) => {
    const response = await fetch(`${(import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')}/ready`, { signal })
    return response.ok
  }, refetchInterval: 30_000, retry: false })
  return <span className={`connection ${result.data ? 'online' : 'offline'}`} role="status"><span className="connection-dot" /> API {result.isPending ? 'checking' : result.data ? 'connected' : 'unavailable'}</span>
}

function Shell() {
  const { token, signOut } = useSession()
  const location = useLocation()
  if (!token) return <TokenGate />
  const links = [
    { path: '/', label: 'Dashboard', icon: '◫' },
    { path: '/invoices', label: 'Invoices', icon: '▤' },
    { path: '/review-queue', label: 'Review queue', icon: '◈' },
    { path: '/upload', label: 'Upload invoice', icon: '⇧' },
  ]
  return <div className="app-shell"><aside className="sidebar"><div className="sidebar-brand"><div className="brand-mark">M</div><div><strong>Meridian</strong><small>Supplier operations</small></div></div><p className="sidebar-caption">WORKSPACE</p><nav aria-label="Primary navigation">{links.map(link => <NavLink key={link.path} to={link.path} end={link.path === '/'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><span className="nav-icon" aria-hidden>{link.icon}</span>{link.label}</NavLink>)}</nav><div className="sidebar-foot"><span className="foot-icon">●</span><div><strong>Staff session</strong><small>Bearer token · memory only</small></div></div></aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb">Operations <span>/</span> {location.pathname.startsWith('/invoices/') ? 'Invoice detail' : links.find(item => item.path === location.pathname)?.label || 'Page'}</div><div className="top-actions"><Connection /><span className="staff-indicator" aria-label="Authenticated staff session">Staff access</span><button className="button text" onClick={signOut}>Sign out</button></div></header><main id="main-content" className="main-content"><Suspense fallback={<div role="status">Loading workspace page…</div>}><Outlet /></Suspense></main></div></div>
}

export const router = createBrowserRouter([{ path: '/', element: <Shell />, children: [
  { index: true, element: <DashboardPage /> },
  { path: 'invoices', element: <InvoicesPage /> },
  { path: 'review-queue', element: <ReviewQueuePage /> },
  { path: 'upload', element: <UploadPage /> },
  { path: 'invoices/:id', element: <InvoicePage /> },
  { path: '*', element: <div className="page"><p className="eyebrow">404</p><h1>Page not found</h1><p className="muted">The requested workspace page does not exist.</p><NavLink to="/" className="button primary">Return to dashboard</NavLink></div> },
] }])

export default function App() { return <QueryClientProvider client={queryClient}><SessionProvider><RouterProvider router={router} /></SessionProvider></QueryClientProvider> }
