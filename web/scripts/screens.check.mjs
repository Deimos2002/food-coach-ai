import assert from 'node:assert/strict'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test, { after } from 'node:test'

const findingsPath = new URL('../test-results/screens/findings.json', import.meta.url)
const originalFindings = existsSync(findingsPath) ? readFileSync(findingsPath) : null
mkdirSync(new URL('../test-results/screens/', import.meta.url), { recursive: true })
after(() => {
  if (originalFindings !== null) writeFileSync(findingsPath, originalFindings)
  else rmSync(findingsPath, { force: true })
})

function checkFatalCapture(executable = process.execPath) {
  writeFileSync(findingsPath, '[]')
  const result = spawnSync(executable, ['scripts/screens.mjs'], {
    cwd: new URL('../', import.meta.url),
    env: { ...process.env, SCREEN_BASE_URL: 'http://localhost:1', SCREEN_MEAL_ID: '1', SCREEN_PRODUCT_ID: '1' },
    encoding: 'utf8',
  })
  assert.ifError(result.error)
  assert.notEqual(result.status, 0)
  const findings = JSON.parse(readFileSync(findingsPath, 'utf8'))
  assert.ok(findings.some(finding => finding.startsWith('1280-today: fatal:')), 'Fatal navigation must be recorded with its route label')
}

test('fatal navigation writes a labelled finding before exiting', () => {
  checkFatalCapture()
})

test('a failed spawn cannot pass using stale findings', () => {
  writeFileSync(findingsPath, JSON.stringify(['1280-today: fatal: stale failure']))
  assert.throws(() => checkFatalCapture('foodcoach-nonexistent-node-executable'), /spawnSync/)
  assert.equal(readFileSync(findingsPath, 'utf8'), '[]')
})

if (!process.env.SCREEN_CHECK_CHILD) {
  test('regression preserves preexisting findings and an absent findings file', () => {
    const runChild = () => {
      const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url)], {
        env: { ...process.env, SCREEN_CHECK_CHILD: '1' }, encoding: 'utf8',
      })
      assert.ifError(result.error)
      assert.equal(result.status, 0, result.stderr || result.stdout)
    }
    const sentinel = '["real capture finding: preserve me"]'
    writeFileSync(findingsPath, sentinel)
    runChild()
    assert.equal(readFileSync(findingsPath, 'utf8'), sentinel)
    rmSync(findingsPath)
    runChild()
    assert.equal(existsSync(findingsPath), false)
  })
}
