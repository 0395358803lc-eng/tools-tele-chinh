import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import ProgressModal from '../components/ProgressModal.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import ReactionBuilderModal from '../components/ReactionBuilderModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'

function AccountPicker({ accounts, ids, setIds, t }) {
  return (
    <div className="nb-card-sm p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="font-bold text-xs uppercase">{t('messaging.accounts')}</span>
        <button className="nb-btn !py-0.5 !px-1 text-[10px]" onClick={() => setIds(accounts.map((a) => a.id))}>{t('common.all')}</button>
        <button className="nb-btn !py-0.5 !px-1 text-[10px]" onClick={() => setIds([])}>{t('common.none')}</button>
        <span className="text-xs opacity-70 ml-auto">{t('common.selected', { count: ids.length })}</span>
      </div>
      <div className="flex flex-wrap gap-1 max-h-32 overflow-auto">
        {accounts.map((a) => (
          <label key={a.id} className={'nb-badge cursor-pointer ' + (ids.includes(a.id) ? 'bg-brand-pri text-black' : 'bg-white text-black')}>
            <input type="checkbox" className="mr-1"
              checked={ids.includes(a.id)}
              onChange={() => setIds((arr) => arr.includes(a.id) ? arr.filter((x) => x !== a.id) : [...arr, a.id])}
            />
            {(a.first_name || a.phone).slice(0, 14)}
          </label>
        ))}
      </div>
    </div>
  )
}

const keyOf = (e) => (e.custom_emoji_id ? `c:${e.custom_emoji_id}` : `s:${e.emoji}`)

// Split account ids across emojis by percentage. Shuffled so it's fair.
// Leftover accounts (when total% < 100) simply don't react. custom_emoji_id is
// carried through so premium custom emoji reactions reach the backend.
function distribute(ids, emojis) {
  const shuffled = [...ids]
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
  }
  const N = shuffled.length
  let cursor = 0
  const reactions = []
  for (const e of emojis) {
    let count = Math.min(Math.round((e.pct / 100) * N), N - cursor)
    const slice = shuffled.slice(cursor, cursor + count)
    cursor += count
    if (slice.length) reactions.push({ emoji: e.emoji, custom_emoji_id: e.custom_emoji_id || null, account_ids: slice })
  }
  return reactions
}

