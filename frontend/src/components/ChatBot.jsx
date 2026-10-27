import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

const STARTERS = ['How is Bellandur?', 'Was Hebbal drained?', 'Which lakes need attention?', 'How accurate is this?']

// Small "Ask AquaRevive" chat bubble. Answers come only from the project's own data.
export default function ChatBot() {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState([{ from: 'bot', text: 'Hi! Ask me about any well-known Bengaluru lake.' }])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [context, setContext] = useState({})
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, open])

  async function send(text) {
    const q = (text ?? input).trim()
    if (!q || busy) return
    setInput('')
    setMsgs((m) => [...m, { from: 'you', text: q }])
    setBusy(true)
    try {
      const history = msgs.slice(1).slice(-6).map((m) => ({ role: m.from === 'you' ? 'user' : 'assistant', content: m.text }))
      const r = await api.chat(q, context, history)
      if (r.lake_ids.length) setContext({ lake_ids: r.lake_ids, topic: r.topic })
      setMsgs((m) => [...m, { from: 'bot', text: r.answer, link: r.link?.url, ai: r.mode === 'ai' }])
    } catch (e) {
      setMsgs((m) => [...m, { from: 'bot', text: `Sorry, I can't reach the data right now (${e.message}).` }])
    } finally {
      setBusy(false)
    }
  }

  if (!open) return <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Open chat">💬 Ask AquaRevive</button>

  return (
    <div className="chat-box">
      <div className="chat-head">
        <strong>Ask AquaRevive</strong>
        <button className="chat-x" onClick={() => setOpen(false)} aria-label="Close chat">×</button>
      </div>
      <div className="chat-body">
        {msgs.map((m, i) => (
          <div key={i} className={`chat-msg ${m.from}`}>
            {m.text}
            {m.link && <div><Link to={m.link} onClick={() => setOpen(false)}>See details →</Link></div>}
            {m.ai && <div className="chat-tag">AI answer · every number checked against the data</div>}
          </div>
        ))}
        {msgs.length === 1 && (
          <div className="chat-starters">
            {STARTERS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {busy && <div className="chat-msg bot muted"><span className="spinner" />Looking at the data…</div>}
        <div ref={end} />
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send() }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about a lake…" maxLength={300} />
        <button className="btn small" disabled={busy || !input.trim()}>Send</button>
      </form>
    </div>
  )
}
