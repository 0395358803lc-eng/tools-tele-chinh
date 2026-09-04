import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')
const src = path.join(root, 'src')

async function sourceFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  const out = []
  for (const entry of entries) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...await sourceFiles(full))
    else if (/\.(js|jsx)$/.test(entry.name)) out.push(full)
  }
  return out
}

test('frontend source keeps dangerous DOM/code execution primitives out', async () => {
  const forbidden = [
    ['dangerouslySetInnerHTML', /dangerouslySetInnerHTML/],
    ['direct innerHTML assignment', /\.innerHTML\s*=/],
    ['eval', /\beval\s*\(/],
    ['Function constructor', /\bnew\s+Function\s*\(/],
    ['document.cookie access', /document\.cookie/],
  ]

  for (const file of await sourceFiles(src)) {
    const code = await readFile(file, 'utf8')
    for (const [label, pattern] of forbidden) {
      assert.equal(pattern.test(code), false, `${label} found in ${path.relative(root, file)}`)
    }
  }
})

test('API client keeps cookies attached and centralized unauthorized handling', async () => {
  const code = await readFile(path.join(src, 'lib', 'api.js'), 'utf8')
  assert.match(code, /credentials:\s*['"]include['"]/)
  assert.match(code, /r\.status\s*===\s*401/)
  assert.match(code, /_onUnauth/)
})
