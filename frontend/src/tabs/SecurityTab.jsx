import { useEffect, useState, useCallback } from 'react'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { fmtTime } from '../lib/util'
import ProgressModal from '../components/ProgressModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'

const TYPE_COLORS = {
  login_code:       'bg-brand-warn',
  new_login:        'bg-brand-err',
  '2fa_change':     'bg-brand-violet',
  account_deletion: 'bg-brand-err',
  unknown:          'bg-white',
}

function AccountRow({ account, onChange }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState([])
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const m = await Endpoints.securityMessages(account.id)
      setMsgs(m)
      try {
        const s = await Endpoints.tgSessions(account.id)
        setSessions(s)
      } catch { setSessions([]) }
    } catch (e) { toast.error(e.message) } finally { setLoading(false) }
  }, [account.id, toast])

  useEffect(() => { if (open) load() }, [open, load])

  async function markRead(id) {
    try { await Endpoints.markRead(id); await load(); onChange?.() } catch (e) { toast.error(e.message) }
  }
  async function markAllRead() {
    try { await Endpoints.markAllRead(account.id); await load(); onChange?.() } catch (e) { toast.error(e.message) }
  }
  async function killSession(hash) {
    if (!confirm('Terminate this session?')) return
    try { await Endpoints.terminateSession(account.id, hash); await load() } catch (e) { toast.error(e.message) }
  }
  async function killOthers() {
    if (!confirm('Terminate ALL other sessions?')) return
    try { await Endpoints.terminateOthers(account.id); await load() } catch (e) { toast.error(e.message) }
  }

  async function backfill() {
    setLoading(true)
    try {
      await Endpoints.backfillSecurity(account.id, 50)
      await load()
      toast.success('Pulled latest messages from Telegram')
    } catch (e) { toast.error(e.message) } finally { setLoading(false) }
  }

  return (
    <div className="nb-card-sm mb-3">
      <div
        className="px-4 py-3 flex items-center gap-3 cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="font-bold flex-1">
          {(account.first_name + ' ' + account.last_name).trim() || account.phone}
        </span>
        {account.has_2fa
          ? <span className="nb-badge bg-brand-violet text-black">2FA on</span>
          : <span className="nb-badge bg-brand-warn text-black">2FA off</span>}
        {account.unread_security > 0 && (
          <span className="nb-badge bg-brand-err text-black">{account.unread_security} new</span>
        )}
        <span className="opacity-60 text-sm">{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="px-4 pb-4 border-t-2 border-black dark:border-white">
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm uppercase">Telegram service messages</span>
            <span className="text-[10px] opacity-60">from "Telegram" (+42777, user_id 777000)</span>
            <button className="nb-btn !py-1 !px-2 text-xs ml-auto" onClick={backfill} disabled={loading}>
              {loading ? '…' : 'Pull latest 50'}
            </button>
            <button className="nb-btn !py-1 !px-2 text-xs" onClick={markAllRead}>Mark all read</button>
            <button className="nb-btn !py-1 !px-2 text-xs" onClick={load}>Refresh</button>
          </div>
          {loading && <div className="text-sm opacity-60 mt-2">Loading…</div>}
          {!loading && msgs.length === 0 && (
            <div className="text-sm opacity-60 mt-2">
              No messages from Telegram yet. Try "Pull latest 50" to fetch history from Telegram.
            </div>
          )}
          <div className="space-y-2 mt-2">
            {msgs.map((m) => (
              <div key={m.id} className={'nb-card-sm p-3 ' + (m.is_read ? 'opacity-60' : '')}>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`nb-badge ${TYPE_COLORS[m.type] || 'bg-white'} text-black`}>{m.type}</span>
                  {!m.is_read && <span className="w-2 h-2 rounded-full bg-brand-err inline-block" />}
                  <span className="text-xs opacity-70 ml-auto">{fmtTime(m.received_at)}</span>
                </div>
                <div className={'whitespace-pre-wrap text-sm font-mono ' + (m.is_read ? '' : 'font-bold')}>
                  {m.message_text}
                </div>
                {!m.is_read && (
                  <button className="nb-btn !py-1 !px-2 text-xs mt-2" onClick={() => markRead(m.id)}>
                    Mark as read
                  </button>
                )}
              </div>
            ))}
          </div>

          <div className="mt-5 flex items-center gap-2">
            <span className="font-bold text-sm uppercase">Active sessions</span>
            <button className="nb-btn-err !py-1 !px-2 text-xs ml-auto" onClick={killOthers}>Terminate all others</button>
          </div>
          <div className="space-y-2 mt-2">
            {sessions.length === 0 && <div className="text-sm opacity-60">No sessions data.</div>}
            {sessions.map((s) => (
              <div key={s.hash} className="nb-card-sm p-3 flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">
                    {s.device || s.app_name || 'Unknown'} {s.is_current && <span className="nb-badge bg-brand-ok text-black ml-1">current</span>}
                  </div>
                  <div className="text-xs opacity-70 truncate">
                    {s.platform} • {s.ip} • {s.country} • {fmtTime(s.date_created)}
                  </div>
                </div>
                {!s.is_current && (
                  <button className="nb-btn-err !py-1 !px-2 text-xs" onClick={() => killSession(s.hash)}>
                    Terminate
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Bulk change/set the Two-Step (2FA) password across many accounts at once.
function Bulk2faPanel({ accounts, onChange }) {
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()
  const [open, setOpen] = useState(false)
  const [ids, setIds] = useState([])
  const [newPwd, setNewPwd] = useState('')
  const [newPwd2, setNewPwd2] = useState('')
  const [hint, setHint] = useState('')
  const [bank, setBank] = useState([])        // current-password attempt bank (max 5)
  const [bankInput, setBankInput] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [knownCount, setKnownCount] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    Endpoints.twofaKnown().then((r) => setKnownCount(r?.count ?? 0)).catch(() => setKnownCount(null))
  }, [open])

  const allChecked = ids.length === accounts.length && accounts.length > 0
  const toggleAll = () => setIds(allChecked ? [] : accounts.map((a) => a.id))
  const toggle = (id) => setIds((arr) => arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id])

  function addBank() {
    const p = bankInput.trim()
    if (!p) return
    if (bank.length >= 5) { toast.error('Max 5 current passwords'); return }
    if (bank.includes(p)) { toast.info('Password already added'); setBankInput(''); return }
    setBank((arr) => [...arr, p]); setBankInput('')
  }
  const removeBank = (p) => setBank((arr) => arr.filter((x) => x !== p))

  async function start() {
    if (ids.length === 0) { toast.error('Pick at least one account'); return }
    if (!newPwd) { toast.error('Enter the new 2FA password'); return }
    if (newPwd.trim() !== newPwd2.trim()) { toast.error('New passwords do not match'); return }
    if (!confirm(
      `Set/change the Two-Step password on ${ids.length} account(s)?\n\n` +
      `For accounts that already have 2FA, your remembered passwords` +
      `${bank.length ? ` and ${bank.length} entered password(s)` : ''} are tried (up to 5 per account).`
    )) return
    setBusy(true)
    await run(`Bulk 2FA — ${ids.length} account(s)`, (onEvent) =>
      Endpoints.bulk2fa({ account_ids: ids, new_password: newPwd, hint, password_bank: bank }, onEvent))
    setBusy(false)
    onChange?.()  // refresh 2FA counts
  }

  return (
    <div className="nb-card p-4 mb-4">
      <div className="flex items-center gap-2 cursor-pointer" onClick={() => setOpen((o) => !o)}>
        <span className="font-extrabold uppercase">Bulk Two-Step (2FA) Password</span>
        <span className="nb-badge bg-brand-violet text-black">change / set on many</span>
        <span className="opacity-60 text-sm ml-auto">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="mt-3 space-y-3">
          <div className="text-xs opacity-70">
            Sets a new Two-Step password on every selected account. Accounts <b>without</b> 2FA get it
            turned on. Accounts that <b>already have</b> 2FA need their current password — we try each
            account's remembered password first (saved when you logged in), then the list below, at most
            5 tries per account.
            {knownCount != null && <> We currently remember <b>{knownCount}</b> password{knownCount === 1 ? '' : 's'} from login.</>}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label>
              <div className="text-xs font-bold uppercase mb-1">New 2FA password</div>
              <input type={showPwd ? 'text' : 'password'} className="nb-input" value={newPwd}
                onChange={(e) => setNewPwd(e.target.value)} placeholder="new password for all" />
            </label>
            <label>
              <div className="text-xs font-bold uppercase mb-1">Confirm new password</div>
              <input type={showPwd ? 'text' : 'password'} className="nb-input" value={newPwd2}
                onChange={(e) => setNewPwd2(e.target.value)} placeholder="repeat new password" />
            </label>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={showPwd} onChange={(e) => setShowPwd(e.target.checked)} />
              Show passwords
            </label>
            <label className="flex items-center gap-2 text-xs flex-1 min-w-[180px]">
              <span className="font-bold uppercase">Hint (optional)</span>
              <input className="nb-input !py-1" maxLength={20} value={hint}
                onChange={(e) => setHint(e.target.value)} placeholder="max 20 chars" />
            </label>
          </div>

          {/* current-password attempt bank */}
          <div className="nb-card-sm p-3">
            <div className="text-xs font-bold uppercase mb-1">Current passwords to try (max 5)</div>
            <div className="text-[11px] opacity-60 mb-2">
              For accounts that already have 2FA with a password we don't remember. Tried in order until one works.
            </div>
            <div className="flex gap-2 mb-2">
              <input type={showPwd ? 'text' : 'password'} className="nb-input !py-1 text-sm" value={bankInput}
                onChange={(e) => setBankInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addBank() } }}
                placeholder="add a current password" disabled={bank.length >= 5} />
              <button className="nb-btn !px-3" onClick={addBank} disabled={bank.length >= 5}>Add</button>
            </div>
            <div className="flex flex-wrap gap-1">
              {bank.length === 0 && <span className="text-[11px] opacity-50">No passwords added yet.</span>}
              {bank.map((p, i) => (
                <span key={i} className="nb-badge bg-white text-black flex items-center gap-1">
                  <span className="font-mono text-[11px] normal-case">{showPwd ? p : '•'.repeat(Math.min(p.length, 8))}</span>
                  <button className="opacity-60 hover:opacity-100" onClick={() => removeBank(p)}>✕</button>
                </span>
              ))}
            </div>
          </div>

          {/* account picker */}
          <div className="nb-card-sm p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-bold uppercase">Accounts</span>
              <button className="nb-btn !py-0.5 !px-2 text-[11px]" onClick={toggleAll}>{allChecked ? 'Clear' : 'Select all'}</button>
              <span className="text-xs opacity-70 ml-auto">{ids.length} selected</span>
            </div>
            <div className="flex flex-wrap gap-1 max-h-40 overflow-auto">
              {accounts.map((a) => (
                <label key={a.id} className={'nb-badge cursor-pointer flex items-center gap-1 ' + (ids.includes(a.id) ? 'bg-brand-pri text-black' : 'bg-white text-black')}>
                  <input type="checkbox" checked={ids.includes(a.id)} onChange={() => toggle(a.id)} />
                  <span>{((a.first_name || '') + ' ' + (a.last_name || '')).trim() || a.phone}</span>
                  {a.has_2fa
                    ? <span className="text-[8px] font-bold text-brand-violet" title="already has 2FA">2FA</span>
                    : <span className="text-[8px] font-bold opacity-40" title="no 2FA yet">off</span>}
                </label>
              ))}
            </div>
          </div>

          <button className="nb-btn-pri w-full" disabled={busy} onClick={start}>
            {busy ? 'Working…' : `Change / Set 2FA on ${ids.length} account(s)`}
          </button>
        </div>
      )}

      <ProgressModal progress={progress} onClose={close} />
    </div>
  )
}

