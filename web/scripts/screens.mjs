import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from '@playwright/test'

// Run against a disposable seeded local stack: SCREEN_MEAL_ID / SCREEN_PRODUCT_ID,
// optional SCREEN_DAY / SCREEN_BASE_URL. PNGs and findings stay in ignored test-results.
const day = process.env.SCREEN_DAY ?? '2026-10-15'
const meal = process.env.SCREEN_MEAL_ID
const product = process.env.SCREEN_PRODUCT_ID
export const routes = [
  ['today', '/', 'Today'], ['day', `/day/${day}`, 'Today'],
  ['add', `/add/${day}/${meal}`, 'Add food'],
  ['progress', '/progress', 'Progress'], ['coach', '/coach', 'Coach'],
  ['me', '/me', 'Me'], ['advanced', '/me/advanced', 'Advanced'],
  ['profile', '/profile', 'Profile'], ['saved', '/saved', 'Saved meals'],
  ['aliases', '/aliases', 'Aliases'], ['product-new', '/products/new', 'New product'],
  ['product', `/products/${product}`, null],
  ['week-redirect', '/week', 'Progress', '/progress'],
  ['iso-week-redirect', '/week/2026-W42', 'Progress', '/progress'],
  ['weight-redirect', '/weight', 'Progress', '/progress'],
  ['settings-redirect', '/settings', 'Me', '/me'],
]
assert.equal(routes.length, 16, 'Capture every screen, Today alias, and legacy redirect')
assert.equal(new Set(routes.map(([name]) => name)).size, routes.length)
if (process.argv.includes('--check')) {
  console.log('Route inventory: 16 unique captures; layout checks run against the local stack.')
} else {
  assert.match(meal ?? '', /^[1-9]\d*$/, 'Set SCREEN_MEAL_ID to the disposable seeded meal')
  assert.match(product ?? '', /^[1-9]\d*$/, 'Set SCREEN_PRODUCT_ID to a seeded product')
  const base = process.env.SCREEN_BASE_URL ?? 'http://localhost:5173'
  const output = new URL('../test-results/screens/', import.meta.url)
  await mkdir(output, { recursive: true })
  const browser = await chromium.launch()
  const findings = []
  let label = 'setup'
  try {
    for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport, colorScheme: 'light' })
      const page = await context.newPage()
      page.on('console', message => {
        if (message.type() === 'error') findings.push(`${label}: console: ${message.text()}`)
      })
      page.on('pageerror', error => findings.push(`${label}: page: ${error.message}`))
      page.on('response', response => {
        if (response.status() >= 400) findings.push(`${label}: HTTP ${response.status()} ${new URL(response.url()).pathname}`)
      })
      for (const [name, path, heading, redirect] of routes) {
        label = `${viewport.width}-${name}`
        await page.goto(new URL(path, base).href)
        await page.waitForLoadState('networkidle')
        await page.locator('h1').waitFor()
        if (heading) assert.equal(await page.locator('h1').textContent(), heading, label)
        if (redirect) assert.equal(new URL(page.url()).pathname, redirect, label)
        const layout = await page.evaluate(() => {
          const nav = document.querySelector('nav[aria-label="Primary"]')
          const rect = nav.getBoundingClientRect()
          return {
            width: document.documentElement.scrollWidth, viewport: innerWidth,
            navPosition: getComputedStyle(nav).position,
            navBottom: rect.bottom, navWidth: rect.width,
            links: [...nav.querySelectorAll('a')].map(a => a.textContent),
            clipped: [...document.querySelectorAll('.app-content button, .app-content input, .app-content h1, .app-content p')]
              .filter(element => {
                const box = element.getBoundingClientRect()
                return box.width > 0 && (box.left < -1 || box.right > innerWidth + 1)
                  && !element.closest('.ui-tabs')
              }).map(element => element.textContent?.slice(0, 80) || element.tagName),
          }
        })
        try {
          assert.ok(layout.width <= layout.viewport, `${label}: horizontal overflow (${layout.width})`)
          assert.deepEqual(layout.clipped, [], `${label}: content clipped beyond viewport`)
          assert.deepEqual(layout.links, ['Today', 'Progress', 'Coach', 'Me'])
          assert.equal(layout.navPosition, viewport.width < 900 ? 'fixed' : 'sticky')
          if (viewport.width < 900) assert.equal(Math.round(layout.navBottom), viewport.height)
          else assert.equal(layout.navWidth, 180)
          if (name === 'day') {
            assert.match(await page.locator('.ui-macro--carbs').textContent(), /\?/, 'Unknown carbohydrate target stays unknown')
            assert.match(await page.locator('.ui-macro--fat').textContent(), /\?/, 'Unknown fat target stays unknown')
          }
          if (name === 'progress') {
            assert.equal(await page.locator('progress').first().evaluate(bar => getComputedStyle(bar).appearance), 'none', 'Calorie bars replace browser default appearance')
          }
          if (name === 'add') {
            const search = page.getByRole('searchbox', { name: 'Search food' })
            assert.equal(await search.evaluate(input => input === document.activeElement), true, 'Search autofocus')
            await search.fill('chicken')
            await page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/search'))
            await page.waitForLoadState('networkidle')
            assert.equal(await search.inputValue(), 'chicken')
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Search results fit viewport')
          }
        } catch (error) { findings.push(error.message) }
        await page.screenshot({ path: new URL(`${label}.png`, output).pathname.replace(/^\/(\w:)/, '$1'), fullPage: true })
        console.log(`Captured ${label}`)
      }
      await context.close()
    }
  } catch (error) {
    findings.push(`${label}: fatal: ${error.message}`)
    throw error
  } finally {
    await browser.close()
    await writeFile(new URL('findings.json', output), JSON.stringify(findings, null, 2))
  }
  findings.forEach(finding => console.error(finding))
  assert.equal(findings.length, 0, 'Review test-results/screens/findings.json')
}
