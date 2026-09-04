import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import i18n, { getSavedLanguage, setSavedLanguage } from '../i18n'

export default function LoginScreen({ onAuthed }) {
  const { t } = useTranslation()
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [lang, setLang] = useState(getSavedLanguage())

  function changeLanguage(language) {
    setLang(language)
    setSavedLanguage(language)
    i18n.changeLanguage(language)
  }

  async function submit(e) {
    e?.preventDefault?.()
    if (!pw) return
    setBusy(true); setErr('')
    try {
      await Endpoints.login(pw)
      onAuthed?.()
    } catch (e) {
      setErr(e.message || t('login.loginFailed'))
      setPw('')
    } finally { setBusy(false) }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-zinc-100 dark:bg-zinc-950">
      <form className="nb-card p-6 w-full max-w-sm" onSubmit={submit}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-xs font-extrabold uppercase tracking-tight text-brand-pri inline-block bg-black px-2 py-0.5">
            {t('login.protected')}
          </div>
          <select
            className="nb-input !w-auto !py-0.5 !px-2 text-xs"
            value={lang}
            onChange={(e) => changeLanguage(e.target.value)}
            title="🌐"
            aria-label="Language"
          >
            <option value="vi">🌐 {t('login.langVi')}</option>
            <option value="en">🌐 {t('login.langEn')}</option>
          </select>
        </div>
        <h1 className="font-extrabold uppercase tracking-tighter text-2xl mb-1">Multi TG Manager</h1>
        <p className="text-sm opacity-70 mb-4">{t('login.subtitle')}</p>
        <label className="block">
          <div className="text-xs font-bold uppercase mb-1">{t('login.password')}</div>
          <input
            type="password"
            className="nb-input"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            autoFocus
            autoComplete="current-password"
          />
        </label>
        {err && (
          <div className="mt-3 nb-card-sm bg-brand-err text-black px-3 py-2 text-sm font-bold">
            {err}
          </div>
        )}
        <button className="nb-btn-pri w-full mt-4" disabled={busy || !pw} type="submit">
          {busy ? '…' : t('login.unlock')}
        </button>
        <p className="text-[10px] opacity-50 mt-4 leading-snug">
          {t('login.rateLimitNote')}
        </p>
      </form>
    </div>
  )
}
