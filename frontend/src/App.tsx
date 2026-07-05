import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { NumbersPage } from './pages/NumbersPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { ConversationDetail } from './pages/ConversationDetail'
import { BotsPage, SkillsPage, ProvidersPage, RoutesPage, ContactsPage, GroupsPage } from './pages/CrudPages'

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
        <Route path="conversations" element={<ConversationsPage />} />
        <Route path="conversations/:id" element={<ConversationDetail />} />
        <Route path="bots" element={<BotsPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="providers" element={<ProvidersPage />} />
        <Route path="routes" element={<RoutesPage />} />
        <Route path="contacts" element={<ContactsPage />} />
        <Route path="groups" element={<GroupsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
