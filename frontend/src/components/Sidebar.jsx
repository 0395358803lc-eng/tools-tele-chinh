import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CopyButton } from '../lib/CopyButton'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import AccountAvatar from './AccountAvatar'
import ConfirmModal from './ConfirmModal'
import { getLocale } from '../lib/util'

function ago(iso, t) {
  if (!iso) return ''
  const norm = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'
  const tm = new Date(norm).getTime()
  if (Number.isNaN(tm)) return ''
  const s = Math.max(0, (Date.now() - tm) / 1000)
  if (s < 60) return t('time.justNow')
  const m = s / 60; if (m < 60) return t('time.minutesAgo', { count: Math.floor(m) })
  const h = m / 60; if (h < 24) return t('time.hoursAgo', { count: Math.floor(h) })
  const d = h / 24; if (d < 30) return t('time.daysAgo', { count: Math.floor(d) })
  return new Date(norm).toLocaleDateString(getLocale())
}

const goneName = (g) => `${g.first_name || ''} ${g.last_name || ''}`.trim() || g.phone

export default function Sidebar({ accounts, gone = [], selectedId, onSelect, onAdd, onDeleted, onGoneChange }) {
  const { t } = useTranslation()
  const toast = useToast()

  const statusBadge = (status) => {
    if (status === 'connected') return <span className="nb-badge bg-brand-ok text-black">{t('nav.statusConnected')}</span>
    if (status === 'banned')    return <span className="nb-badge bg-brand-err text-black">{t('nav.statusBanned')}</span>
    if (status === 'connecting') return <span className="nb-badge bg-brand-violet text-black">Connecting</span>
    if (status === 'cooldown') return <span className="nb-badge bg-brand-warn text-black">Waiting</span>
    if (status === 'session_revoked') return <span className="nb-badge bg-brand-err text-black">Session revoked</span>
    if (status === 'auth_error') return <span className="nb-badge bg-brand-err text-black">Auth error</span>
    return <span className="nb-badge bg-brand-warn text-black">{t('nav.statusDisconnected')}</span>
  }
  const [pendingDelete, setPendingDelete] = useState(null)  // account pending removal
  const [confirmRemoveAll, setConfirmRemoveAll] = useState(false)
  const [passwordRemoveAll, setPasswordRemoveAll] = useState(false)
  const [removeAllPassword, setRemoveAllPassword] = useState('')
  const [removeAllBusy, setRemoveAllBusy] = useState(false)
  const [goneOpen, setGoneOpen] = useState(false)

  // Banned accounts drop out of the active list (they live in Gone/Banned).
  // Serial numbers are the 1-based position in this active list.
  const active = accounts.filter((a) => a.status !== 'banned')
  const totalAccounts = accounts.length

  async function confirmDelete() {
    const a = pendingDelete
    setPendingDelete(null)
    if (!a) return
    try {
      await Endpoints.deleteAccount(a.id)
      toast.success(t('nav.accountRemoved'))
      onDeleted?.()
    } catch (err) { toast.error(err.message) }
  }

  function askRemoveAllPassword() {
    setConfirmRemoveAll(false)
    setRemoveAllPassword('')
    setPasswordRemoveAll(true)
  }

  function closeRemoveAllPassword() {
    if (removeAllBusy) return
    setPasswordRemoveAll(false)
    setRemoveAllPassword('')
  }

  async function removeAllAccounts() {
    if (!removeAllPassword) {
      toast.error(t('nav.enterAppPwd'))
      return
    }
    setRemoveAllBusy(true)
    try {
      const r = await Endpoints.removeAllAccounts(removeAllPassword)
      toast.success(t('nav.removedCount', { count: r.removed || 0 }))
      setPasswordRemoveAll(false)
      setRemoveAllPassword('')
      onDeleted?.()
      onSelect?.(null)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setRemoveAllBusy(false)
    }
  }

  async function clearGone() {
    try { await Endpoints.clearGoneAccounts(); onGoneChange?.() }
    catch (err) { toast.error(err.message) }
  }
  async function dismissGone(id) {
    try { await Endpoints.deleteGoneAccount(id); onGoneChange?.() }
    catch (err) { toast.error(err.message) }
  }

  return (
    <aside className="w-[320px] border-r-2 border-black dark:border-white bg-white dark:bg-zinc-900 flex flex-col">
      <div className="p-3 border-b-2 border-black dark:border-white flex items-center justify-between">
        <span className="font-extrabold uppercase tracking-tight">{t('nav.accounts', { count: active.length })}</span>
      </div>
      <div className="flex-1 overflow-auto">
        {active.length === 0 && (
          <div className="p-4 text-sm opacity-70">{t('nav.noAccountsYet')}</div>
        )}
        {active.map((a, i) => {
          const sel = a.id === selectedId
          const serial = i + 1
          return (
            <div
              key={a.id}
              onClick={() => onSelect(a.id)}
              className={
                'flex items-start gap-2 p-3 border-b-2 border-black dark:border-white cursor-pointer transition-colors ' +
                (sel ? 'bg-brand-pri text-black' : 'hover:bg-zinc-100 dark:hover:bg-zinc-800')
              }
            >
              <div className="w-5 shrink-0 self-center text-right font-mono text-xs font-bold opacity-50 select-none">
                {serial}
              </div>
              <AccountAvatar account={a} size={40} showOnline />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-bold truncate">
                    {(a.first_name + ' ' + a.last_name).trim() || a.phone}
                  </div>
                  {statusBadge(a.status)}
                </div>
                <div className="flex items-center gap-1 text-xs font-mono truncate">
                  <span className="truncate">{a.phone}</span>
                  <CopyButton value={a.phone} label={t('profile.phone')} />
                </div>
                {a.username ? (
                  <div className="flex items-center gap-1 text-xs font-mono truncate opacity-80">
                    <span className="truncate">@{a.username}</span>
                    <CopyButton value={a.username} label={t('profile.username')} />
                  </div>
                ) : (
                  <div className="text-xs opacity-50 italic">{t('nav.noUsername')}</div>
                )}
                <div className="flex items-center gap-2 mt-1">
                  {a.has_2fa && <span className="nb-badge bg-brand-violet text-black">2FA</span>}
                  {a.unread_security > 0 && (
                    <span className="nb-badge bg-brand-err text-black">{t('nav.newBadge', { count: a.unread_security })}</span>
                  )}
                  <button
                    className="ml-auto text-[10px] underline opacity-60 hover:opacity-100"
                    onClick={(e) => { e.stopPropagation(); setPendingDelete(a) }}
                  >{t('common.remove')}</button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {gone.length > 0 && (
        <div className="border-t-2 border-black dark:border-white shrink-0">
          <button
            onClick={() => setGoneOpen((o) => !o)}
            className="w-full flex items-center gap-2 p-3 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <span className="font-extrabold uppercase tracking-tight text-sm">{t('nav.goneBanned')}</span>
            <span className="nb-badge bg-brand-err text-black">{gone.length}</span>
            <span className="ml-auto opacity-60">{goneOpen ? '▲' : '▼'}</span>
          </button>
          {goneOpen && (
            <div className="max-h-72 overflow-auto border-t border-black/20 dark:border-white/20">
              {gone.map((g) => (
                <div key={g.id} className="px-3 py-2 border-b border-black/15 dark:border-white/15 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-bold truncate">{goneName(g)}</span>
                    <span className={'nb-badge text-black ' + (g.reason === 'banned' ? 'bg-brand-err' : 'bg-brand-warn')}>
                      {g.reason}
                    </span>
                    <span className="ml-auto font-mono opacity-60 shrink-0">{t('nav.wasSerial', { serial: g.old_serial ?? '—' })}</span>
                  </div>
                  <div className="font-mono opacity-70 truncate">
                    {g.phone}{g.username ? ` · @${g.username}` : ''}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="opacity-50">{ago(g.gone_at, t)}</span>
                    <button
                      className="ml-auto text-[10px] underline opacity-50 hover:opacity-100"
                      onClick={() => dismissGone(g.id)}
                    >{t('common.dismiss')}</button>
                  </div>
                </div>
              ))}
              <div className="p-2 text-center">
                <button className="text-[10px] underline opacity-60 hover:opacity-100" onClick={clearGone}>
                  {t('nav.clearAllHistory')}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="p-3 border-t-2 border-black dark:border-white shrink-0">
          <button
            className="nb-btn-err w-full mb-2"
            disabled={totalAccounts === 0 || removeAllBusy}
            onClick={() => setConfirmRemoveAll(true)}
          >
            {t('nav.removeAllAccounts')}
          </button>
        <button className="nb-btn-pri w-full" onClick={onAdd}>{t('nav.addAccount')}</button>
      </div>

      {pendingDelete && (
        <ConfirmModal
          title={t('nav.removeAccountTitle')}
          message={t('nav.removeAccountBody', {
            name: (pendingDelete.first_name + ' ' + pendingDelete.last_name).trim() || pendingDelete.phone,
            phone: pendingDelete.phone,
          })}
          confirmLabel={t('nav.removeConfirm')}
          cancelLabel={t('nav.removeCancel')}
          danger
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}

      {confirmRemoveAll && (
        <ConfirmModal
          title={t('nav.removeAllTitle')}
          message={t('nav.removeAllBody', { count: totalAccounts })}
          confirmLabel={t('nav.confirmYes')}
          cancelLabel={t('nav.confirmNo')}
          danger
          onConfirm={askRemoveAllPassword}
          onCancel={() => setConfirmRemoveAll(false)}
        />
      )}

      {passwordRemoveAll && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={closeRemoveAllPassword}>
          <div className="nb-card p-6 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <h2 className="font-extrabold uppercase tracking-tight mb-2">{t('nav.enterPasswordTitle')}</h2>
            <div className="text-sm opacity-80 mb-4">
              {t('nav.enterPasswordHint')}
            </div>
            <input
              type="password"
              className="nb-input mb-4"
              value={removeAllPassword}
              onChange={(e) => setRemoveAllPassword(e.target.value)}
              placeholder={t('nav.passwordPlaceholder')}
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') removeAllAccounts() }}
              disabled={removeAllBusy}
            />
            <div className="flex gap-2 justify-end">
              <button className="nb-btn" disabled={removeAllBusy} onClick={closeRemoveAllPassword}>{t('common.cancel')}</button>
              <button className="nb-btn-err" disabled={removeAllBusy || !removeAllPassword} onClick={removeAllAccounts}>
                {removeAllBusy ? t('nav.removing') : t('nav.removeAllBtn')}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
