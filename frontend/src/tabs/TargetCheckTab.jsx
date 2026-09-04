import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { errText } from '../lib/err'
import i18n from '../i18n'
import { useToast } from '../lib/toast.jsx'

function ItemList({ title, items, tone }) {
  if (!items.length) return null
  return (
    <div className="nb-card-sm p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className={'nb-badge text-black ' + tone}>{title}</span>
        <span className="text-xs opacity-60">{items.length}</span>
      </div>
      <div className="space-y-2 max-h-72 overflow-auto">
        {items.map((r) => (
          <div key={r.id} className="border-2 border-black dark:border-white p-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-bold truncate">{r.name}</span>
              <span className="text-xs opacity-60 ml-auto font-mono">{r.phone}</span>
            </div>
            <div className="text-xs opacity-70 mt-1">{errText(r.detail, r.error_code, errParams(r.error_params))}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

const LABEL_WORDS = { bot: 'chat.bot', user: 'checker.labelUser', channel: 'chat.channel', group: 'chat.group', chat: 'chat.chat' }

function errParams(params) {
  if (!params || !params.label) return params
  const key = LABEL_WORDS[params.label]
  return { ...params, label: key ? i18n.t(key) : params.label }
}

export default function TargetCheckTab() {
  const { t } = useTranslation()
  const toast = useToast()
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  async function search() {
    const tt = target.trim()
    if (!tt) {
      toast.error(t('checker.enterUsername'))
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const r = await Endpoints.targetCheck(tt)
      setResult(r)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const totals = result
    ? {
        present: result.present?.length || 0,
        absent: result.absent?.length || 0,
        skipped: result.skipped?.length || 0,
        failed: result.failed?.length || 0,
      }
    : null

  return (
    <div className="space-y-4">
      <div className="nb-card p-4">
        <div className="font-extrabold uppercase">{t('checker.title')}</div>
        <div className="text-sm opacity-70 mt-1">
          {t('checker.desc')}
        </div>
        <div className="flex gap-2 mt-3">
          <input
            className="nb-input"
            placeholder={t('checker.placeholder')}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }}
          />
          <button className="nb-btn-pri" disabled={busy} onClick={search}>
            {busy ? t('checker.searchBtnChecking') : t('checker.searchBtn')}
          </button>
        </div>
      </div>

      {totals && (
        <div className="flex flex-wrap gap-2">
          <span className="nb-badge bg-brand-ok text-black">{t('checker.found', { count: totals.present })}</span>
          <span className="nb-badge bg-brand-warn text-black">{t('checker.notFound', { count: totals.absent })}</span>
          <span className="nb-badge bg-brand-violet text-black">{t('checker.skippedCount', { count: totals.skipped })}</span>
          <span className="nb-badge bg-brand-err text-black">{t('checker.failedCount', { count: totals.failed })}</span>
        </div>
      )}

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ItemList
            title={result.peer?.kind === 'bot' || result.peer?.kind === 'user' ? t('checker.notUsedYet') : t('checker.notJoinedYet')}
            items={result.absent || []}
            tone="bg-brand-warn"
          />
          <ItemList
            title={result.peer?.kind === 'bot' || result.peer?.kind === 'user' ? t('checker.alreadyUsed') : t('checker.alreadyJoined')}
            items={result.present || []}
            tone="bg-brand-ok"
          />
          <ItemList title={t('checker.skipped')} items={result.skipped || []} tone="bg-brand-violet" />
          <ItemList title={t('checker.failed')} items={result.failed || []} tone="bg-brand-err" />
        </div>
      )}
    </div>
  )
}
