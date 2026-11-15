import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { setTokenProvider } from './lib/auth'
import './index.css'

const queryClient = new QueryClient()

if (import.meta.env.VITE_ENTRA_CLIENT_ID) {
  const { createMsalProvider } = await import('./lib/msal')
  const provider = await createMsalProvider(import.meta.env)
  if (provider) setTokenProvider(provider)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
