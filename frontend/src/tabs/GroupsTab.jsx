import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { CopyButton } from '../lib/CopyButton'
import ProgressModal from '../components/ProgressModal.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'
import { getLocale, kindText } from '../lib/util'

export default function GroupsTab({ accounts, selected }) {
  const { t } = useTranslation()
  const toast = useToast()
  const [target, setTarget] = useState('')
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [bulkIds, setBulkIds] = useState([])
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState(null) // {title,message,onYes}
  const { progress, run, close } = useBulkProgress()

  async function loadGroups(id) {
    setLoading(true)
    try { setGroups(await Endpoints.listGroups(id)) }
    catch (e) { toast.error(e.message); setGroups([]) }
    finally { setLoading(false) }
  }

  useEffect(() => {
    if (selected) loadGroups(selected.id)
    else setGroups([])
  }, [selected?.id])

  async function joinOne() {
    if (!selected) { toast.error(t('groups.selectAccountFirst')); return }
    if (!target.trim()) return
    setBusy(true)
    try {
      await Endpoints.joinGroup(selected.id, target)
      toast.success(t('groups.joined'))
      await loadGroups(selected.id)
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  async function bulkJoin() {
    if (!target.trim() || bulkIds.length === 0) { toast.error(t('groups.pickAccountsTarget')); return }
    setPending({
      title: t('groups.bulkJoinTitle', { target }),
      message: t('groups.joinTargetConfirm', { target, count: bulkIds.length }),
      onYes: () => doBulkJoin(),
    })
  }

  async function doBulkJoin() {
    setBusy(true)
    await run(t('groups.bulkJoinTitle', { target }), (onEvent) => Endpoints.bulkJoin(bulkIds, target, onEvent))
    setBusy(false)
    if (selected) loadGroups(selected.id)
  }

  async function bulkLeaveTarget() {
    if (!target.trim() || bulkIds.length === 0) { toast.error(t('groups.pickAccountsTarget')); return }
    setPending({
      title: t('groups.bulkLeaveTitle', { target }),
      message: t('groups.leaveTargetConfirm', { target, count: bulkIds.length }),
      onYes: () => doBulkLeaveTarget(),
    })
  }

  async function doBulkLeaveTarget() {
    setBusy(true)
    await run(t('groups.bulkLeaveTitle', { target }), (onEvent) => Endpoints.bulkLeaveTarget(bulkIds, target, onEvent))
    setBusy(false)
    if (selected) loadGroups(selected.id)
  }

  async function leaveOne(chat_id) {
    if (!selected) return
    setPending({
      title: t('groups.leaveGroupConfirm'),
      message: '',
      onYes: () => doLeaveOne(chat_id),
    })
  }

  async function doLeaveOne(chat_id) {
    setBusy(true)
    try {
      await Endpoints.leaveGroup(selected.id, chat_id)
      toast.success(t('groups.left'))
      await loadGroups(selected.id)
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  async function bulkLeave(chat_id) {
    if (bulkIds.length === 0) { toast.error(t('groups.selectAccounts')); return }
    setPending({
      title: t('groups.bulkLeaveCountTitle', { count: bulkIds.length }),
      message: t('groups.leaveCountConfirm', { count: bulkIds.length }),
      onYes: () => doBulkLeave(chat_id),
    })
  }

  async function doBulkLeave(chat_id) {
    setBusy(true)
    await run(t('groups.bulkLeaveCountTitle', { count: bulkIds.length }), (onEvent) => Endpoints.bulkLeave(bulkIds, chat_id, onEvent))
    setBusy(false)
    if (selected) loadGroups(selected.id)
  }

  async function bulkLeaveAll() {
    if (bulkIds.length === 0) { toast.error(t('groups.selectAccountsFirst')); return }
    setPending({
      title: t('groups.leaveAllTitle', { count: bulkIds.length }),
      message: t('groups.leaveAllConfirm', { count: bulkIds.length }),
      onYes: () => doBulkLeaveAll(),
    })
  }

  async function doBulkLeaveAll() {
    setBusy(true)
    await run(t('groups.leaveAllTitle', { count: bulkIds.length }), (onEvent) => Endpoints.bulkLeaveAll(bulkIds, onEvent))
    setBusy(false)
    if (selected) loadGroups(selected.id)
  }

  async function bulkDeleteAllMessages() {
    if (bulkIds.length === 0) { toast.error(t('groups.selectAccountsFirst')); return }
    setPending({
      title: t('groups.deleteAllMsgTitle', { count: bulkIds.length }),
      message: t('groups.deleteAllMsgConfirm', { count: bulkIds.length }),
      onYes: () => doBulkDeleteAllMessages(),
    })
  }

  async function doBulkDeleteAllMessages() {
    setBusy(true)
    await run(t('groups.deleteAllMsgTitle', { count: bulkIds.length }), (onEvent) => Endpoints.bulkDeleteMyMessages(bulkIds, 2000, onEvent))
    setBusy(false)
    if (selected) loadGroups(selected.id)
  }

  async function deleteMyMessages(chat_id, title) {
    if (!selected) return
    setBusy(true)
    try {
      toast.info(t('groups.countingMsgs'))
      const cnt = await Endpoints.countMyMessages(selected.id, chat_id, 2000)
      if (cnt.count === 0) {
        toast.info(t('groups.noMsgsFound'))
        setBusy(false)
        return
      }
      setPending({
        title: t('groups.deleteMsgsConfirm', {
          count: cnt.count,
          title,
          account: (selected.first_name || selected.phone),
        }),
        message: '',
        onYes: () => doDeleteMyMessages(chat_id, title),
      })
      setBusy(false)
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  async function doDeleteMyMessages(chat_id, title) {
    setBusy(true)
    try {
      const r = await Endpoints.deleteMyMessages(selected.id, chat_id, 2000)
      toast.success(t('groups.deletedMsgs', { count: r.deleted, title }))
      await loadGroups(selected.id)
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  const toggleId = (id) => setBulkIds((arr) => arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id])

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2">
        <div className="nb-card p-4 mb-4">
          <h3 className="font-extrabold uppercase mb-3">{t('groups.joinGroupChannel')}</h3>
          <div className="flex gap-2">
            <input
              className="nb-input"
              placeholder={t('groups.targetPlaceholder')}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
            <button className="nb-btn-pri" disabled={busy} onClick={joinOne}>{t('groups.joinOne')}</button>
            <button className="nb-btn" disabled={busy} onClick={bulkJoin}>{t('groups.bulkJoin', { count: bulkIds.length })}</button>
            <button className="nb-btn-err" disabled={busy} onClick={bulkLeaveTarget}>{t('groups.bulkLeave', { count: bulkIds.length })}</button>
          </div>
          <div className="text-[11px] opacity-60 mt-2">
            {t('groups.bulkLeaveHint')}
          </div>
        </div>

        <div className="nb-card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-extrabold uppercase">
              {selected ? t('groups.groupsChannels', { name: (selected.first_name || selected.phone) }) : t('groups.pickAccount')}
            </h3>
            {selected && (
              <button className="nb-btn !py-1 !px-2 text-xs" onClick={() => loadGroups(selected.id)}>{t('groups.refresh')}</button>
            )}
          </div>
          {loading && <div className="opacity-60 text-sm">{t('groups.loading')}</div>}
          {!loading && !selected && <div className="opacity-60 text-sm">{t('groups.selectAccount')}</div>}
          {!loading && selected && groups.length === 0 && <div className="opacity-60 text-sm">{t('groups.noGroups')}</div>}
          <div className="space-y-2">
            {groups.map((g) => (
              <div key={g.id} className="nb-card-sm p-3 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="font-bold truncate flex items-center gap-1">
                    {g.title}
                    {g.invite_link && <CopyButton value={g.invite_link} label={t('groups.link')} />}
                  </div>
                  <div className="text-xs opacity-70 truncate flex items-center gap-1">
                    <span className="nb-badge bg-white text-black !px-1 !py-0">{kindText(g.type)}</span>
                    {g.username && (
                      <span className="flex items-center gap-1">@{g.username}<CopyButton value={g.username} /></span>
                    )}
                    {g.members != null && <span>• {t('groups.members', { count: g.members.toLocaleString(getLocale()) })}</span>}
                  </div>
                </div>
                <button className="nb-btn-err !py-1 !px-2 text-xs" title={t('groups.delMyMsgsTitle')}
                  onClick={() => deleteMyMessages(g.id, g.title)} disabled={busy}>
                  {t('groups.delMyMsgs')}
                </button>
                <button className="nb-btn-err !py-1 !px-2 text-xs" onClick={() => leaveOne(g.id)}>{t('groups.leave')}</button>
                <button className="nb-btn !py-1 !px-2 text-xs" onClick={() => bulkLeave(g.id)}>{t('groups.bulkLeave', { count: bulkIds.length })}</button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="nb-card p-4 h-fit">
        <h3 className="font-extrabold uppercase mb-3">{t('groups.bulkSelection')}</h3>
        <div className="flex items-center gap-2 mb-2">
          <button className="nb-btn !py-1 !px-2 text-xs" onClick={() => setBulkIds(accounts.map((a) => a.id))}>{t('common.selectAll')}</button>
          <button className="nb-btn !py-1 !px-2 text-xs" onClick={() => setBulkIds([])}>{t('common.clear')}</button>
          <span className="text-xs opacity-70 ml-auto">{t('common.selected', { count: bulkIds.length })}</span>
        </div>
        <div className="space-y-1 max-h-[45vh] overflow-auto">
          {accounts.map((a) => (
            <label key={a.id} className="flex items-center gap-2 p-1 cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800">
              <input type="checkbox" checked={bulkIds.includes(a.id)} onChange={() => toggleId(a.id)} />
              <span className="text-sm truncate">{(a.first_name + ' ' + a.last_name).trim() || a.phone}</span>
            </label>
          ))}
        </div>

        {/* DANGER ZONE — acts on EVERY group of EVERY selected account */}
        <div className="mt-4 pt-3 border-t-2 border-black dark:border-white">
          <div className="text-[10px] uppercase font-extrabold tracking-tight text-brand-err mb-2">
            {t('groups.dangerZone')}
          </div>
          <div className="space-y-2">
            <button className="nb-btn-err w-full text-xs" disabled={busy || bulkIds.length === 0} onClick={bulkLeaveAll}>
              {t('groups.leaveAllGroups', { count: bulkIds.length })}
            </button>
            <button className="nb-btn-err w-full text-xs" disabled={busy || bulkIds.length === 0} onClick={bulkDeleteAllMessages}>
              {t('groups.deleteAllMyMsgs', { count: bulkIds.length })}
            </button>
          </div>
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
