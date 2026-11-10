import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'

interface AuthUser { email: string; name: string; picture?: string }

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null)

  useEffect(() => {
    fetch('/auth/me')
      .then(r => (r.ok ? r.json() : null))
      .then((d: ({ authenticated: boolean } & AuthUser) | null) => {
        if (d?.authenticated) setUser({ email: d.email, name: d.name, picture: d.picture })
      })
      .catch(() => {})
  }, [])

  return (
    <div className="layout">
      {user && (
        <div className="auth-bar">
          {user.picture && <img src={user.picture} alt="" className="auth-avatar" referrerPolicy="no-referrer" />}
          <span className="auth-name">{user.name || user.email}</span>
          <a href="/auth/logout" className="auth-logout">Sign out</a>
        </div>
      )}
      <main className="main-area">
        <Outlet />
      </main>
    </div>
  )
}
