import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { fmtTime } from '../lib/util'
import ProgressModal from '../components/ProgressModal.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'

const TYPE_COLORS = {
  login_code:       'bg-brand-warn',
  new_login:        'bg-brand-err',
  '2fa_change':     'bg-brand-violet',
  account_deletion: 'bg-brand-err',
  unknown:          'bg-white',
}

function AccountRow({ account, onChange }) {
  const { t } = useTranslation()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState([])
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)
  const [killConfirm, setKillConfirm] = useState(null) // {type:'one',hash} | {type:'all'}

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
    await Endpoints.terminateSession(account.id, hash); await load()
  }
  async function killOthers() {
    await Endpoints.terminateOthers(account.id); await load()
  }

  async function backfill() {
    setLoading(true)
    try {
      await Endpoints.backfillSecurity(account.id, 50)
      await load()
      toast.success(t('security.pulledLatest'))
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
          ? <span className="nb-badge bg-brand-violet text-black">{t('security.twoFaOn')}</span>
          : <span className="nb-badge bg-brand-warn text-black">{t('security.twoFaOff')}</span>}
        {account.unread_security > 0 && (
          <span className="nb-badge bg-brand-err text-black">{t('security.new', { count: account.unread_security })}</span>
        )}
        <span className="opacity-60 text-sm">{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="px-4 pb-4 border-t-2 border-black dark:border-white">
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm uppercase">{t('security.serviceMessages')}</span>
            <span className="text-[10px] opacity-60">{t('security.fromTelegram')}</span>
            <button className="nb-btn !py-1 !px-2 text-xs ml-auto" onClick={backfill} disabled={loading}>
              {loading ? '…' : t('security.pullLatest')}
            </button>
            <button className="nb-btn !py-1 !px-2 text-xs" onClick={markAllRead}>{t('security.markAllRead')}</button>
            <button className="nb-btn !py-1 !px-2 text-xs" onClick={load}>{t('security.refresh')}</button>
          </div>
          {loading && <div className="text-sm opacity-60 mt-2">{t('security.loading')}</div>}
          {!loading && msgs.length === 0 && (
            <div className="text-sm opacity-60 mt-2">
              {t('security.noMessages')}
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
                    {t('security.markAsRead')}
                  </button>
                )}
              </div>
            ))}
          </div>

          <div className="mt-5 flex items-center gap-2">
            <span className="font-bold text-sm uppercase">{t('security.activeSessions')}</span>
            <button className="nb-btn-err !py-1 !px-2 text-xs ml-auto" onClick={() => setKillConfirm({ type: 'all' })}>{t('security.terminateAllOthers')}</button>
          </div>
          <div className="space-y-2 mt-2">
            {sessions.length === 0 && <div className="text-sm opacity-60">{t('security.noSessions')}</div>}
            {sessions.map((s) => (
              <div key={s.hash} className="nb-card-sm p-3 flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">
                    {s.device || s.app_name || t('security.unknownDevice')} {s.is_current && <span className="nb-badge bg-brand-ok text-black ml-1">{t('common.current')}</span>}
                  </div>
                  <div className="text-xs opacity-70 truncate">
                    {s.platform} • {s.ip} • {s.country} • {fmtTime(s.date_created)}
                  </div>
                </div>
                {!s.is_current && (
                  <button className="nb-btn-err !py-1 !px-2 text-xs" onClick={() => setKillConfirm({ type: 'one', hash: s.hash })}>
                    {t('security.terminate')}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {killConfirm?.type === 'one' && (
        <ConfirmModal
          title={t('security.terminateSessionConfirm')}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          danger
          onConfirm={() => { const h = killConfirm.hash; setKillConfirm(null); killSession(h) }}
          onCancel={() => setKillConfirm(null)}
        />
      )}
      {killConfirm?.type === 'all' && (
        <ConfirmModal
          title={t('security.terminateOthersConfirm')}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          danger
          onConfirm={() => { setKillConfirm(null); killOthers() }}
          onCancel={() => setKillConfirm(null)}
        />
      )}
    </div>
  )
}

// Bulk change/set the Two-Step (2FA) password across many accounts at once.
function Bulk2faPanel({ accounts, onChange }) {
  const { t } = useTranslation()
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
  const [confirmOpen, setConfirmOpen] = useState(false)

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
    if (bank.length >= 5) { toast.error(t('security.max5Pwds')); return }
    if (bank.includes(p)) { toast.info(t('security.pwdAlreadyAdded')); setBankInput(''); return }
    setBank((arr) => [...arr, p]); setBankInput('')
  }
  const removeBank = (p) => setBank((arr) => arr.filter((x) => x !== p))

  async function start() {
    if (ids.length === 0) { toast.error(t('security.pickAccount')); return }
    if (!newPwd) { toast.error(t('security.enterNewPwd')); return }
    if (newPwd.trim() !== newPwd2.trim()) { toast.error(t('security.pwdsNoMatch')); return }
    if (!confirmOpen) { setConfirmOpen(true); return }
    setConfirmOpen(false)
    setBusy(true)
    await run(t('security.bulk2faTitle', { count: ids.length }), (onEvent) =>
      Endpoints.bulk2fa({ account_ids: ids, new_password: newPwd, hint, password_bank: bank }, onEvent))
    setBusy(false)
    onChange?.()  // refresh 2FA counts
  }

  return (
    <div className="nb-card p-4 mb-4">
      <div className="flex items-center gap-2 cursor-pointer" onClick={() => setOpen((o) => !o)}>
        <span className="font-extrabold uppercase">{t('security.bulk2faPanel')}</span>
        <span className="nb-badge bg-brand-violet text-black">{t('security.changeSetTooMany')}</span>
        <span className="opacity-60 text-sm ml-auto">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="mt-3 space-y-3">
          <div className="text-xs opacity-70">
            {t('security.bulk2faDesc')}
            {knownCount != null && <> {t('security.rememberedCount', { count: knownCount })}</>}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label>
              <div className="text-xs font-bold uppercase mb-1">{t('security.new2faPwd')}</div>
              <input type={showPwd ? 'text' : 'password'} className="nb-input" value={newPwd}
                onChange={(e) => setNewPwd(e.target.value)} placeholder={t('security.newPwdPlaceholder')} />
            </label>
            <label>
              <div className="text-xs font-bold uppercase mb-1">{t('security.confirmNewPwd')}</div>
              <input type={showPwd ? 'text' : 'password'} className="nb-input" value={newPwd2}
                onChange={(e) => setNewPwd2(e.target.value)} placeholder={t('security.confirmPwdPlaceholder')} />
            </label>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={showPwd} onChange={(e) => setShowPwd(e.target.checked)} />
              {t('security.showPasswords')}
            </label>
            <label className="flex items-center gap-2 text-xs flex-1 min-w-[180px]">
              <span className="font-bold uppercase">{t('security.hintOptional')}</span>
              <input className="nb-input !py-1" maxLength={20} value={hint}
                onChange={(e) => setHint(e.target.value)} placeholder={t('security.hintPlaceholder')} />
            </label>
          </div>

          {/* current-password attempt bank */}
          <div className="nb-card-sm p-3">
            <div className="text-xs font-bold uppercase mb-1">{t('security.currentPwdsToTry')}</div>
            <div className="text-[11px] opacity-60 mb-2">
              {t('security.currentPwdsHint')}
            </div>
            <div className="flex gap-2 mb-2">
              <input type={showPwd ? 'text' : 'password'} className="nb-input !py-1 text-sm" value={bankInput}
                onChange={(e) => setBankInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addBank() } }}
                placeholder={t('security.addPwdPlaceholder')} disabled={bank.length >= 5} />
              <button className="nb-btn !px-3" onClick={addBank} disabled={bank.length >= 5}>{t('security.add')}</button>
            </div>
            <div className="flex flex-wrap gap-1">
              {bank.length === 0 && <span className="text-[11px] opacity-50">{t('security.noPwdsAdded')}</span>}
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
              <span className="text-xs font-bold uppercase">{t('security.accounts')}</span>
              <button className="nb-btn !py-0.5 !px-2 text-[11px]" onClick={toggleAll}>{allChecked ? t('common.clear') : t('common.selectAll')}</button>
              <span className="text-xs opacity-70 ml-auto">{t('common.selected', { count: ids.length })}</span>
            </div>
            <div className="flex flex-wrap gap-1 max-h-40 overflow-auto">
              {accounts.map((a) => (
                <label key={a.id} className={'nb-badge cursor-pointer flex items-center gap-1 ' + (ids.includes(a.id) ? 'bg-brand-pri text-black' : 'bg-white text-black')}>
                  <input type="checkbox" checked={ids.includes(a.id)} onChange={() => toggle(a.id)} />
                  <span>{((a.first_name || '') + ' ' + (a.last_name || '')).trim() || a.phone}</span>
                  {a.has_2fa
                    ? <span className="text-[8px] font-bold text-brand-violet" title={t('security.alreadyHas2fa')}>2FA</span>
                    : <span className="text-[8px] font-bold opacity-40" title={t('security.no2faYet')}>off</span>}
                </label>
              ))}
            </div>
          </div>

          <button className="nb-btn-pri w-full" disabled={busy} onClick={start}>
            {busy ? t('common.working') : t('security.changeSet2faBtn', { count: ids.length })}
          </button>
        </div>
      )}

      <ProgressModal progress={progress} onClose={close} />

      {confirmOpen && (
        <ConfirmModal
          title={t('security.bulk2faTitle', { count: ids.length })}
          message={t('security.bulk2faConfirm', {
            count: ids.length,
            bank: bank.length ? t('security.bulk2faConfirmBank', { count: bank.length }) : '',
          })}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          onConfirm={() => start()}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </div>
  )
}

