import { useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints, onUnauthorized } from './lib/api'
import { useToast } from './lib/toast.jsx'
import { useTheme } from './lib/theme'
import { ensureNotificationPermission, desktopNotify } from './lib/util'
import LoginScreen from './components/LoginScreen.jsx'
import Sidebar from './components/Sidebar.jsx'
import TopStats from './components/TopStats.jsx'
import AddAccountModal from './components/AddAccountModal.jsx'
import DashboardTab from './tabs/DashboardTab.jsx'
import ProfileTab from './tabs/ProfileTab.jsx'
import SecurityTab from './tabs/SecurityTab.jsx'
import GroupsTab from './tabs/GroupsTab.jsx'
import MessagingTab from './tabs/MessagingTab.jsx'
import TargetCheckTab from './tabs/TargetCheckTab.jsx'
import BulkTab from './tabs/BulkTab.jsx'
import SettingsTab from './tabs/SettingsTab.jsx'

const TABS = [
  { id: 'dashboard', labelKey: 'navigation.dashboard' },
  { id: 'profile',   labelKey: 'navigation.profile'   },
  { id: 'security',  labelKey: 'navigation.security'  },
  { id: 'groups',    labelKey: 'navigation.groups'    },
  { id: 'messages',  labelKey: 'navigation.messages'  },
  { id: 'checker',   labelKey: 'navigation.checker'   },
  { id: 'bulk',      labelKey: 'navigation.bulk'      },
  { id: 'settings',  labelKey: 'navigation.settings'  },
]

