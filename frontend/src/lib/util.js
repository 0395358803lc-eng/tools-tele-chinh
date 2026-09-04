import i18n from '../i18n'

export function getLocale() {
  return i18n.language === 'vi' ? 'vi-VN' : 'en-US'
}

// Localized bulk status label. Falls back to the raw status when no key exists
// so streaming rows never break on a novel status value from the backend.
const STATUS_KEYS = {
  ok: 'bulk.status.ok',
  success: 'bulk.status.success',
  failed: 'bulk.status.failed',
  skipped: 'bulk.status.skipped',
  pending: 'bulk.status.pending',
  running: 'bulk.status.running',
}
export function statusText(status) {
  const key = STATUS_KEYS[status] || 'bulk.status.unknown'
  return i18n.exists(key) ? i18n.t(key) : status
}

// Localized entity-kind word (group/supergroup/channel/bot/user/chat).
const KIND_KEYS = {
  group: 'chat.group',
  supergroup: 'chat.supergroup',
  channel: 'chat.channel',
  bot: 'chat.bot',
  user: 'checker.labelUser',
  chat: 'chat.chat',
}
export function kindText(kind) {
  const key = KIND_KEYS[kind]
  if (key && i18n.exists(key)) return i18n.t(key)
  return kind
}

export function initials(first, last) {
  const a = (first || '').trim()[0] || ''
  const b = (last  || '').trim()[0] || ''
  return (a + b).toUpperCase() || '?'
}

export function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString(getLocale(), { hour: '2-digit', minute: '2-digit', month: 'short', day: '2-digit' })
  } catch { return '' }
}

export function colorForString(s) {
  let h = 0
  for (const c of s || '') h = (h * 31 + c.charCodeAt(0)) % 360
  return `hsl(${h} 70% 60%)`
}

export function ensureNotificationPermission() {
  if (!('Notification' in window)) return Promise.resolve('unsupported')
  if (Notification.permission === 'default') return Notification.requestPermission()
  return Promise.resolve(Notification.permission)
}

export function desktopNotify(title, body) {
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title, { body })
    }
  } catch {}
}
