const BASE = ''  // proxied by vite

import i18n from '../i18n'
import { errText } from './err'

// listeners notified when any request returns 401
const _onUnauth = new Set()
export function onUnauthorized(cb) { _onUnauth.add(cb); return () => _onUnauth.delete(cb) }

// Localize an API error message. `msg` is the raw detail text and `body` may
// carry `error_code`/`error_params` from the backend. Also maps the frontend's
// own "Cannot reach server" string (it has a literal errors.* key).
function localizeApiError(msg, body) {
  if (body) {
    const localized = errText(msg, body.error_code, body.error_params)
    if (localized !== msg) return localized
  }
  const known = i18n.exists('errors.' + msg)
  if (known) return i18n.t('errors.' + msg)
  return msg
}

async function request(method, path, { json, form, query, silent } = {}) {
  let url = path
  if (query) {
    const q = new URLSearchParams(
      Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString()
    if (q) url += '?' + q
  }
  const opts = { method, headers: {}, credentials: 'include' }
  if (json !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(json)
  } else if (form) {
    opts.body = form
  }
  let r
  try {
    r = await fetch(BASE + url, opts)
  } catch (netErr) {
    // network/CORS/abort — surface a cleaner message
    const e = new Error(localizeApiError('Cannot reach server'))
    e.status = 0
    e.network = true
    throw e
  }
  let body = null
  try { body = await r.json() } catch { /* may not be json */ }
  if (r.status === 401 && !path.startsWith('/api/auth-app/')) {
    _onUnauth.forEach((fn) => { try { fn() } catch {} })
  }
  if (!r.ok) {
    const msg = body?.detail || body?.message || `${r.status} ${r.statusText}`
    const err = new Error(localizeApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), body))
    err.status = r.status
    err.body = body
    throw err
  }
  return body
}

export const api = {
  get:  (p, query)        => request('GET',  p, { query }),
  post: (p, json)         => request('POST', p, { json }),
  put:  (p, json)         => request('PUT',  p, { json }),
  del:  (p)               => request('DELETE', p),
  postForm: (p, form)     => request('POST', p, { form }),
}

// POST and consume a newline-delimited JSON (NDJSON) stream, calling onEvent(obj)
// for each line as it arrives. Shared by every live bulk task. `init` is passed
// straight to fetch() so callers can stream a JSON body OR a multipart FormData.
async function streamRequest(path, init, onEvent) {
  let r
  try {
    r = await fetch(BASE + path, { credentials: 'include', ...init })
  } catch {
    const e = new Error(localizeApiError('Cannot reach server')); e.status = 0; e.network = true; throw e
  }
  if (r.status === 401) _onUnauth.forEach((fn) => { try { fn() } catch {} })
  if (!r.ok || !r.body) {
    let detail = `${r.status} ${r.statusText}`
    let body = null
    try { body = await r.json(); detail = body?.detail || detail } catch { /* not json */ }
    const err = new Error(localizeApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), body))
    err.status = r.status
    throw err
  }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let nl
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim()
        buf = buf.slice(nl + 1)
        if (line) onEvent(JSON.parse(line))
      }
    }
    const last = buf.trim()
    if (last) onEvent(JSON.parse(last))
  } finally {
    try { reader.cancel() } catch { /* already closed */ }
  }
}

// Stream an NDJSON response from a JSON POST body.
export function streamNDJSON(path, body, onEvent) {
  return streamRequest(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, onEvent)
}

// Stream an NDJSON response from a multipart/form-data POST (file uploads).
export function streamNDJSONForm(path, form, onEvent) {
  return streamRequest(path, { method: 'POST', body: form }, onEvent)
}

