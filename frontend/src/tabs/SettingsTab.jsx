import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import i18n, { getSavedLanguage, setSavedLanguage } from '../i18n'

export default function SettingsTab() {
  const { t } = useTranslation()
  const toast = useToast()
  const [s, setS] = useState(null)
  const [busy, setBusy] = useState(false)
  const [diagnostics, setDiagnostics] = useState(null)
  const [logs, setLogs] = useState([])
  const [logLimit, setLogLimit] = useState(100)
  const [errorsOnly, setErrorsOnly] = useState(false)

  const [lang, setLang] = useState(getSavedLanguage())

  useEffect(() => {
    Endpoints.getSettings().then(setS).catch((e) => toast.error(e.message))
    Endpoints.diagnostics().then(setDiagnostics).catch(() => {})
  }, [])

  async function refreshLogs(limit = logLimit, onlyErrors = errorsOnly) {
    try { setLogs((await Endpoints.logs(limit, onlyErrors)).lines || []) }
    catch (e) { toast.error(e.message) }
  }

  async function backup() {
    setBusy(true)
    try {
      const result = await Endpoints.createBackup()
      toast.success(t('settings.backupCompleted', { name: result.name }))
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  if (!s) return <div className="opacity-60">{t('settings.loading')}</div>

  async function save() {
    setBusy(true)
    try {
      const r = await Endpoints.putSettings(s)
      setS(r)
      toast.success(t('settings.saved'))
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  function changeLanguage(language) {
    setLang(language)
    setSavedLanguage(language)
    i18n.changeLanguage(language)
  }

  async function exportJson() {
    try {
      const data = await Endpoints.exportJson()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `accounts-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { toast.error(e.message) }
  }

  return (
    <div className="max-w-2xl space-y-4">
      <div className="nb-card p-5">
        <h3 className="font-extrabold uppercase mb-4">{t('settings.language')}</h3>
        <div className="text-xs opacity-70 mb-3">
          {t('settings.languageDesc')}
        </div>
        <select className="nb-input" value={lang} onChange={(e) => changeLanguage(e.target.value)}>
          <option value="vi">{t('settings.languageVn')}</option>
          <option value="en">{t('settings.languageEn')}</option>
        </select>
      </div>

      <div className="nb-card p-5">
        <h3 className="font-extrabold uppercase mb-4">{t('settings.rateLimitWindow')}</h3>
        <p className="text-xs opacity-70 mb-3">
          {t('settings.rateLimitDesc')}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label>
            <div className="text-xs font-bold uppercase mb-1">{t('settings.minSeconds')}</div>
            <input type="number" step="0.1" className="nb-input" value={s.rate_min}
              onChange={(e) => setS({ ...s, rate_min: Number(e.target.value) || 0 })} />
          </label>
          <label>
            <div className="text-xs font-bold uppercase mb-1">{t('settings.maxSeconds')}</div>
            <input type="number" step="0.1" className="nb-input" value={s.rate_max}
              onChange={(e) => setS({ ...s, rate_max: Number(e.target.value) || 0 })} />
          </label>
        </div>
        <label className="block mt-3">
          <div className="text-xs font-bold uppercase mb-1">{t('settings.parallelAccounts')}</div>
          <input type="number" step="1" min="1" max="50" className="nb-input"
            value={s.concurrency ?? 5}
            onChange={(e) => setS({ ...s, concurrency: Math.max(1, parseInt(e.target.value) || 1) })} />
          <p className="text-xs opacity-70 mt-1">
            {t('settings.parallelDesc')}
          </p>
        </label>
      </div>

      <div className="nb-card p-5">
        <h3 className="font-extrabold uppercase mb-4">{t('settings.behavior')}</h3>
        <label className="flex items-center gap-2 mb-2">
          <input type="checkbox" checked={s.auto_reconnect}
            onChange={(e) => setS({ ...s, auto_reconnect: e.target.checked })} />
          <span>{t('settings.autoReconnect')}</span>
        </label>
      </div>

      <div className="flex gap-2">
        <button className="nb-btn-pri" disabled={busy} onClick={save}>{t('settings.saveSettings')}</button>
        <button className="nb-btn" onClick={exportJson}>{t('settings.exportAccounts')}</button>
        <button className="nb-btn" disabled={busy} onClick={backup}>{t('settings.createBackup')}</button>
      </div>

      {diagnostics && (
        <div className="nb-card p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-extrabold uppercase">{t('settings.systemDiagnostics')}</h3>
            <button className="nb-btn !py-1 text-xs" onClick={() => navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2))}>{t('settings.copy')}</button>
          </div>
          <pre className="text-xs whitespace-pre-wrap overflow-auto">{JSON.stringify(diagnostics, null, 2)}</pre>
        </div>
      )}

      <div className="nb-card p-5">
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <h3 className="font-extrabold uppercase mr-auto">{t('settings.systemLogs')}</h3>
          <select className="nb-input !w-auto !py-1 text-xs" value={logLimit} onChange={(e) => setLogLimit(Number(e.target.value))}>
            <option value={100}>{t('settings.lastN', { count: 100 })}</option><option value={500}>{t('settings.lastN', { count: 500 })}</option>
          </select>
          <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> {t('settings.errorsOnly')}</label>
          <button className="nb-btn !py-1 text-xs" onClick={() => refreshLogs()}>{t('settings.load')}</button>
          <button className="nb-btn !py-1 text-xs" disabled={!logs.length} onClick={() => navigator.clipboard.writeText(logs.join(''))}>{t('settings.copy')}</button>
          <button className="nb-btn !py-1 text-xs" onClick={() => Endpoints.openLogFolder().catch((e) => toast.error(e.message))}>{t('settings.openFolder')}</button>
        </div>
        <pre className="text-[11px] whitespace-pre-wrap overflow-auto max-h-80 bg-zinc-100 dark:bg-zinc-950 p-2">{logs.join('') || t('settings.logsEmpty')}</pre>
      </div>
    </div>
  )
}
