import { useTranslation } from 'react-i18next'

function Pill({ label, value, color = 'bg-white' }) {
  return (
    <div className={`nb-stat-sm px-3 py-1 ${color} flex items-center gap-2`}>
      <span className="text-[10px] font-extrabold uppercase">{label}</span>
      <span className="font-mono font-extrabold">{value}</span>
    </div>
  )
}

export default function TopStats({ stats, onBellClick }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Pill label={t('topstats.total')}     value={stats.total} />
      <Pill label={t('topstats.connected')} value={stats.connected} color="bg-brand-ok" />
      <Pill label={t('topstats.banned')}    value={stats.banned}    color="bg-brand-err" />
      <Pill label={t('topstats.twoFa')}     value={stats.with_2fa}  color="bg-brand-violet" />
      <button onClick={onBellClick} className="relative nb-stat-sm bg-white px-3 py-1 flex items-center gap-2 hover:translate-x-[1px] hover:translate-y-[1px] transition-transform">
        <span className="text-[10px] font-extrabold uppercase">{t('topstats.alerts')}</span>
        <span className="font-mono font-extrabold">{stats.unread_security}</span>
        {stats.unread_security > 0 && (
          <span className="absolute -top-2 -right-2 inline-flex items-center justify-center min-w-[20px] h-5 px-1 text-[10px] font-bold bg-brand-err text-black border-2 border-black">
            {stats.unread_security}
          </span>
        )}
      </button>
    </div>
  )
}