function BulkTerminateSessionsPanel({ accounts, onChange }) {
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()
  const [busy, setBusy] = useState(false)
  const accountCount = accounts.length

  async function start() {
    if (accountCount === 0) {
      toast.error('No accounts available')
      return
    }
    if (!confirm(
      `Terminate other Telegram sessions on ${accountCount} account(s)?\n\n` +
      `The current session used by this app is kept active for each account. ` +
      `Your Multi TG Manager website login will not be logged out.`
    )) return

    setBusy(true)
    await run(`Terminate sessions - ${accountCount} account(s)`, (onEvent) =>
      Endpoints.terminateOthersAll(onEvent))
    setBusy(false)
    onChange?.()
  }

  return (
    <div className="nb-card p-4 mb-4">
      <div className="flex items-start sm:items-center gap-3 flex-col sm:flex-row">
        <div className="flex-1">
          <div className="font-extrabold uppercase">All Accounts Sessions</div>
          <div className="text-sm opacity-70">
            One click will remove every other Telegram login/device from every connected account, while keeping this app's current Telegram session active.
          </div>
        </div>
        <button className="nb-btn-err w-full sm:w-auto" disabled={busy || accountCount === 0} onClick={start}>
          {busy ? 'Working...' : `Terminate Others on All (${accountCount})`}
        </button>
      </div>

      <ProgressModal progress={progress} onClose={close} />
    </div>
  )
}

export default function SecurityTab({ accounts, onChange }) {
  return (
    <div>
      <div className="nb-card p-4 mb-4">
        <div className="font-extrabold uppercase">Security Center</div>
        <div className="text-sm opacity-70">
          All messages from the official Telegram service account (shown in your phone as <b>"Telegram"</b> / <b>+42777</b>, internal user_id <b>777000</b>) — per account. New messages also trigger a desktop notification. Use "Pull latest 50" to backfill history for a newly added account.
        </div>
      </div>
      <BulkTerminateSessionsPanel accounts={accounts} onChange={onChange} />
      <Bulk2faPanel accounts={accounts} onChange={onChange} />
      {accounts.length === 0 && <div className="opacity-60">No accounts.</div>}
      {accounts.map((a) => <AccountRow key={a.id} account={a} onChange={onChange} />)}
    </div>
  )
}
