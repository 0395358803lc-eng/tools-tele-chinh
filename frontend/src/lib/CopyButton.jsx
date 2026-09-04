import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function CopyButton({ value, label = '' }) {
  const { t } = useTranslation()
  const [done, setDone] = useState(false)
  if (!value) return null
  const copy = async (e) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(String(value))
      setDone(true)
      setTimeout(() => setDone(false), 1500)
    } catch {}
  }
  return (
    <button
      onClick={copy}
      title={`${t('common.copy')} ${label || value}`}
      className={
        'inline-flex items-center justify-center w-6 h-6 border-2 border-black dark:border-white text-[10px] font-bold uppercase ' +
        (done ? 'bg-brand-ok text-black' : 'bg-white dark:bg-zinc-900 hover:bg-brand-pri hover:text-black')
      }
    >
      {done ? '✓' : '⧉'}
    </button>
  )
}
