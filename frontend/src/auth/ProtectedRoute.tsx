import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'
import { Spinner } from '../components/Spinner'

export function ProtectedRoute({ children }: { children: JSX.Element }) {
  const { principal, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Spinner fullPage />
  if (!principal) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}