export default function App() {
  const { t } = useTranslation()
  const toast = useToast()
  const { theme, toggle } = useTheme()
  const [authState, setAuthState] = useState('checking') // checking | in | out
  const [health, setHealth] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [gone, setGone] = useState([])  // banned/removed account history
  const [stats, setStats] = useState({ total: 0, connected: 0, banned: 0, with_2fa: 0, unread_security: 0 })
  const [selectedId, setSelectedId] = useState(null)
  const [tab, setTab] = useState('dashboard')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [addOpen, setAddOpen] = useState(false)
  const prevUnreadRef = useRef(0)

  // initial auth check
  useEffect(() => {
    Endpoints.health().then(setHealth).catch(() => setHealth({ backend: 'error', database: 'unknown' }))
    Endpoints.me()
      .then((r) => setAuthState(r?.authed ? 'in' : 'out'))
      .catch(() => setAuthState('out'))
  }, [])

  // global 401 handler: kick back to login
  useEffect(() => onUnauthorized(() => {
    setAuthState('out')
    setAccounts([]); setGone([]); setSelectedId(null)
  }), [])

  async function logout() {
    try { await Endpoints.logout() } catch {}
    setAuthState('out')
    setAccounts([]); setGone([]); setSelectedId(null)
    prevUnreadRef.current = 0
  }

  const refreshAccounts = useCallback(async () => {
    try {
      const list = await Endpoints.accounts()
      setAccounts(list)
      if (!selectedId && list.length) setSelectedId(list[0].id)
    } catch (e) {
      // Stay silent on polling failures — only show error on first-load
      if (accounts.length === 0 && !e.network && e.status !== 401) {
        toast.error(t('common.loading') + ' accounts: ' + e.message)
      }
    }
  }, [selectedId, toast, accounts.length])

  const refreshGone = useCallback(async () => {
    try { setGone(await Endpoints.goneAccounts()) } catch (e) { /* silent */ }
  }, [])

  const refreshStats = useCallback(async () => {
    try {
      const s = await Endpoints.stats()
      setStats(s)
      if (s.unread_security > prevUnreadRef.current && prevUnreadRef.current !== 0) {
        desktopNotify(t('nav.statusConnected') + ' · ' + t('app.title'), `Unread: ${s.unread_security}`)
      }
      prevUnreadRef.current = s.unread_security
    } catch (e) { /* silent */ }
  }, [])

  useEffect(() => {
    if (authState !== 'in') return
    ensureNotificationPermission()
    refreshAccounts()
    refreshStats()
    refreshGone()
    const id = setInterval(() => { refreshAccounts(); refreshStats(); refreshGone() }, 30000)
    return () => clearInterval(id)
  }, [authState, refreshAccounts, refreshStats, refreshGone])

  if (authState === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-100 dark:bg-zinc-950">
        <div className="nb-card-sm p-4 font-bold uppercase tracking-tight">{t('common.loading')}</div>
      </div>
    )
  }
  if (health?.database === 'error' || health?.secret_store === 'error') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-100 dark:bg-zinc-950 p-6">
        <div className="nb-card p-6 max-w-xl">
          <h1 className="font-extrabold text-xl uppercase text-brand-err">Startup health problem detected</h1>
          <p className="mt-3">Telegram clients were not started. Check database integrity and Windows DPAPI, then restore a known-good backup if needed.</p>
        </div>
      </div>
    )
  }
  if (authState === 'out') {
    return <LoginScreen onAuthed={() => setAuthState('in')} />
  }

  const selected = accounts.find((a) => a.id === selectedId) || null

  return (
    <div className="h-screen w-screen flex flex-col">
      <header className="border-b-2 border-black dark:border-white bg-brand-pri text-black flex items-center px-4 py-2 gap-3">
        <button onClick={() => setSidebarOpen((s) => !s)} className="nb-btn !bg-white !text-black !py-1 !px-2">
          ☰
        </button>
        <h1 className="font-extrabold text-xl uppercase tracking-tighter">Multi TG Manager</h1>
        {health && health.telegram_api !== 'configured' && <span className="nb-badge bg-brand-err text-black">Telegram API missing</span>}
        <div className="flex-1" />
        <TopStats stats={stats} onBellClick={() => setTab('security')} />
        <button onClick={toggle} className="nb-btn !bg-white !text-black !py-1 !px-2" title={t('app.theme')}>
          {theme === 'dark' ? '☀' : '☾'}
        </button>
        <button onClick={logout} className="nb-btn !bg-white !text-black !py-1 !px-2" title={t('app.logout')}>
          ⏻
        </button>
      </header>

      <div className="flex-1 flex min-h-0">
        {sidebarOpen && (
          <Sidebar
            accounts={accounts}
            gone={gone}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAdd={() => setAddOpen(true)}
            onDeleted={() => { refreshAccounts(); refreshGone() }}
            onGoneChange={refreshGone}
          />
        )}

        <main className="flex-1 min-w-0 flex flex-col">
          <nav className="flex gap-1 px-4 pt-3 flex-wrap border-b-2 border-black dark:border-white bg-zinc-100 dark:bg-zinc-900">
            {TABS.map((tabItem) => (
              <button
                key={tabItem.id}
                className={`nb-tab ${tab === tabItem.id ? 'nb-tab-active' : ''}`}
                onClick={() => setTab(tabItem.id)}
              >
                {t(tabItem.labelKey)}
              </button>
            ))}
          </nav>
          <div className="flex-1 min-h-0 overflow-auto p-4">
            {tab === 'dashboard' && <DashboardTab stats={stats} accounts={accounts} onSelect={(id) => { setSelectedId(id); setTab('profile') }} onChange={() => { refreshStats(); refreshAccounts() }} />}
            {tab === 'profile'   && <ProfileTab account={selected} onRefresh={refreshAccounts} />}
            {tab === 'security'  && <SecurityTab accounts={accounts} onChange={refreshStats} />}
            {tab === 'groups'    && <GroupsTab accounts={accounts} selected={selected} />}
            {tab === 'messages'  && <MessagingTab accounts={accounts} selected={selected} />}
            {tab === 'checker'   && <TargetCheckTab />}
            {tab === 'bulk'      && <BulkTab accounts={accounts} onDone={refreshAccounts} />}
            {tab === 'settings'  && <SettingsTab />}
          </div>
        </main>
      </div>

      {addOpen && (
        <AddAccountModal
          onClose={() => setAddOpen(false)}
          onAdded={() => { setAddOpen(false); refreshAccounts(); refreshStats() }}
          onImported={() => { refreshAccounts(); refreshStats() }}
        />
      )}
    </div>
  )
}
