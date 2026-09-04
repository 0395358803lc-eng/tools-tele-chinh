import { useEffect, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CopyButton } from '../lib/CopyButton'
import { fmtTime } from '../lib/util'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast'
import AccountAvatar from '../components/AccountAvatar'

function Stat({ label, value, color = 'bg-white', hint }) {
  return (
    <div className={`nb-stat p-4 ${color}`}>
      <div className="text-[10px] uppercase font-extrabold tracking-tight">{label}</div>
      <div className="text-3xl font-extrabold mt-1 font-mono leading-none">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-1">{hint}</div>}
    </div>
  )
}

function StatusBar({ stats, total, t }) {
  const c = stats.connected || 0
  const b = stats.banned || 0
  const d = Math.max(total - c - b, 0)
  if (total === 0) return null
  const pct = (n) => `${(n / total) * 100}%`
  return (
    <div className="flex h-4 border-2 border-black dark:border-white overflow-hidden">
      <div className="bg-brand-ok"   title={t('dashboard.healthConnected', { count: c })}    style={{ width: pct(c) }} />
      <div className="bg-brand-warn" title={t('dashboard.healthDisconnected', { count: d })} style={{ width: pct(d) }} />
      <div className="bg-brand-err"  title={t('dashboard.healthBanned', { count: b })}       style={{ width: pct(b) }} />
    </div>
  )
}

function StatusDot({ status, t }) {
  const c = status === 'connected' ? 'bg-brand-ok' : ['banned', 'session_revoked', 'auth_error'].includes(status) ? 'bg-brand-err' : status === 'connecting' ? 'bg-brand-violet' : 'bg-brand-warn'
  return <span className={'inline-block w-2 h-2 ' + c + ' border border-black dark:border-white'} title={statusLabel(status, t)} />
}

function statusLabel(status, t) {
  if (status === 'connected') return t('nav.statusConnected')
  if (status === 'banned') return t('nav.statusBanned')
  if (status === 'connecting') return 'Connecting'
  if (status === 'cooldown') return 'Waiting for Telegram'
  if (status === 'session_revoked') return 'Session revoked'
  if (status === 'auth_error') return 'Authentication error'
  return t('nav.statusDisconnected')
}

