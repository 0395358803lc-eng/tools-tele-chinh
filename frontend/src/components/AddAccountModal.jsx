import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'

export default function AddAccountModal({ onClose, onAdded, onImported }) {
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
      toast.error('Enter phone with country code, e.g. +8801712345678')
      return
    }
    setBusy(true); setHint('Sending code via Telegram... up to 30 seconds')
    try {
      await Endpoints.sendCode(phone)
      toast.info('Code sent. Check Telegram.')
      setHint('')
      setStep(2)
    } catch (e) { toast.error(e.message); setHint('') } finally { setBusy(false) }
  }

  async function submitCode() {
    if (!code) return
    setBusy(true); setHint('Verifying code...')
    try {
      const r = await Endpoints.signIn(phone, code)
      if (r?.needs_2fa) {
        toast.info('2FA password required')
        setHint('')
        setStep(3)
      } else {
        toast.success('Account added!')
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
    setBusy(true); setHint('Submitting 2FA password...')
    try {
      await Endpoints.signIn2fa(phone, pwd)
      toast.success('Account added with 2FA!')
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
          toast.success('Account added via QR!')
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
          setQrError(r?.error || 'Telegram returned an error')
        }
      } catch (e) {
        // network errors during poll: keep trying, but surface persistent failures
      }
    }, 1500)
  }

  async function submitQr2fa() {
    if (!qr2faPwd || !qrIdRef.current) return
    setBusy(true); setHint('Submitting 2FA password...')
    try {
      const r = await Endpoints.qrSignIn2fa(qrIdRef.current, qr2faPwd)
      if (r?.state === 'authorized') {
        toast.success('Account added with 2FA!')
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
      toast.error('Choose at least one .session file')
      return
    }
    setBusy(true)
    setHint(`Importing ${sessionFiles.length} session file${sessionFiles.length === 1 ? '' : 's'}...`)
    setSessionResult(null)
    try {
      const r = await Endpoints.importSessions(sessionFiles)
      setSessionResult(r)
      if ((r.success || 0) > 0) {
        toast.success(`Imported ${r.success} account${r.success === 1 ? '' : 's'}`)
        onImported?.()
      }
      const needsAttention = (r.failed || 0) + (r.skipped || 0)
      if (needsAttention > 0) {
        toast.info(`${needsAttention} session file${needsAttention === 1 ? '' : 's'} need attention`)
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
    setHint('Scanning sessions folder...')
    setSessionResult(null)
    try {
      const r = await Endpoints.syncSessionsFolder()
      setSessionResult(r)
      if ((r.success || 0) > 0) {
        toast.success(`Added ${r.success} pasted session${r.success === 1 ? '' : 's'}`)
        onImported?.()
      } else if ((r.failed || 0) === 0) {
        toast.info('No new pasted sessions found')
      }
      const needsAttention = (r.failed || 0) + (r.skipped || 0)
      if (needsAttention > 0) {
        toast.info(`${needsAttention} session file${needsAttention === 1 ? '' : 's'} reported`)
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
            Add Account {step === 3 && method === 'phone' && <span className="nb-badge bg-brand-violet text-black ml-2">2FA</span>}
            {method === 'qr' && qrState === 'needs_2fa' && <span className="nb-badge bg-brand-violet text-black ml-2">2FA</span>}
          </h2>
          <button className="nb-btn !py-1 !px-2" onClick={close}>✕</button>
        </div>

        <div className="flex gap-1 mb-4">
          <button
            className={`nb-tab flex-1 ${method === 'phone' ? 'nb-tab-active' : ''}`}
            onClick={() => setMethod('phone')}
          >Phone</button>
          <button
            className={`nb-tab flex-1 ${method === 'qr' ? 'nb-tab-active' : ''}`}
            onClick={() => setMethod('qr')}
          >QR Code</button>
          <button
            className={`nb-tab flex-1 ${method === 'session' ? 'nb-tab-active' : ''}`}
            onClick={() => setMethod('session')}
          >Session Files</button>
        </div>

        {method === 'phone' && step === 1 && (
          <div className="space-y-3">
            <label className="block">
              <div className="text-xs font-bold uppercase mb-1">Phone (with country code)</div>
              <input
                className="nb-input"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+8801712345678"
                autoFocus
                onKeyDown={(e) => { if (e.key === 'Enter') sendCode() }}
              />
            </label>
            <button className="nb-btn-pri w-full" disabled={busy} onClick={sendCode}>
              {busy ? 'Sending…' : 'Send Code'}
            </button>
          </div>
        )}

        {method === 'phone' && step === 2 && (
          <div className="space-y-3">
            <div className="text-sm">OTP sent to <span className="font-mono">{phone}</span></div>
            <input
              className="nb-input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Login code"
              autoFocus
              inputMode="numeric"
              onKeyDown={(e) => { if (e.key === 'Enter') submitCode() }}
            />
            <button className="nb-btn-pri w-full" disabled={busy || !code} onClick={submitCode}>
              {busy ? 'Verifying…' : 'Verify Code'}
            </button>
            <button className="nb-btn w-full" disabled={busy} onClick={() => setStep(1)}>
              Back / Resend
            </button>
          </div>
        )}

        {method === 'phone' && step === 3 && (
          <div className="space-y-3">
            <div className="nb-card-sm p-3 bg-brand-violet text-black text-sm font-bold">
              2FA enabled. Enter your Two-Step password for <span className="font-mono">{phone}</span>
            </div>
            <input
              type="password"
              className="nb-input"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              placeholder="Telegram 2FA password"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') submit2fa() }}
            />
            <button className="nb-btn-pri w-full" disabled={busy || !pwd} onClick={submit2fa}>
              {busy ? 'Submitting…' : 'Submit 2FA'}
            </button>
            <div className="text-[10px] opacity-60">
              Wrong password? You can retry without re-sending the code.
            </div>
          </div>
        )}

        {method === 'qr' && qrState !== 'needs_2fa' && (
          <div className="space-y-3">
            <ol className="text-xs space-y-1 opacity-80 list-decimal pl-4">
              <li>Open Telegram on your phone</li>
              <li>Go to <b>Settings → Devices → Link Desktop Device</b></li>
              <li>Scan the code below</li>
            </ol>

            <div className="flex items-center justify-center bg-white rounded p-3 border-2 border-black min-h-[280px]">
              {qrImg ? (
                <img src={qrImg} alt="Telegram QR" width="260" height="260" />
              ) : (
                <div className="text-xs opacity-60">{busy ? 'Generating QR…' : 'No code yet'}</div>
              )}
            </div>

            {qrState === 'waiting' && (
              <div className="text-xs opacity-70 text-center">Waiting for scan…</div>
            )}
            {qrState === 'expired' && (
              <div className="nb-card-sm p-2 bg-yellow-200 text-black text-xs font-bold text-center">
                QR expired. Tap Refresh to get a new code.
              </div>
            )}
            {qrState === 'error' && (
              <div className="nb-card-sm p-2 bg-red-300 text-black text-xs font-bold">
                {qrError || 'Something went wrong'}
              </div>
            )}

            <button className="nb-btn w-full" disabled={busy} onClick={refreshQr}>
              {busy ? 'Working…' : 'Refresh Code'}
            </button>
          </div>
        )}

        {method === 'qr' && qrState === 'needs_2fa' && (
          <div className="space-y-3">
            <div className="nb-card-sm p-3 bg-brand-violet text-black text-sm font-bold">
              QR scanned. This account has 2FA — enter your Two-Step password.
            </div>
            <input
              type="password"
              className="nb-input"
              value={qr2faPwd}
              onChange={(e) => setQr2faPwd(e.target.value)}
              placeholder="Telegram 2FA password"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') submitQr2fa() }}
            />
            <button className="nb-btn-pri w-full" disabled={busy || !qr2faPwd} onClick={submitQr2fa}>
              {busy ? 'Submitting…' : 'Submit 2FA'}
            </button>
          </div>
        )}

        {method === 'session' && (
          <div className="space-y-3">
            <div className="text-sm opacity-80">
              Import one or more Telethon <span className="font-mono">.session</span> files, or paste them into the sessions folder and scan. If one file fails, the rest will still be imported.
            </div>

            <label className="block">
              <div className="text-xs font-bold uppercase mb-1">Session files</div>
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
              {busy ? 'Importing...' : `Import ${sessionFiles.length || ''} Session File${sessionFiles.length === 1 ? '' : 's'}`}
            </button>

            <button className="nb-btn-info w-full" disabled={busy} onClick={scanSessionsFolder}>
              Scan Pasted Sessions Folder
            </button>

            {sessionResult && (
              <div className="space-y-2">
                <div className="flex gap-2 flex-wrap">
                  <span className="nb-badge bg-brand-ok text-black">{sessionResult.success || 0} imported</span>
                  <span className="nb-badge bg-brand-err text-black">{sessionResult.failed || 0} failed</span>
                  <span className="nb-badge bg-brand-warn text-black">{sessionResult.skipped || 0} skipped</span>
                </div>
                <div className="space-y-1 max-h-64 overflow-auto">
                  {(sessionResult.results || []).map((r, i) => (
                    <div key={`${r.filename}-${i}`} className="nb-card-sm p-2 text-sm flex items-center gap-2">
                      <span className={'nb-badge text-black ' + (r.status === 'ok' ? 'bg-brand-ok' : r.status === 'skipped' ? 'bg-brand-warn' : 'bg-brand-err')}>
                        {r.status}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-bold truncate">{r.name || r.phone || r.filename}</div>
                        <div className="font-mono text-[11px] opacity-70 truncate">{r.filename}{r.phone ? ` - ${r.phone}` : ''}</div>
                      </div>
                      {r.detail && <div className="text-xs opacity-75 max-w-[45%] truncate" title={r.detail}>{r.detail}</div>}
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
