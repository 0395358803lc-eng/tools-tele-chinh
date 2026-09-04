import i18n from '../i18n'

// Translate a backend error into the current language. Backend responses and
// streamed rows carry an optional machine-readable `error_code` + `error_params`
// (see backend/app/errors.py). A bare code is looked up under `errors.*`;
// a dotted code is treated as a full i18n key (e.g. `addAccount.imported`).
// If no translation exists we fall back to the original (English) detail string
// so nothing is lost.
export function errText(detail, error_code, error_params) {
  if (error_code) {
    const key = error_code.indexOf('.') >= 0 ? error_code : 'errors.' + error_code
    if (i18n.exists(key)) {
      return i18n.t(key, error_params || {})
    }
  }
  return detail
}

// Preferred text for a bulk row: structured `message_code`+`params` win,
// then `error_code`+`error_params` (translated via errText), then raw `detail`
// as a debug fallback. Never lets a raw English `detail` be the primary text
// when a machine-readable code exists.
export function rowText(r) {
  if (!r) return ''
  const code = r.message_code || r.error_code
  const params = r.params || r.error_params
  return errText(r.detail, code, params)
}