import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { NumbersPage } from './pages/NumbersPage'
import { ComingSoon } from './pages/ComingSoon'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="numbers" element={<NumbersPage />} />
        <Route path="conversations" element={<ComingSoon name="Conversations" />} />
        <Route path="conversations/:id" element={<ComingSoon name="Conversation" />} />
        <Route path="bots" element={<ComingSoon name="Bots" />} />
        <Route path="skills" element={<ComingSoon name="Skills" />} />
        <Route path="providers" element={<ComingSoon name="Providers" />} />
        <Route path="routes" element={<ComingSoon name="Routes" />} />
        <Route path="contacts" element={<ComingSoon name="Contacts" />} />
        <Route path="groups" element={<ComingSoon name="Groups" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