// Convenience endpoints
export const Endpoints = {
  health: () => api.get('/api/health'),
  stats: () => api.get('/api/stats'),
  accounts: () => api.get('/api/accounts'),
  account: (id) => api.get(`/api/accounts/${id}`),
  connectAccount: (id) => api.post(`/api/accounts/${id}/connect`),
  disconnectAccount: (id) => api.post(`/api/accounts/${id}/disconnect`),
  connectAll: () => api.post('/api/accounts/connect_all'),
  disconnectAll: () => api.post('/api/accounts/disconnect_all'),
  deleteAccount: (id) => api.del(`/api/accounts/${id}`),
  removeAllAccounts: (password) => api.post('/api/accounts/remove_all', { password }),

  // gone / banned account history
  goneAccounts: () => api.get('/api/gone_accounts'),
  clearGoneAccounts: () => api.del('/api/gone_accounts'),
  deleteGoneAccount: (id) => api.del(`/api/gone_accounts/${id}`),

  sendCode: (phone) => api.post('/api/auth/send_code', { phone }),
  signIn: (phone, code) => api.post('/api/auth/sign_in', { phone, code }),
  signIn2fa: (phone, password) => api.post('/api/auth/sign_in_2fa', { phone, code: '', password }),
  authCancel: (phone) => api.post('/api/auth/cancel', { phone }),
  importSessions: (files) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    return api.postForm('/api/auth/import_sessions', fd)
  },
  syncSessionsFolder: () => api.post('/api/auth/sync_sessions_folder'),

  qrStart:    () => api.post('/api/auth/qr/start'),
  qrRecreate: (qr_id) => api.post('/api/auth/qr/recreate', { qr_id }),
  qrPoll:     (qr_id) => api.post('/api/auth/qr/poll', { qr_id }),
  qrSignIn2fa:(qr_id, password) => api.post('/api/auth/qr/sign_in_2fa', { qr_id, password }),
  qrCancel:   (qr_id) => api.post('/api/auth/qr/cancel', { qr_id }),

  updateProfile: (id, payload) => api.put(`/api/accounts/${id}/profile`, payload),
  checkUsername: (id, username) => api.get(`/api/accounts/${id}/profile/check_username`, { username }),
  updateUsername: (id, username) => api.put(`/api/accounts/${id}/profile/username`, { username }),
  photoUrl: (id) => api.get(`/api/accounts/${id}/profile/photo_url`),
  uploadPhoto: (id, file) => {
    const fd = new FormData(); fd.append('file', file)
    return api.postForm(`/api/accounts/${id}/profile/photo`, fd)
  },

  // bulk profile/photo stream live progress (NDJSON), same as the other bulk tasks
  bulkProfile: (payload, onEvent) => streamNDJSON('/api/bulk/profile', payload, onEvent),
  bulkPhoto: (ids, files, onEvent) => {
    const fd = new FormData()
    fd.append('account_ids', ids.join(','))
    for (const f of files) fd.append('files', f)
    return streamNDJSONForm('/api/bulk/photo', fd, onEvent)
  },

  securityMessages: (accountId, onlyUnread) =>
    api.get('/api/security/messages', { account_id: accountId, only_unread: onlyUnread }),
  markRead: (id) => api.post(`/api/security/messages/${id}/read`),
  markAllRead: (accountId) => api.post('/api/security/messages/mark_all_read' + (accountId ? `?account_id=${accountId}` : '')),
  backfillSecurity: (accountId, limit = 50) => api.post(`/api/security/messages/${accountId}/backfill?limit=${limit}`),
  tgSessions: (id) => api.get(`/api/security/sessions/${id}`),
  terminateSession: (id, hash) => api.del(`/api/security/sessions/${id}/${hash}`),
  terminateOthers: (id) => api.post(`/api/security/sessions/${id}/terminate_others`),
  terminateOthersAll: (onEvent) => streamNDJSON('/api/security/sessions/terminate_others_all', {}, onEvent),

  // bulk 2FA: how many current passwords we already remember from login, and the
  // streaming change run (tries remembered pw first, then the bank; ≤5 per account)
  twofaKnown: () => api.get('/api/security/twofa_known'),
  bulk2fa: (payload, onEvent) => streamNDJSON('/api/security/bulk_2fa', payload, onEvent),

  joinGroup: (id, target) => api.post(`/api/groups/${id}/join`, { target }),
  bulkJoin: (ids, target, onEvent) => streamNDJSON('/api/groups/bulk_join', { account_ids: ids, target }, onEvent),
  listGroups: (id) => api.get(`/api/groups/${id}/list`),
  leaveGroup: (id, chat_id) => api.post(`/api/groups/${id}/leave`, { chat_id }),
  bulkLeave: (ids, chat_id, onEvent) => streamNDJSON('/api/groups/bulk_leave', { account_ids: ids, chat_id }, onEvent),
  // leave ONE target (by @username / invite link) from selected accounts that are members
  bulkLeaveTarget: (ids, target, onEvent) => streamNDJSON('/api/groups/bulk_leave_target', { account_ids: ids, target }, onEvent),
  // leave EVERY group/channel each selected account is in
  bulkLeaveAll: (ids, onEvent) => streamNDJSON('/api/groups/bulk_leave_all', { account_ids: ids }, onEvent),
  // delete every message each selected account sent across ALL its groups/channels
  bulkDeleteMyMessages: (ids, max_scan, onEvent) =>
    streamNDJSON('/api/groups/bulk_delete_my_messages', { account_ids: ids, max_scan }, onEvent),
  countMyMessages: (id, chat_id, max_scan = 1000) =>
    api.get(`/api/groups/${id}/my_messages_count`, { chat_id, max_scan }),
  deleteMyMessages: (id, chat_id, max_scan = 2000) =>
    api.post(`/api/groups/${id}/delete_my_messages?chat_id=${chat_id}&max_scan=${max_scan}`),

  sendMessage: (id, target, text) => api.post(`/api/messaging/${id}/send`, { target, text }),
  bulkSend: (ids, target, text, onEvent) => streamNDJSON('/api/messaging/bulk_send', { account_ids: ids, target, text }, onEvent),
  // wipe the ENTIRE chat with one user (by @username / t.me link) from selected
  // accounts: clears history for both sides (revoke) and removes the dialog
  bulkWipeChat: (ids, target, onEvent) => streamNDJSON('/api/messaging/bulk_wipe_chat', { account_ids: ids, target }, onEvent),

  // Telegram-like chat panel: open by @username / t.me link (referral links
  // like t.me/Bot?start=CODE fire the bot /start), poll history, send.
  openChat: (id, input, limit = 40) => api.post(`/api/messaging/${id}/open`, { input, limit }),
  chatHistory: (id, peer, limit = 40) => api.get(`/api/messaging/${id}/history`, { peer, limit }),
  chatSend: (id, peer, text) => api.post(`/api/messaging/${id}/chat_send`, { peer, text }),
  targetCheck: (target) => api.post('/api/messaging/target_check', { target }),
  // which reactions this post's chat actually allows (standard + custom emoji)
  allowedReactions: (post_link, account_id) => api.post('/api/messaging/allowed_reactions', { post_link, account_id }),
  // reactions: [{ emoji, account_ids, custom_emoji_id? }]
  react: (post_link, reactions, onEvent) => streamNDJSON('/api/messaging/react', { post_link, reactions }, onEvent),
  view: (ids, post_link, onEvent) => streamNDJSON('/api/messaging/view', { account_ids: ids, post_link }, onEvent),

  getSettings: () => api.get('/api/settings'),
  putSettings: (s) => api.put('/api/settings', s),
  exportJson: () => api.get('/api/settings/export'),
  createBackup: () => api.post('/api/settings/backup'),
  backups: () => api.get('/api/settings/backups'),
  diagnostics: () => api.get('/api/settings/diagnostics'),
  logs: (limit = 100, errors_only = false) => api.get('/api/settings/logs', { limit, errors_only }),
  openLogFolder: () => api.post('/api/settings/logs/open-folder'),

  // app auth
  me:     () => api.get('/api/auth-app/me'),
  login:  (password) => api.post('/api/auth-app/login', { password }),
  logout: () => api.post('/api/auth-app/logout'),
}
