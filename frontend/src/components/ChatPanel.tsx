import { useState, useEffect, useRef } from 'react'
import { api } from '../api'
import type { ChatMessage } from '../types'

function AqlView({ aql }: { aql: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className="aql-toggle" onClick={() => setOpen(o => !o)}>
        {open ? '▾ Hide AQL' : '▸ Show AQL'}
      </button>
      {open && <pre className="aql-block">{aql}</pre>}
    </>
  )
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'user') {
    return (
      <div className="msg msg-user">
        <div className="msg-bubble">{msg.text}</div>
      </div>
    )
  }
  return (
    <div className="msg msg-assistant">
      <div className="msg-bubble">{msg.text}</div>
      {msg.error && <div className="msg-error">⚠ {msg.error}</div>}
      {msg.aql && <AqlView aql={msg.aql} />}
    </div>
  )
}

export function ChatPanel() {
  const [messages, setMessages]     = useState<ChatMessage[]>([])
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [input, setInput]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [aiStatus, setAiStatus]     = useState<'ok' | 'down' | 'nc'>('nc')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.suggestions().then(setSuggestions).catch(() => {})
    fetch('/api/health')
      .then(r => r.json())
      .then((h: { txt2aql: string }) => {
        if (h.txt2aql === 'ok') setAiStatus('ok')
        else if (h.txt2aql === 'not_configured') setAiStatus('nc')
        else setAiStatus('down')
      })
      .catch(() => setAiStatus('down'))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function send(q: string) {
    if (!q.trim() || loading) return
    setInput('')
    setMessages(m => [...m, { role: 'user', text: q }])
    setLoading(true)
    try {
      const res = await api.ask(q)
      setMessages(m => [...m, {
        role:  'assistant',
        text:  res.answer ?? 'No response.',
        aql:   res.aql,
        error: res.error,
      }])
    } catch {
      setMessages(m => [...m, { role: 'assistant', text: 'Request failed.', error: 'network_error' }])
    }
    setLoading(false)
  }

  const statusLabel = aiStatus === 'ok' ? 'AI Query' : aiStatus === 'nc' ? 'AI (not configured)' : 'AI (unavailable)'
  const dotClass    = aiStatus === 'ok' ? '' : aiStatus === 'nc' ? 'nc' : 'down'

  return (
    <aside className="chat-panel">
      <div className="chat-header">
        <span className={`chat-dot ${dotClass}`} />
        {statusLabel}
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty-state">Ask a question about the fleet or select a suggestion below.</div>
        )}
        {messages.map((m, i) => <MessageBubble key={i} msg={m} />)}
        {loading && (
          <div className="msg msg-assistant">
            <div className="msg-bubble"><span className="spinner" /></div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {suggestions.length > 0 && !messages.length && (
        <div className="chat-suggestions">
          {suggestions.map((s, i) => (
            <button key={i} className="chip" onClick={() => send(s)}>{s}</button>
          ))}
        </div>
      )}

      <div className="chat-input-row">
        <input
          className="chat-input"
          placeholder="Ask about the fleet…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send(input)}
          disabled={loading}
        />
        <button className="btn btn-primary btn-sm" onClick={() => send(input)} disabled={loading}>
          Ask
        </button>
      </div>
    </aside>
  )
}