export default function MessagingTab({ accounts, selected }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()

  const [target, setTarget] = useState('')
  const [text, setText] = useState('')
  const [bulkIds, setBulkIds] = useState([])
  const [busy, setBusy] = useState(false)
  const [pendingSend, setPendingSend] = useState(false)
  const [pendingWipe, setPendingWipe] = useState(false)

  // react
  const [postLink, setPostLink] = useState('')
  const [emojis, setEmojis] = useState([{ emoji: '🔥', pct: 100 }]) // [{emoji, pct}]
  const [reactModal, setReactModal] = useState(false)
  const [reactIds, setReactIds] = useState([])

  // view
  const [viewLink, setViewLink] = useState('')
  const [viewIds, setViewIds] = useState([])

  // wipe DM / chat by username (deletes the whole conversation, both sides)
  const [wipeTarget, setWipeTarget] = useState('')
  const [wipeIds, setWipeIds] = useState([])

  const totalPct = emojis.reduce((s, e) => s + (Number(e.pct) || 0), 0)

  async function sendOne() {
    if (!selected) { toast.error(t('messaging.selectAcctFirst')); return }
    setBusy(true)
    try {
      await Endpoints.sendMessage(selected.id, target, text)
      toast.success(t('messaging.sent'))
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  async function sendBulk() {
    if (bulkIds.length === 0 || !target || !text) { toast.error(t('messaging.pickAcctsTargetText')); return }
    if (!pendingSend) { setPendingSend(true); return }
    setPendingSend(false)
    setBusy(true)
    await run(t('messaging.bulkSendTitle', { count: bulkIds.length }), (onEvent) => Endpoints.bulkSend(bulkIds, target, text, onEvent))
    setBusy(false)
  }

  async function doReact() {
    if (reactIds.length === 0 || !postLink || emojis.length === 0) { toast.error(t('messaging.pickAcctsLinkEmoji')); return }
    if (totalPct > 100) { toast.error(t('messaging.totalPctOver100')); return }
    const reactions = distribute(reactIds, emojis)
    if (reactions.length === 0) { toast.error(t('messaging.increasePct')); return }
    setBusy(true)
    await run(t('messaging.reactTitle'), (onEvent) => Endpoints.react(postLink, reactions, onEvent))
    setBusy(false)
  }

  async function doView() {
    if (viewIds.length === 0 || !viewLink) { toast.error(t('messaging.pickAcctsLink')); return }
    setBusy(true)
    await run(t('messaging.viewTitle', { count: viewIds.length }), (onEvent) => Endpoints.view(viewIds, viewLink, onEvent))
    setBusy(false)
  }

  async function doWipe() {
    const wt = wipeTarget.trim()
    if (wipeIds.length === 0 || !wt) { toast.error(t('messaging.pickAcctsUsername')); return }
    if (!pendingWipe) { setPendingWipe(true); return }
    setPendingWipe(false)
    setBusy(true)
    await run(t('messaging.wipeChatTitle', { target: wt, count: wipeIds.length }), (onEvent) => Endpoints.bulkWipeChat(wipeIds, wt, onEvent))
    setBusy(false)
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="nb-card p-4">
        <h3 className="font-extrabold uppercase mb-3">{t('messaging.sendMessage')}</h3>
        <input className="nb-input mb-2" placeholder={t('messaging.targetPlaceholder')}
          value={target} onChange={(e) => setTarget(e.target.value)} />
        <textarea className="nb-input min-h-[100px] mb-2" placeholder={t('messaging.messagePlaceholder')}
          value={text} onChange={(e) => setText(e.target.value)} />
        <div className="flex gap-2">
          <button className="nb-btn-pri flex-1" disabled={busy || !target || !text} onClick={sendOne}>
            {t('messaging.sendOne')}
          </button>
        </div>
        <div className="mt-4">
          <AccountPicker accounts={accounts} ids={bulkIds} setIds={setBulkIds} t={t} />
        </div>
        <button className="nb-btn mt-3 w-full" disabled={busy} onClick={sendBulk}>
          {t('messaging.bulkSendBtn', { count: bulkIds.length })}
        </button>
      </div>

      <div className="nb-card p-4">
        <h3 className="font-extrabold uppercase mb-3">{t('messaging.reactToPost')}</h3>
        <input className="nb-input mb-2" placeholder={t('messaging.postLinkPlaceholder')}
          value={postLink} onChange={(e) => setPostLink(e.target.value)} />

        {/* chosen reactions summary + open the % builder popup */}
        <div className="nb-card-sm p-3 mb-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="font-bold text-xs uppercase">{t('messaging.reactions')}</span>
            <button className="nb-btn !py-0.5 !px-2 text-[11px] ml-auto" onClick={() => setReactModal(true)}>
              {t('messaging.setReactionsPct')}
            </button>
          </div>
          {emojis.length === 0 ? (
            <div className="text-xs opacity-60">{t('messaging.noReactionsChosen')}</div>
          ) : (
            <div className="flex flex-wrap gap-1">
              {emojis.map((e) => (
                <span key={keyOf(e)} className="nb-badge bg-white text-black flex items-center gap-1">
                  <span className="text-base leading-none">{e.emoji}</span>
                  {e.custom_emoji_id && <span className="text-[9px] font-bold text-brand-violet" title={t('messaging.customEmoji')}>★</span>}
                  <span className="font-mono text-[11px]">{e.pct}%</span>
                </span>
              ))}
              <span className={'text-[11px] ml-auto font-bold self-center ' + (totalPct > 100 ? 'text-brand-err' : 'opacity-60')}>
                {t('messaging.total', { count: totalPct })}
              </span>
            </div>
          )}
        </div>

        <AccountPicker accounts={accounts} ids={reactIds} setIds={setReactIds} t={t} />
        <button className="nb-btn-pri mt-3 w-full" disabled={busy} onClick={doReact}>
          {t('messaging.sendReactions', { count: reactIds.length })}
        </button>
      </div>

      <div className="nb-card p-4 lg:col-span-2">
        <h3 className="font-extrabold uppercase mb-3">{t('messaging.viewOpenPost')}</h3>
        <input className="nb-input mb-2" placeholder={t('messaging.postLinkPlaceholder')}
          value={viewLink} onChange={(e) => setViewLink(e.target.value)} />
        <AccountPicker accounts={accounts} ids={viewIds} setIds={setViewIds} t={t} />
        <button className="nb-btn-pri mt-3" disabled={busy} onClick={doView}>
          {t('messaging.visitPost', { count: viewIds.length })}
        </button>
      </div>

      <div className="nb-card p-4 lg:col-span-2">
        <h3 className="font-extrabold uppercase mb-1 text-brand-err">{t('messaging.wipeDm')}</h3>
        <div className="text-[11px] opacity-70 mb-3">
          {t('messaging.wipeDesc')}
        </div>
        <input className="nb-input mb-2" placeholder={t('messaging.wipePlaceholder')}
          value={wipeTarget} onChange={(e) => setWipeTarget(e.target.value)} />
        <AccountPicker accounts={accounts} ids={wipeIds} setIds={setWipeIds} t={t} />
        <button className="nb-btn-err mt-3" disabled={busy || wipeIds.length === 0 || !wipeTarget.trim()} onClick={doWipe}>
          {t('messaging.wipeBtn', { count: wipeIds.length })}
        </button>
      </div>

      {reactModal && (
        <ReactionBuilderModal
          accountCount={reactIds.length}
          accountId={reactIds[0] ?? selected?.id ?? accounts[0]?.id ?? null}
          postLink={postLink}
          initial={emojis}
          onConfirm={(list) => { setEmojis(list); setReactModal(false) }}
          onClose={() => setReactModal(false)}
        />
      )}

      <ProgressModal progress={progress} onClose={close} />

      {pendingSend && (
        <ConfirmModal
          title={t('messaging.bulkSendTitle', { count: bulkIds.length })}
          message={t('messaging.sendBulkConfirm', { count: bulkIds.length })}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          onConfirm={() => setPendingSend(false) || sendBulk()}
          onCancel={() => setPendingSend(false)}
        />
      )}

      {pendingWipe && (
        <ConfirmModal
          title={t('messaging.wipeChatTitle', { target: wipeTarget.trim(), count: wipeIds.length })}
          message={t('messaging.wipeConfirm', { target: wipeTarget.trim(), count: wipeIds.length })}
          confirmLabel={t('common.yes')}
          cancelLabel={t('common.no')}
          danger
          onConfirm={() => setPendingWipe(false) || doWipe()}
          onCancel={() => setPendingWipe(false)}
        />
      )}
    </div>
  )
}
