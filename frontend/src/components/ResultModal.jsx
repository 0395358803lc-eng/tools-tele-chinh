import { useTranslation } from 'react-i18next'
import { rowText } from '../lib/err'
import { statusText } from '../lib/util'

export default function ResultModal({ title, success = 0, failed = 0, skipped = 0, results = [], onClose }) {
  const { t } = useTranslation()
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="nb-card p-6 w-full max-w-lg max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-extrabold uppercase tracking-tight">{title}</h2>
          <button className="nb-btn !py-1 !px-2" onClick={onClose}>✕</button>
        </div>
        <div className="flex gap-2 mb-4 flex-wrap">
          <span className="nb-badge bg-brand-ok text-black">{success} {t('result.success')}</span>
          <span className="nb-badge bg-brand-err text-black">{failed} {t('result.failed')}</span>
          <span className="nb-badge bg-brand-warn text-black">{skipped} {t('result.skipped')}</span>
        </div>
        <div className="space-y-1">
          {results.map((r, i) => (
            <div key={i} className="nb-card-sm p-2 text-sm flex items-center gap-2">
              <span className={'nb-badge ' + (r.status === 'ok' ? 'bg-brand-ok' : r.status === 'failed' ? 'bg-brand-err' : 'bg-brand-warn') + ' text-black'}>{statusText(r.status)}</span>
              <span className="font-mono text-xs">{r.phone}</span>
              {rowText(r) && <span className="text-xs opacity-70 truncate" title={rowText(r)}>{rowText(r)}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
