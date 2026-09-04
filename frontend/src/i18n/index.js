import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import en from './locales/en.json'
import vi from './locales/vi.json'

const STORAGE_KEY = 'app_language'

export function getSavedLanguage() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved === 'en' || saved === 'vi' ? saved : 'vi'
  } catch {
    return 'vi'
  }
}

export function setSavedLanguage(lang) {
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch { /* local storage unavailable */ }
}

function syncHtmlLang(lang) {
  try {
    document.documentElement.lang = lang === 'vi' ? 'vi' : 'en'
  } catch { /* document unavailable (SSR/early) */ }
}
syncHtmlLang(getSavedLanguage())

i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      vi: { translation: vi },
    },
    lng: getSavedLanguage(),
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })

i18n.on('languageChanged', syncHtmlLang)
i18n.on('initialized', syncHtmlLang)

export default i18n
