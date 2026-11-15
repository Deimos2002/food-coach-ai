import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes, useNavigate, useParams, useLocation } from 'react-router-dom'
import { DayPage } from './features/day/DayPage'
import { NewProductForm } from './features/products/NewProductForm'
import { ProductPage } from './features/products/ProductPage'
import { SavedMealsPage } from './features/saved/SavedMealsPage'
import { AliasesPage } from './features/saved/AliasesPage'
import { ProfilePage } from './features/profile/ProfilePage'
import { MePage } from './features/me/MePage'
import { AdvancedPage } from './features/me/AdvancedPage'
import { ProgressPage } from './features/progress/ProgressPage'
import { localDay } from './lib/dates'
import type { PickedProduct } from './features/add/ProductSearch'
import { AddPage } from './features/add/AddPage'
import { CoachPage } from './features/coach/CoachPage'
import { AppShell } from './ui/AppShell'

const queryClient = new QueryClient()

function AddRoute() {
  const { day = localDay(), meal = '' } = useParams<{ day: string; meal: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const initialProduct = (location.state as { initialProduct?: PickedProduct } | null)?.initialProduct
  const mealId = Number(meal)
  if (!/^[1-9]\d*$/.test(meal) || !Number.isSafeInteger(mealId)) {
    return <Navigate to={`/day/${day}`} replace />
  }
  return <AddPage initialProduct={initialProduct} mealId={mealId} day={day} onClose={() => navigate(`/day/${day}`)} />
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DayPage />} />
      <Route path="/day/:day" element={<DayPage />} />
      <Route path="/add/:day/:meal" element={<AddRoute />} />
      <Route path="/progress" element={<ProgressPage />} />
      <Route path="/week" element={<Navigate to="/progress" replace />} />
      <Route path="/week/:isoWeek" element={<Navigate to="/progress" replace />} />
      <Route path="/weight" element={<Navigate to="/progress" replace />} />
      <Route path="/coach" element={<CoachPage />} />
      <Route path="/me" element={<MePage />} />
      <Route path="/me/advanced" element={<AdvancedPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="/saved" element={<SavedMealsPage />} />
      <Route path="/aliases" element={<AliasesPage />} />
      <Route path="/products/new" element={<NewProductForm />} />
      <Route path="/products/:id" element={<ProductPage />} />
      <Route path="/settings" element={<Navigate to="/me" replace />} />
    </Routes>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell>
          <AppRoutes />
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
