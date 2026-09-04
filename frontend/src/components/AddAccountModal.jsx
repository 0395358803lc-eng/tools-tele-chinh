import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import QRCode from 'qrcode'
import { Endpoints } from '../lib/api'
import { rowText } from '../lib/err'
import { useToast } from '../lib/toast.jsx'

export default function AddAccountModal({ onClose, onAdded, onImported }) {
  const { t } = useTranslation()
  const toast = useToast()
  const [method, setMethod] = useState('phone') // 'phone' | 'qr' | 'session'

  // Phone flow
  const [step, setStep] = useState(1) // 1: phone, 2: code, 3: 2fa
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [pwd, setPwd] = useState('')
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')

  // QR flow
  const [qrId, setQrId] = useState(null)
  const [qrUrl, setQrUrl] = useState('')
  const [qrImg, setQrImg] = useState('')
  const [qrState, setQrState] = useState('idle') // idle|waiting|needs_2fa|expired|error|authorized
  const [qrError, setQrError] = useState('')
  const [qr2faPwd, setQr2faPwd] = useState('')
  const pollRef = useRef(null)
  const qrIdRef = useRef(null)

  // Session file import flow
  const [sessionFiles, setSessionFiles] = useState([])
  const [sessionResult, setSessionResult] = useState(null)

  async function close() {
    if (phone) { try { await Endpoints.authCancel(phone) } catch {} }
    if (qrIdRef.current) { try { await Endpoints.qrCancel(qrIdRef.current) } catch {} }
    stopPolling()
    onClose?.()
  }

  // ---------- Phone flow ----------
  async function sendCode() {
    if (!phone.startsWith('+')) {
      toast.error(t('addAccount.phoneFormatError'))
      return
    }
    setBusy(true); setHint(t('addAccount.sendingCode'))
    try {
      await Endpoints.sendCode(phone)
      toast.info(t('addAccount.codeSent'))
      setHint('')
      setStep(2)
    } catch (e) { toast.error(e.message); setHint('') } finally { setBusy(false) }
  }

  async function submitCode() {
    if (!code) return
    setBusy(true); setHint(t('addAccount.verifyingCode'))
    try {
      const r = await Endpoints.signIn(phone, code)
      if (r?.needs_2fa) {
        toast.info(t('addAccount.twoFaRequired'))
        setHint('')
        setStep(3)
      } else {
        toast.success(t('addAccount.accountAdded'))
        onAdded?.()
      }
    } catch (e) {
      toast.error(e.message)
      if (/code|invalid|expired/i.test(e.message)) {
        setStep(1)
      }
    } finally { setBusy(false); setHint('') }
  }

  async function submit2fa() {
    if (!pwd) return
    setBusy(true); setHint(t('addAccount.submitting2fa'))
    try {
      await Endpoints.signIn2fa(phone, pwd)
      toast.success(t('addAccount.accountAdded2fa'))
      onAdded?.()
    } catch (e) {
      toast.error(e.message)
      setPwd('')
    } finally { setBusy(false); setHint('') }
  }

  // ---------- QR flow ----------
  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  async function renderQr(url) {
    try {
      const dataUrl = await QRCode.toDataURL(url, {
        margin: 1,
        width: 260,
        color: { dark: '#000000', light: '#ffffff' },
      })
      setQrImg(dataUrl)
    } catch {
      setQrImg('')
    }
  }

  async function startQr() {
    setBusy(true); setQrError(''); setQrState('waiting')
    try {
      const r = await Endpoints.qrStart()
      qrIdRef.current = r.qr_id
      setQrId(r.qr_id)
      setQrUrl(r.url)
      await renderQr(r.url)
      beginPolling()
    } catch (e) {
      setQrState('error')
      setQrError(e.message)
    } finally { setBusy(false) }
  }

  async function refreshQr() {
    const id = qrIdRef.current
    if (!id) { return startQr() }
    setBusy(true); setQrError(''); setQrState('waiting')
    try {
      const r = await Endpoints.qrRecreate(id)
      setQrUrl(r.url)
      await renderQr(r.url)
    } catch (e) {
      setQrState('error')
      setQrError(e.message)
    } finally { setBusy(false) }
  }

  function beginPolling() {
    stopPolling()
    pollRef.current = setInterval(async () => {
      const id = qrIdRef.current
      if (!id) return
      try {
        const r = await Endpoints.qrPoll(id)
        const s = r?.state
        if (s === 'authorized') {
          stopPolling()
          setQrState('authorized')
          toast.success(t('addAccount.accountAddedQr'))
          onAdded?.()
        } else if (s === 'needs_2fa') {
          stopPolling()
          setQrState('needs_2fa')
        } else if (s === 'expired') {
          stopPolling()
          setQrState('expired')
        } else if (s === 'error') {
          stopPolling()
          setQrState('error')
          setQrError(r?.error || t('addAccount.telegramError'))
        }
      } catch (e) {
        // network errors during poll: keep trying, but surface persistent failures
      }
    }, 1500)
  }

  async function submitQr2fa() {
    if (!qr2faPwd || !qrIdRef.current) return
    setBusy(true); setHint(t('addAccount.submitting2fa'))
    try {
      const r = await Endpoints.qrSignIn2fa(qrIdRef.current, qr2faPwd)
      if (r?.state === 'authorized') {
        toast.success(t('addAccount.accountAdded2fa'))
        onAdded?.()
      }
    } catch (e) {
      toast.error(e.message)
      setQr2faPwd('')
    } finally { setBusy(false); setHint('') }
  }

  // Switch to QR tab → auto-start. Switch away → cancel.
  function onPickSessionFiles(e) {
    const picked = Array.from(e.target.files || [])
    setSessionFiles(picked)
    setSessionResult(null)
  }

  async function importSessionFiles() {
    if (sessionFiles.length === 0) {
      toast.error(t('addAccount.pickSessionsError'))
      return
    }
    setBusy(true)
    setHint(t('addAccount.importingSessions', { count: sessionFiles.length }))
    setSessionResult(null)
    try {
      const r = await Endpoints.importSessions(sessionFiles)
      setSessionResult(r)
      if ((r.success || 0) > 0) {
        toast.success(t('addAccount.importedAccounts', { count: r.success }))
        onImported?.()
      }
      const needsAttention = (r.failed || 0) + (r.skipped || 0)
      if (needsAttention > 0) {
        toast.info(t('addAccount.needAttention', { count: needsAttention }))
      }
    } catch (e) {
      toast.error(e.message)
    } finally {
      setBusy(false)
      setHint('')
    }
  }

  async function scanSessionsFolder() {
    setBusy(true)
    setHint(t('addAccount.scanningFolder'))
    setSessionResult(null)
    try {
      const r = await Endpoints.syncSessionsFolder()
      setSessionResult(r)
      if ((r.success || 0) > 0) {
        toast.success(t('addAccount.addedSessions', { count: r.success }))
        onImported?.()
      } else if ((r.failed || 0) === 0) {
        toast.info(t('addAccount.noNewSessions'))
      }
      const needsAttention = (r.failed || 0) + (r.skipped || 0)
      if (needsAttention > 0) {
        toast.info(t('addAccount.reportedSessions', { count: needsAttention }))
      }
    } catch (e) {
      toast.error(e.message)
    } finally {
      setBusy(false)
      setHint('')
    }
  }

  useEffect(() => {
    if (method === 'qr' && !qrIdRef.current) {
      startQr()
    }
    if (method !== 'qr' && qrIdRef.current) {
      const id = qrIdRef.current
      qrIdRef.current = null
      setQrId(null); setQrUrl(''); setQrImg(''); setQrState('idle')
      stopPolling()
      Endpoints.qrCancel(id).catch(() => {})
    }
  }, [method])

  useEffect(() => () => stopPolling(), [])

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={close}>
      <div className="nb-card p-6 w-full max-w-2xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-extrabold uppercase tracking-tight text-xl">
            {t('addAccount.title')} {step === 3 && method === 'phone' && <span className="nb-badge bg-brand-violet text-black ml-2">2FA</span>}
            {method === 'qr' && qrState === 'needs_2fa' && <span className="nb-badge bg-brand-violet text-black ml-2">2FA</span>}
          </h2>
          <button className="nb-btn !py-1 !px-2" onClick={close}>✕</button>
        </div>

        <div className="flex gap-1 mb-4">
          <button
            className={`nb-tab flex-1 ${method === 'phone' ? 'nb-tab-active' : ''}`}
            onClick={() => setMethod('phone')}
          >{t('addAccount.tabPhone')}</button>
          <button
            className={`nb-tab flex-1 ${method === 'qr' ? 'nb-tab-active' : ''}`}
            onClick={() => setMethod('qr')}
          >{t('addAccount.tabQr')}</button>
          <button
            className={`nb-tab flex-1 ${method === 'session' ? 'nb-tab-active' : ''}`}
            onClick={() => setMethod('session')}
          >{t('addAccount.tabSession')}</button>
        </div>

        {method === 'phone' && step === 1 && (
          <div className="space-y-3">
            <label className="block">
              <div className="text-xs font-bold uppercase mb-1">{t('addAccount.phoneLabel')}</div>
              <input
                className="nb-input"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder={t('addAccount.phonePlaceholder')}
                autoFocus
                onKeyDown={(e) => { if (e.key === 'Enter') sendCode() }}
              />
            </label>
            <button className="nb-btn-pri w-full" disabled={busy} onClick={sendCode}>
              {busy ? t('addAccount.sendCodeBusy') : t('addAccount.sendCode')}
            </button>
          </div>
        )}

        {method === 'phone' && step === 2 && (
          <div className="space-y-3">
            <div className="text-sm">{t('addAccount.otpSent')} <span className="font-mono">{phone}</span></div>
            <input
              className="nb-input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={t('addAccount.codePlaceholder')}
              autoFocus
              inputMode="numeric"
              onKeyDown={(e) => { if (e.key === 'Enter') submitCode() }}
            />
            <button className="nb-btn-pri w-full" disabled={busy || !code} onClick={submitCode}>
              {busy ? t('addAccount.verifying') : t('addAccount.verifyCode')}
            </button>
            <button className="nb-btn w-full" disabled={busy} onClick={() => setStep(1)}>
              {t('addAccount.backResend')}
            </button>
          </div>
        )}

        {method === 'phone' && step === 3 && (
          <div className="space-y-3">
            <div className="nb-card-sm p-3 bg-brand-violet text-black text-sm font-bold">
              {t('addAccount.twoFaEnabled')} <span className="font-mono">{phone}</span>
            </div>
            <input
              type="password"
              className="nb-input"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              placeholder={t('addAccount.telegram2faPwd')}
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') submit2fa() }}
            />
            <button className="nb-btn-pri w-full" disabled={busy || !pwd} onClick={submit2fa}>
              {busy ? t('addAccount.submitting') : t('addAccount.submit2fa')}
            </button>
            <div className="text-[10px] opacity-60">
              {t('addAccount.twoFaRetryHint')}
            </div>
          </div>
        )}

        {method === 'qr' && qrState !== 'needs_2fa' && (
          <div className="space-y-3">
            <ol className="text-xs space-y-1 opacity-80 list-decimal pl-4">
              <li>{t('addAccount.qrStep1')}</li>
              <li>{t('addAccount.qrStep2')}</li>
              <li>{t('addAccount.qrStep3')}</li>
            </ol>

            <div className="flex items-center justify-center bg-white rounded p-3 border-2 border-black min-h-[280px]">
              {qrImg ? (
                <img src={qrImg} alt={t('addAccount.tabQr')} width="260" height="260" />
              ) : (
                <div className="text-xs opacity-60">{busy ? t('addAccount.generatingQr') : t('addAccount.noCodeYet')}</div>
              )}
            </div>

            {qrState === 'waiting' && (
              <div className="text-xs opacity-70 text-center">{t('addAccount.waitingScan')}</div>
            )}
            {qrState === 'expired' && (
              <div className="nb-card-sm p-2 bg-yellow-200 text-black text-xs font-bold text-center">
                {t('addAccount.qrExpired')}
              </div>
            )}
            {qrState === 'error' && (
              <div className="nb-card-sm p-2 bg-red-300 text-black text-xs font-bold">
                {qrError || t('addAccount.somethingWrong')}
              </div>
            )}

            <button className="nb-btn w-full" disabled={busy} onClick={refreshQr}>
              {busy ? t('addAccount.working') : t('addAccount.refreshCode')}
            </button>
          </div>
        )}

        {method === 'qr' && qrState === 'needs_2fa' && (
          <div className="space-y-3">
            <div className="nb-card-sm p-3 bg-brand-violet text-black text-sm font-bold">
              {t('addAccount.qrNeeds2fa')}
            </div>
            <input
              type="password"
              className="nb-input"
              value={qr2faPwd}
              onChange={(e) => setQr2faPwd(e.target.value)}
              placeholder={t('addAccount.telegram2faPwd')}
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') submitQr2fa() }}
            />
            <button className="nb-btn-pri w-full" disabled={busy || !qr2faPwd} onClick={submitQr2fa}>
              {busy ? t('addAccount.submitting') : t('addAccount.submit2fa')}
            </button>
          </div>
        )}

        {method === 'session' && (
          <div className="space-y-3">
            <div className="text-sm opacity-80">
              {t('addAccount.sessionIntro')}
            </div>

            <label className="block">
              <div className="text-xs font-bold uppercase mb-1">{t('addAccount.sessionFilesLabel')}</div>
              <input
                type="file"
                className="nb-input"
                accept=".session"
                multiple
                disabled={busy}
                onChange={onPickSessionFiles}
              />
            </label>

            {sessionFiles.length > 0 && (
              <div className="nb-card-sm p-2 max-h-28 overflow-auto text-xs">
                {sessionFiles.map((f, i) => (
                  <div key={`${f.name}-${i}`} className="flex gap-2">
                    <span className="font-mono opacity-60">{i + 1}.</span>
                    <span className="truncate">{f.name}</span>
                  </div>
                ))}
              </div>
            )}

            <button className="nb-btn-pri w-full" disabled={busy || sessionFiles.length === 0} onClick={importSessionFiles}>
              {busy ? t('addAccount.importingSessions', { count: sessionFiles.length }) : t('addAccount.importBtn', { count: sessionFiles.length })}
            </button>

            <button className="nb-btn-info w-full" disabled={busy} onClick={scanSessionsFolder}>
              {t('addAccount.scanFolder')}
            </button>

            {sessionResult && (
              <div className="space-y-2">
                <div className="flex gap-2 flex-wrap">
                  <span className="nb-badge bg-brand-ok text-black">{t('addAccount.importedBadge', { count: sessionResult.success || 0 })}</span>
                  <span className="nb-badge bg-brand-err text-black">{t('addAccount.failedBadge', { count: sessionResult.failed || 0 })}</span>
                  <span className="nb-badge bg-brand-warn text-black">{t('addAccount.skippedBadge', { count: sessionResult.skipped || 0 })}</span>
                </div>
                <div className="space-y-1 max-h-64 overflow-auto">
                  {(sessionResult.results || []).map((r, i) => (
                    <div key={`${r.filename}-${i}`} className="nb-card-sm p-2 text-sm flex items-center gap-2">
                      <span className={'nb-badge text-black ' + (r.status === 'ok' ? 'bg-brand-ok' : r.status === 'skipped' ? 'bg-brand-warn' : 'bg-brand-err')}>
                        {t(r.status === 'ok' ? 'progress.statusOk' : r.status === 'skipped' ? 'progress.statusSkipped' : 'progress.statusFailed')}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-bold truncate">{r.name || r.phone || r.filename}</div>
                        <div className="font-mono text-[11px] opacity-70 truncate">{r.filename}{r.phone ? ` - ${r.phone}` : ''}</div>
                      </div>
                      {rowText(r) && <div className="text-xs opacity-75 max-w-[45%] truncate" title={rowText(r)}>{rowText(r)}</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {hint && <div className="mt-3 text-xs opacity-70 italic">{hint}</div>}
      </div>
    </div>
  )
}
