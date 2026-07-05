import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { fetchMe, logout as doLogout, isLoggedIn } from '../api/auth'
import type { Principal } from '../api/types'

interface AuthState {
  principal: Principal | null
  loading: boolean
  logout: () => void
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState>(undefined as unknown as AuthState)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null)
  const [loading, setLoading] = useState<boolean>(true)

  const load = async () => {
    if (!isLoggedIn()) {
      setPrincipal(null)
      setLoading(false)
      return
    }
    try {
      const me = await fetchMe()
      setPrincipal(me)
    } catch {
      doLogout()
      setPrincipal(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const logout = () => {
    doLogout()
    setPrincipal(null)
  }

  return (
    <AuthContext.Provider value={{ principal, loading, logout, refresh: load }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
