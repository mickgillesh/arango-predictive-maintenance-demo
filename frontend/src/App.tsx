import { Outlet } from 'react-router-dom'
import { ChatPanel } from './components/ChatPanel'

export default function App() {
  return (
    <div className="layout">
      <main className="main-area">
        <Outlet />
      </main>
      <ChatPanel />
    </div>
  )
}
