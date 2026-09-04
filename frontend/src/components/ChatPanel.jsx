import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { getLocale } from '../lib/util'

function fmtTime(iso) {
  if (!iso) return ''
  const norm = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'
  const d = new Date(norm)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString(getLocale(), { hour: '2-digit', minute: '2-digit' })
}

function kindBadge(kind, isBot, t) {
  const label = isBot ? t('chat.bot') : kind === 'channel' ? t('chat.channel') : kind === 'group' ? t('chat.group') : t('chat.chat')
  const color = isBot ? 'bg-brand-violet' : kind === 'channel' ? 'bg-brand-info'
    : kind === 'group' ? 'bg-brand-ok' : 'bg-brand-warn'
  return <span className={'nb-badge text-black ' + color}>{label}</span>
}

export default function ChatPanel({ account, className = '' }) {
  const { t } = useTranslation()
  const toast = useToast()
  const [input, setInput] = useState('')
  const [peer, setPeer] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [opening, setOpening] = useState(false)
  const [sending, setSending] = useState(false)
  const listRef = useRef(null)

  // Reset everything when the selected account changes — the chat is opened
  // from that account's perspective.
  useEffect(() => {
    setInput(''); setPeer(null); setMessages([]); setDraft('')
    setOpening(false); setSending(false)
  }, [account?.id])

  // Poll history every 4s while a chat is open (incoming replies, referral acks).
  useEffect(() => {
    if (!account?.id || !peer?.ref) return
    let alive = true
    const tick = async () => {
      try {
        const r = await Endpoints.chatHistory(account.id, peer.ref)
        if (alive && r?.messages) setMessages(r.messages)
      } catch { /* silent — transient */ }
    }
    const id = setInterval(tick, 4000)
    return () => { alive = false; clearInterval(id) }
  }, [account?.id, peer?.ref])

  // Auto-scroll to newest.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  async function openChat() {
    const val = input.trim()
    if (!val || !account?.id) return
    setOpening(true)
    try {
      const r = await Endpoints.openChat(account.id, val)
      setPeer(r.peer)
      setMessages(r.messages || [])
      if (r.started) toast.success(t('messaging.referralSent'))
    } catch (e) {
      toast.error(e.message)
    } finally {
      setOpening(false)
    }
  }

  async function send() {
    const text = draft.trim()
    if (!text || !peer?.ref || !account?.id) return
    setSending(true)
    try {
      await Endpoints.chatSend(account.id, peer.ref, text)
      setDraft('')
      const r = await Endpoints.chatHistory(account.id, peer.ref)
      if (r?.messages) setMessages(r.messages)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSending(false)
    }
  }

  function newChat() {
    setPeer(null); setMessages([]); setInput(''); setDraft('')
  }

  if (!account) return null
  const notConnected = account.status !== 'connected'

  return (
    <div className={'nb-card flex flex-col h-[75vh] ' + className}>
      {/* header */}
      <div className="flex items-center gap-2 p-2 border-b-2 border-black dark:border-white">
        <span className="font-extrabold uppercase tracking-tight text-sm shrink-0">{t('chat.chat')}</span>
        {peer && (
          <>
            <span className="font-bold truncate">{peer.title}</span>
            {kindBadge(peer.kind, peer.is_bot, t)}
            <button className="nb-btn !py-0.5 !px-2 ml-auto shrink-0" onClick={newChat} title={t('chat.openAnother')}>
              {t('chat.new')}
            </button>
          </>
        )}
      </div>

      {/* open bar — paste @username or t.me link (referral links supported) */}
      <div className="p-2 border-b-2 border-black dark:border-white space-y-1">
        <div className="flex gap-2">
          <input
            className="nb-input !py-1 text-sm"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); openChat() } }}
            placeholder={t('chat.openPlaceholder')}
          />
          <button className="nb-btn-pri !py-1 !px-3 shrink-0" onClick={openChat} disabled={opening || !input.trim()}>
            {opening ? '…' : t('chat.open')}
          </button>
        </div>
        <div className="text-[10px] opacity-50">
          {t('chat.tip')}
        </div>
      </div>

      {notConnected && (
        <div className="px-2 py-1 text-[11px] bg-brand-warn text-black border-b-2 border-black dark:border-white">
          {t('chat.notConnected', { status: account.status })}
        </div>
      )}

      {/* messages */}
      <div ref={listRef} className="flex-1 overflow-auto p-2 space-y-1 bg-zinc-50 dark:bg-zinc-950/40">
        {!peer && (
          <div className="h-full flex items-center justify-center text-center text-xs opacity-50 px-4">
            {t('chat.emptyOpen')}
          </div>
        )}
        {peer && messages.length === 0 && (
          <div className="h-full flex items-center justify-center text-xs opacity-50">{t('chat.noMessages')}</div>
        )}
        {peer && messages.map((m) => {
          if (m.service) {
            return (
              <div key={m.id} className="text-center text-[10px] opacity-50 my-1">
                {m.text || t('chat.serviceMessage')}
              </div>
            )
          }
          return (
            <div key={m.id} className={'flex ' + (m.out ? 'justify-end' : 'justify-start')}>
              <div className={
                'max-w-[80%] px-2 py-1 border-2 border-black dark:border-white text-sm break-words ' +
                (m.out ? 'bg-brand-pri text-black' : 'bg-white dark:bg-zinc-800')
              }>
                {m.text
                  ? <span className="whitespace-pre-wrap">{m.text}</span>
                  : <span className="italic opacity-70">{m.media || t('chat.emptyMedia')}</span>}
                <div className={'text-[9px] mt-0.5 text-right ' + (m.out ? 'text-black/50' : 'opacity-50')}>
                  {fmtTime(m.date)}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* composer */}
      {peer && (
        <div className="p-2 border-t-2 border-black dark:border-white flex gap-2">
          <input
            className="nb-input !py-1 text-sm"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder={t('chat.typeMsg')}
          />
          <button className="nb-btn-pri !py-1 !px-3 shrink-0" onClick={send} disabled={sending || !draft.trim()}>
            {sending ? '…' : t('chat.send')}
          </button>
        </div>
      )}
    </div>
  )
}
