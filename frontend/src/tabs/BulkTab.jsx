import { useEffect, useState, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import ProgressModal from '../components/ProgressModal.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'

// Parse CSV/TXT: each line = "firstname,lastname,username,bio".
// username is col 3 (no separators inside it); bio is col 4+ (commas inside bio
// allowed by taking the rest). Any field may be blank to skip it.
function parseCsv(text) {
  const rows = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    // allow tab or comma separator
    const sep = line.includes('\t') ? '\t' : ','
    const parts = line.split(sep)
    const first = (parts[0] ?? '').trim()
    const last = (parts[1] ?? '').trim()
    const username = (parts[2] ?? '').trim().replace(/^@/, '')
    const bio = parts.slice(3).join(sep).trim()
    rows.push({ first_name: first, last_name: last, username, bio })
  }
  return rows
}

export default function BulkTab({ accounts, onDone }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()
  const [ids, setIds] = useState([])
  // simple mode (one value to all)
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [username, setUsername] = useState('')
  const [bio, setBio] = useState('')
  const [appendNumber, setAppendNumber] = useState(false)
  const [startNumber, setStartNumber] = useState(1)
  // CSV mode
  const [csvRows, setCsvRows] = useState([])  // [{first_name, last_name, bio}, ...]
  const csvRef = useRef(null)
  // photos (accumulating)
  const [photos, setPhotos] = useState([])    // File[]
  const photoRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState(null)

  const allChecked = ids.length === accounts.length && accounts.length > 0
  const toggleAll = () => setIds(allChecked ? [] : accounts.map((a) => a.id))
  const toggle = (id) => setIds((arr) => arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id])

  // ----- CSV -----
  function loadCsv(e) {
    const file = e.target.files?.[0]; if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const rows = parseCsv(String(reader.result || ''))
      setCsvRows(rows)
      toast.info(t('bulk.loadedCsvRows', { count: rows.length }))
    }
    reader.onerror = () => toast.error(t('bulk.csvReadFail'))
    reader.readAsText(file)
    if (csvRef.current) csvRef.current.value = ''
  }
  function clearCsv() { setCsvRows([]) }

  async function applyProfile() {
    if (ids.length === 0) { toast.error(t('bulk.pickAccounts1')); return }
    const usingCsv = csvRows.length > 0
    if (!usingCsv && !firstName && !lastName && !username && bio === '') {
      toast.error(t('bulk.setFieldOrCsv'))
      return
    }
    if (!usingCsv && username && !appendNumber && ids.length > 1) {
      toast.error(t('bulk.usernameUnique'))
      return
    }
    setPending({
      title: t('bulk.bulkProfileTitle', { count: ids.length }),
      message: t('bulk.applyProfileConfirm', {
        count: ids.length,
        csv: usingCsv ? t('bulk.csvMapsRows', { count: Math.min(ids.length, csvRows.length) }) : '',
      }),
      onYes: () => doApplyProfile(usingCsv),
    })
  }

  async function doApplyProfile(usingCsv) {
    let per_account = null
    if (usingCsv) {
      per_account = {}
      ids.forEach((aid, i) => {
        const row = csvRows[i]
        if (!row) return
        per_account[String(aid)] = {
          first_name: row.first_name || null,
          last_name:  row.last_name  || null,
          username:   row.username   || null,
          bio:        row.bio        || null,
        }
      })
    }
    const payload = {
      account_ids: ids,
      first_name: usingCsv ? null : (firstName || null),
      last_name:  usingCsv ? null : (lastName || null),
      username:   usingCsv ? null : (username || null),
      bio:        usingCsv ? null : (bio === '' ? null : bio),
      append_number: !usingCsv && appendNumber,
      start_number: startNumber,
      per_account,
    }
    setBusy(true)
    await run(t('bulk.bulkProfileTitle', { count: ids.length }), (onEvent) => Endpoints.bulkProfile(payload, onEvent))
    setBusy(false)
    onDone?.()
  }

  // ----- PHOTOS -----
  function addPhotos(e) {
    const list = Array.from(e.target.files || [])
    if (!list.length) return
    setPhotos((cur) => [...cur, ...list])
    if (photoRef.current) photoRef.current.value = ''  // allow re-picking same files
    toast.info(t('bulk.addedPhotos', { count: list.length, total: photos.length + list.length }))
  }
  function removePhoto(i) { setPhotos((cur) => cur.filter((_, j) => j !== i)) }
  function clearPhotos() { setPhotos([]) }

  const photoThumbs = useMemo(
    () => photos.map((f) => ({ name: f.name, size: f.size, url: URL.createObjectURL(f) })),
    [photos]
  )
  useEffect(() => () => {
    photoThumbs.forEach((photo) => URL.revokeObjectURL(photo.url))
  }, [photoThumbs])

  async function applyPhoto() {
    if (photos.length === 0) { toast.error(t('bulk.pickPhoto')); return }
    if (ids.length === 0) { toast.error(t('bulk.pickAccounts1')); return }
    const usable = Math.min(photos.length, ids.length)
    const extra = photos.length > ids.length ? t('bulk.extraPhotos', { count: photos.length - ids.length }) : ''
    const missing = ids.length > photos.length ? t('bulk.missingPhotos', { count: ids.length - photos.length }) : ''
    setPending({
      title: t('bulk.bulkPhotoTitle', { usable, count: ids.length }),
      message: t('bulk.applyPhotosConfirm', { usable, count: ids.length, extra, missing }),
      onYes: () => doApplyPhoto(usable),
    })
  }

  async function doApplyPhoto(usable) {
    setBusy(true)
    await run(t('bulk.bulkPhotoTitle', { usable, count: ids.length }), (onEvent) => Endpoints.bulkPhoto(ids, photos, onEvent))
    setBusy(false)
    onDone?.()
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 space-y-4">
        <div className="nb-card p-4">
          <h3 className="font-extrabold uppercase mb-3">{t('bulk.bulkProfileEdit')}</h3>

          <div className="nb-card-sm p-3 mb-3 bg-zinc-50 dark:bg-zinc-800">
            <div className="text-xs font-bold uppercase mb-2">{t('bulk.csvImport')}</div>
            <div className="flex items-center gap-2 flex-wrap">
              <input ref={csvRef} type="file" accept=".csv,.txt" onChange={loadCsv}
                className="text-xs file:mr-2 file:cursor-pointer file:border-2 file:border-black file:bg-white file:px-2 file:py-1 file:font-bold file:uppercase" />
              {csvRows.length > 0 && (
                <>
                  <span className="text-xs font-bold">{t('bulk.rowsLoaded', { count: csvRows.length })}</span>
                  <button className="nb-btn !py-0.5 !px-2 text-xs" onClick={clearCsv}>{t('common.clear')}</button>
                </>
              )}
            </div>
            <div className="text-[10px] opacity-60 mt-1">
              {t('bulk.csvFormat')}
              {csvRows.length > 0 && firstName === '' && lastName === '' && bio === '' ? '' : t('bulk.csvIgnoresFields')}
            </div>
            {csvRows.length > 0 && (
              <div className="mt-2 max-h-32 overflow-auto text-xs font-mono opacity-80">
                {csvRows.slice(0, 5).map((r, i) => (
                  <div key={i}>{i + 1}. {r.first_name} | {r.last_name} | {r.username ? '@' + r.username : '—'} | {r.bio.slice(0, 40)}</div>
                ))}
                {csvRows.length > 5 && <div>{t('common.more', { count: csvRows.length - 5 })}</div>}
              </div>
            )}
          </div>

          <div className={'grid grid-cols-2 gap-3 ' + (csvRows.length > 0 ? 'opacity-50 pointer-events-none' : '')}>
            <label>
              <div className="text-xs font-bold uppercase mb-1">{t('bulk.firstNameAll')}</div>
              <input className="nb-input" value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder={t('bulk.leaveBlank')} />
            </label>
            <label>
              <div className="text-xs font-bold uppercase mb-1">{t('bulk.lastNameAll')}</div>
              <input className="nb-input" value={lastName} onChange={(e) => setLastName(e.target.value)} placeholder={t('bulk.leaveBlank')} />
            </label>
            <label className="col-span-2">
              <div className="text-xs font-bold uppercase mb-1">{t('bulk.usernameNoAt')}</div>
              <input className="nb-input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder={t('bulk.leaveBlank')} />
              <div className="text-[10px] opacity-60 mt-1">
                {t('bulk.usernameUniqueHint')}
              </div>
            </label>
            <label className="col-span-2">
              <div className="text-xs font-bold uppercase mb-1">{t('bulk.bioMax')}</div>
              <textarea maxLength={70} className="nb-input" value={bio} onChange={(e) => setBio(e.target.value)} placeholder={t('bulk.leaveBlank')} />
            </label>
          </div>
          <label className={'flex items-center gap-2 mt-3 ' + (csvRows.length > 0 ? 'opacity-50 pointer-events-none' : '')}>
            <input type="checkbox" checked={appendNumber} onChange={(e) => setAppendNumber(e.target.checked)} />
            <span className="text-sm">{t('bulk.appendNumber', { un: username ? ' + ' + t('profile.username') : '', u: username ? ` / "${username}1", "${username}2"` : '' })}</span>
            {appendNumber && (
              <input type="number" min={1} className="nb-input !w-20 !py-1" value={startNumber}
                onChange={(e) => setStartNumber(Number(e.target.value) || 1)} />
            )}
          </label>

          <button className="nb-btn-pri mt-3" disabled={busy} onClick={applyProfile}>
            {t('bulk.applyProfileBtn', { count: ids.length })}
            {csvRows.length > 0 && ids.length > 0 && (
              <span className="ml-1 opacity-70">{t('bulk.usingCsv', { count: Math.min(ids.length, csvRows.length) })}</span>
            )}
          </button>
        </div>

        <div className="nb-card p-4">
          <h3 className="font-extrabold uppercase mb-3">{t('bulk.bulkProfilePhoto')}</h3>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <input ref={photoRef} type="file" accept="image/*" multiple onChange={addPhotos}
              className="text-xs file:mr-2 file:cursor-pointer file:border-2 file:border-black file:bg-white file:px-2 file:py-1 file:font-bold file:uppercase" />
            <button className="nb-btn !py-1 !px-2 text-xs" disabled={photos.length === 0} onClick={clearPhotos}>
              {t('bulk.clearAll')}
            </button>
            <div className="text-xs ml-auto">
              <span className="font-bold">{t('bulk.photos', { count: photos.length })}</span> •{' '}
              <span className="font-bold">{ids.length}</span> {t('bulk.accounts')}
              {photos.length > 0 && ids.length > 0 && (
                <span className={'ml-2 nb-badge text-black ' + (photos.length >= ids.length ? 'bg-brand-ok' : 'bg-brand-warn')}>
                  {photos.length >= ids.length ? t('bulk.enough') : t('bulk.needMore', { count: ids.length - photos.length })}
                </span>
              )}
            </div>
          </div>
          <div className="text-[11px] opacity-70 mb-2">
            {t('bulk.photoHint')}
          </div>
          {photos.length > 0 && (
            <div className="grid grid-cols-6 sm:grid-cols-8 gap-2 mb-3 max-h-72 overflow-auto p-2 bg-zinc-50 dark:bg-zinc-800 border-2 border-black dark:border-white">
              {photoThumbs.map((p, i) => (
                <div key={i} className="relative group">
                  <img src={p.url} alt={p.name} className="w-full aspect-square object-cover border-2 border-black dark:border-white" />
                  <span className="absolute top-0 left-0 bg-black text-white text-[10px] font-bold px-1">{i + 1}</span>
                  <button onClick={() => removePhoto(i)}
                    className="absolute top-0 right-0 bg-brand-err text-black text-[10px] font-bold px-1 opacity-0 group-hover:opacity-100 transition">
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
          <button className="nb-btn-pri" disabled={busy || photos.length === 0 || ids.length === 0} onClick={applyPhoto}>
            {t('bulk.applyPhotosBtn', { a: Math.min(photos.length, ids.length), b: ids.length })}
          </button>
        </div>
      </div>

      <div className="nb-card p-4 h-fit">
        <h3 className="font-extrabold uppercase mb-3">{t('bulk.pickAccounts')}</h3>
        <label className="flex items-center gap-2 mb-2">
          <input type="checkbox" checked={allChecked} onChange={toggleAll} />
          <span className="font-bold text-sm">{t('common.selectAll')} ({accounts.length})</span>
        </label>
        <div className="text-[10px] opacity-60 mb-2">
          {t('bulk.orderMatters')}
        </div>
        <div className="space-y-1 max-h-[60vh] overflow-auto">
          {accounts.map((a, i) => (
            <label key={a.id} className="flex items-center gap-2 p-1 cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800">
              <input type="checkbox" checked={ids.includes(a.id)} onChange={() => toggle(a.id)} />
              <span className="text-[10px] opacity-50 font-mono w-5">{ids.indexOf(a.id) + 1 || ''}</span>
              <span className="text-sm truncate flex-1">{(a.first_name + ' ' + a.last_name).trim() || a.phone}</span>
              <span className="text-xs opacity-60 font-mono">{a.status === 'connected' ? '●' : '○'}</span>
            </label>
          ))}
        </div>
      </div>

      <ProgressModal progress={progress} onClose={close} />

      {pending && (
        <ConfirmModal
          title={pending.title}
          message={pending.message}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          danger
          onConfirm={() => { const f = pending.onYes; setPending(null); f && f() }}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  )
}
