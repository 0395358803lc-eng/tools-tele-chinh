import { useState } from 'react'
import { Endpoints } from '../lib/api'
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
            <div className="text-xs opacity-70 mt-1">{r.detail}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TargetCheckTab() {
  const toast = useToast()
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  async function search() {
    const t = target.trim()
    if (!t) {
      toast.error('Enter a username or link')
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const r = await Endpoints.targetCheck(t)
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
        <div className="font-extrabold uppercase">Target Checker</div>
        <div className="text-sm opacity-70 mt-1">
          Search one bot, channel, group, or username across every connected account. It will show which accounts have already used or joined it, and which ones have not.
        </div>
        <div className="flex gap-2 mt-3">
          <input
            className="nb-input"
            placeholder="@username or t.me link"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }}
          />
          <button className="nb-btn-pri" disabled={busy} onClick={search}>
            {busy ? 'Checking...' : 'Search All Accounts'}
          </button>
        </div>
      </div>

      {totals && (
        <div className="flex flex-wrap gap-2">
          <span className="nb-badge bg-brand-ok text-black">found {totals.present}</span>
          <span className="nb-badge bg-brand-warn text-black">not found {totals.absent}</span>
          <span className="nb-badge bg-brand-violet text-black">skipped {totals.skipped}</span>
          <span className="nb-badge bg-brand-err text-black">failed {totals.failed}</span>
        </div>
      )}

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ItemList
            title={result.peer?.kind === 'bot' || result.peer?.kind === 'user' ? 'Not used yet' : 'Not joined yet'}
            items={result.absent || []}
            tone="bg-brand-warn"
          />
          <ItemList
            title={result.peer?.kind === 'bot' || result.peer?.kind === 'user' ? 'Already used' : 'Already joined'}
            items={result.present || []}
            tone="bg-brand-ok"
          />
          <ItemList title="Skipped" items={result.skipped || []} tone="bg-brand-violet" />
          <ItemList title="Failed" items={result.failed || []} tone="bg-brand-err" />
        </div>
      )}
    </div>
  )
}