export default function DashboardTab({ stats, accounts, onSelect, onChange }) {
  const { t } = useTranslation()
  const toast = useToast()
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('all')   // all|connected|disconnected|banned|2fa|alerts
  const [recentAlerts, setRecentAlerts] = useState([])
  const [marking, setMarking] = useState(false)
  const [connectionBusy, setConnectionBusy] = useState(null)

  async function connectionAction(key, action) {
    setConnectionBusy(key)
    try {
      await action()
      await onChange?.()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setConnectionBusy(null)
    }
  }

  useEffect(() => {
    Endpoints.securityMessages(undefined, true).then((m) => setRecentAlerts((m || []).slice(0, 10))).catch(() => {})
  }, [stats.unread_security])

  async function markAllRead() {
    setMarking(true)
    try {
      await Endpoints.markAllRead()
      setRecentAlerts([])
      onChange?.()
    } catch (e) {
      // surface nothing intrusive; alerts will reappear on next refresh if it failed
    } finally {
      setMarking(false)
    }
  }

  const filtered = useMemo(() => {
    const qq = q.trim().toLowerCase()
    return accounts.filter((a) => {
      if (filter === 'connected'    && a.status !== 'connected')    return false
      if (filter === 'disconnected' && a.status === 'connected')    return false
      if (filter === 'banned'       && a.status !== 'banned')       return false
      if (filter === '2fa'          && !a.has_2fa)                  return false
      if (filter === 'alerts'       && (a.unread_security || 0) === 0) return false
      if (!qq) return true
      const hay = `${a.first_name} ${a.last_name} ${a.username} ${a.phone}`.toLowerCase()
      return hay.includes(qq)
    })
  }, [accounts, q, filter])

  const totalAlerts = stats.unread_security || 0
  const without2fa = stats.total - stats.with_2fa
  const onlineCount = accounts.filter((a) => a.is_online).length

  return (
    <div className="space-y-4">
      {/* TOP STATS */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
        <Stat label={t('dashboard.totalAccounts')} value={stats.total} />
        <Stat label={t('dashboard.connected')}      value={stats.connected} color="bg-brand-ok" />
        <Stat label={t('dashboard.disconnected')}   value={Math.max(stats.total - stats.connected - stats.banned, 0)} color="bg-brand-warn" />
        <Stat label={t('dashboard.banned')}         value={stats.banned}    color="bg-brand-err" />
        <Stat label={t('dashboard.twoFactorEnabled')}    value={stats.with_2fa}  color="bg-brand-violet"
              hint={without2fa > 0 ? t('dashboard.without2fa', { count: without2fa }) : t('dashboard.allProtected')} />
        <Stat label={t('dashboard.unreadAlerts')}  value={totalAlerts}     color={totalAlerts > 0 ? 'bg-brand-err' : 'bg-white'} />
      </div>

      {/* HEALTH BAR */}
      <div className="nb-card p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="font-extrabold uppercase text-sm">{t('dashboard.accountHealth')}</div>
          <div className="text-xs opacity-70">
            <span className="font-bold">{onlineCount}</span> {t('dashboard.online')} •{' '}
            <span className="font-bold">{stats.connected}</span> {t('dashboard.connected')} •{' '}
            <span className="font-bold">{stats.with_2fa}/{stats.total}</span> {t('dashboard.with2fa')}
          </div>
        </div>
        <StatusBar stats={stats} total={stats.total || 1} t={t} />
        <div className="flex gap-4 mt-2 text-[11px] flex-wrap">
          <span className="flex items-center gap-1"><span className="w-3 h-3 bg-brand-ok border border-black" /> {t('dashboard.healthConnected', { count: stats.connected })}</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 bg-brand-warn border border-black" /> {t('dashboard.healthDisconnected', { count: Math.max(stats.total - stats.connected - stats.banned, 0) })}</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 bg-brand-err border border-black" /> {t('dashboard.healthBanned', { count: stats.banned })}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* ACCOUNT TABLE (2/3) */}
        <div className="lg:col-span-2 nb-card p-4">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <div className="font-extrabold uppercase">{t('dashboard.allAccounts', { n: filtered.length, total: accounts.length })}</div>
            <div className="ml-auto flex gap-2 items-center flex-wrap">
              <button className="nb-btn !py-1 !px-2 text-xs" disabled={connectionBusy !== null}
                onClick={() => connectionAction('all-connect', Endpoints.connectAll)}>
                {connectionBusy === 'all-connect' ? t('dashboard.connecting') : t('dashboard.connectAll')}
              </button>
              <button className="nb-btn !py-1 !px-2 text-xs" disabled={connectionBusy !== null}
                onClick={() => connectionAction('all-disconnect', Endpoints.disconnectAll)}>
                {connectionBusy === 'all-disconnect' ? t('dashboard.disconnecting') : t('dashboard.disconnectAll')}
              </button>
              <input className="nb-input !w-44 !py-1 text-xs" placeholder={t('dashboard.searchPlaceholder')}
                value={q} onChange={(e) => setQ(e.target.value)} />
              <select className="nb-input !w-auto !py-1 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
                <option value="all">{t('common.all')}</option>
                <option value="connected">{t('dashboard.filterConnected')}</option>
                <option value="disconnected">{t('dashboard.filterDisconnected')}</option>
                <option value="banned">{t('dashboard.filterBanned')}</option>
                <option value="2fa">{t('dashboard.filterWith2fa')}</option>
                <option value="alerts">{t('dashboard.filterAlerts')}</option>
              </select>
            </div>
          </div>

          {filtered.length === 0 && (
            <div className="text-sm opacity-60 p-4 text-center">
              {accounts.length === 0
                ? t('dashboard.emptyNoAccounts')
                : t('dashboard.emptyNoMatch')}
            </div>
          )}

          <div className="overflow-auto max-h-[calc(100vh-360px)]">
            <table className="w-full text-sm">
              <thead className="text-[10px] uppercase font-extrabold sticky top-0 bg-white dark:bg-zinc-900 border-b-2 border-black dark:border-white">
                <tr>
                  <th className="text-left p-2">{t('dashboard.colAccount')}</th>
                  <th className="text-left p-2">{t('dashboard.colPhone')}</th>
                  <th className="text-left p-2">{t('dashboard.colUsername')}</th>
                  <th className="text-left p-2">{t('dashboard.colStatus')}</th>
                  <th className="text-left p-2">2FA</th>
                  <th className="text-left p-2">{t('dashboard.colAlerts')}</th>
                  <th className="text-left p-2">{t('dashboard.colLastSeen')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => (
                  <tr key={a.id} onClick={() => onSelect(a.id)}
                      className="border-b border-zinc-300 dark:border-zinc-700 hover:bg-brand-pri hover:text-black cursor-pointer">
                    <td className="p-2">
                      <div className="flex items-center gap-2">
                        <AccountAvatar account={a} size={28} />
                        <span className="font-bold truncate max-w-[140px]">{(a.first_name + ' ' + a.last_name).trim() || '—'}</span>
                        {a.is_online && <span className="w-2 h-2 bg-brand-ok border border-black" title={t('dashboard.online')} />}
                      </div>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      <span className="inline-flex items-center gap-1">{a.phone}<CopyButton value={a.phone} /></span>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {a.username
                        ? <span className="inline-flex items-center gap-1">@{a.username}<CopyButton value={a.username} /></span>
                        : <span className="opacity-40">—</span>}
                    </td>
                    <td className="p-2">
                      <span className="inline-flex items-center gap-1"><StatusDot status={a.status} t={t} /> <span className="text-xs uppercase">{statusLabel(a.status, t)}</span></span>
                      {a.status !== 'banned' && (
                        <button className="nb-btn !py-0.5 !px-2 text-[10px] ml-2"
                          disabled={connectionBusy !== null || a.status === 'connecting'}
                          onClick={(e) => {
                            e.stopPropagation()
                            const connected = a.status === 'connected'
                            connectionAction(`account-${a.id}`, () => connected
                              ? Endpoints.disconnectAccount(a.id)
                              : Endpoints.connectAccount(a.id))
                          }}>
                          {connectionBusy === `account-${a.id}`
                            ? '…'
                            : a.status === 'connected' ? t('dashboard.disconnect') : t('dashboard.connect')}
                        </button>
                      )}
                    </td>
                    <td className="p-2">
                      {a.has_2fa
                        ? <span className="nb-badge bg-brand-violet text-black">{t('common.on')}</span>
                        : <span className="nb-badge bg-zinc-200 text-zinc-700">{t('common.off')}</span>}
                    </td>
                    <td className="p-2">
                      {a.unread_security > 0
                        ? <span className="nb-badge bg-brand-err text-black">{a.unread_security}</span>
                        : <span className="opacity-40">—</span>}
                    </td>
                    <td className="p-2 text-xs opacity-70">
                      {a.last_seen ? fmtTime(a.last_seen) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* RECENT ALERTS (1/3) */}
        <div className="nb-card p-4 h-fit">
          <div className="flex items-center justify-between mb-3">
            <div className="font-extrabold uppercase">{t('dashboard.recentAlerts')}</div>
            <div className="flex items-center gap-2">
              {totalAlerts > 0 && (
                <button className="nb-btn !py-0.5 !px-2 text-[10px] uppercase font-extrabold"
                        onClick={markAllRead} disabled={marking}>
                  {marking ? '…' : t('dashboard.markAllRead')}
                </button>
              )}
              <span className="nb-badge bg-brand-err text-black">{totalAlerts}</span>
            </div>
          </div>
          {recentAlerts.length === 0 && (
            <div className="text-sm opacity-60">{t('dashboard.noUnreadAlerts')}</div>
          )}
          <div className="space-y-2 max-h-[60vh] overflow-auto">
            {recentAlerts.map((m) => {
              const acc = accounts.find((a) => a.id === m.account_id)
              return (
                <div key={m.id} className="nb-card-sm p-2 text-xs" onClick={() => acc && onSelect(acc.id)}>
                  <div className="flex items-center gap-1 mb-1">
                    <span className="nb-badge bg-brand-warn text-black !text-[9px]">{m.type}</span>
                    <span className="opacity-70 ml-auto">{fmtTime(m.received_at)}</span>
                  </div>
                  <div className="font-mono whitespace-pre-wrap line-clamp-3">{m.message_text}</div>
                  {acc && <div className="text-[10px] opacity-60 mt-1">→ {(acc.first_name || acc.phone)}</div>}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