function BulkTerminateSessionsPanel({ accounts, onChange }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()
  const [busy, setBusy] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const accountCount = accounts.length

  async function start() {
    if (accountCount === 0) {
      toast.error(t('security.noAccountsAvailable'))
      return
    }
    if (!confirmOpen) { setConfirmOpen(true); return }
    setConfirmOpen(false)
    setBusy(true)
    await run(t('security.terminateSessionTitle', { count: accountCount }), (onEvent) =>
      Endpoints.terminateOthersAll(onEvent))
    setBusy(false)
    onChange?.()
  }

  return (
    <div className="nb-card p-4 mb-4">
      <div className="flex items-start sm:items-center gap-3 flex-col sm:flex-row">
        <div className="flex-1">
          <div className="font-extrabold uppercase">{t('security.allAccountsSessions')}</div>
          <div className="text-sm opacity-70">
            {t('security.allSessionsDesc')}
          </div>
        </div>
        <button className="nb-btn-err w-full sm:w-auto" disabled={busy || accountCount === 0} onClick={start}>
          {busy ? t('common.working') : t('security.terminateOthersOnAll', { count: accountCount })}
        </button>
      </div>

      <ProgressModal progress={progress} onClose={close} />

      {confirmOpen && (
        <ConfirmModal
          title={t('security.terminateSessionTitle', { count: accountCount })}
          message={t('security.terminateAllConfirm', { count: accountCount })}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          danger
          onConfirm={() => start()}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </div>
  )
}

export default function SecurityTab({ accounts, onChange }) {
  const { t } = useTranslation()
  return (
    <div>
      <div className="nb-card p-4 mb-4">
        <div className="font-extrabold uppercase">{t('security.title')}</div>
        <div className="text-sm opacity-70">
          {t('security.desc')}
        </div>
      </div>
      <BulkTerminateSessionsPanel accounts={accounts} onChange={onChange} />
      <Bulk2faPanel accounts={accounts} onChange={onChange} />
      {accounts.length === 0 && <div className="opacity-60">{t('security.noAccounts')}</div>}
      {accounts.map((a) => <AccountRow key={a.id} account={a} onChange={onChange} />)}
    </div>
  )
}
