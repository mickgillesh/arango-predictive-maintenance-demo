import { Outlet } from 'react-router-dom'

export default function App() {
  return (
    <div className="layout">
      <main className="main-area">
        <Outlet />
      </main>
    </div>
  )
}
