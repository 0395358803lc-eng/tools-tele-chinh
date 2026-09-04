import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')

function flatten(obj, prefix = '', out = {}) {
  for (const [key, value] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      flatten(value, full, out)
    } else {
      out[full] = value
    }
  }
  return out
}

async function locale(name) {
  const raw = await readFile(path.join(root, 'src', 'i18n', 'locales', name), 'utf8')
  return flatten(JSON.parse(raw))
}

test('Vietnamese and English locale keys remain in exact parity', async () => {
  const [en, vi] = await Promise.all([locale('en.json'), locale('vi.json')])
  assert.deepEqual(Object.keys(vi).sort(), Object.keys(en).sort())
})

test('all translation leaves are non-empty strings', async () => {
  for (const name of ['en.json', 'vi.json']) {
    const values = await locale(name)
    for (const [key, value] of Object.entries(values)) {
      assert.equal(typeof value, 'string', `${name}:${key} must be a string`)
      assert.ok(value.trim().length > 0, `${name}:${key} must not be empty`)
    }
  }
})
