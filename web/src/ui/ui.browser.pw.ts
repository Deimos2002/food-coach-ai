/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const styles = ['index.css', 'ui/Button.css', 'ui/Pill.css', 'ui/Tabs.css']
  .map((file) => readFileSync(new URL(`../${file}`, import.meta.url), 'utf8')).join('\n')

test('danger button keeps AA text contrast while hovered', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await page.setContent(`<style>${styles}</style><div style="background: white; padding: 20px"><button class="ui-button ui-button--danger">Delete</button></div>`)
  const button = page.getByRole('button', { name: 'Delete' })
  await button.hover()
  const ratio = await button.evaluate((element) => {
    const style = getComputedStyle(element)
    const rgb = (color: string) => color.match(/[\d.]+/g)!.slice(0, 3).map(Number)
    const opacity = Number(style.opacity)
    const composite = (color: number[]) => color.map((channel) => channel * opacity + 255 * (1 - opacity))
    const luminance = (color: number[]) => color.map((channel) => {
      const c = channel / 255
      return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4
    }).reduce((sum, channel, index) => sum + channel * [.2126, .7152, .0722][index]!, 0)
    const foreground = luminance(composite(rgb(style.color)))
    const background = luminance(composite(rgb(style.backgroundColor)))
    return (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05)
  })
  console.log(`hover contrast: ${ratio.toFixed(2)}:1`)
  expect(ratio).toBeGreaterThanOrEqual(4.5)
})

for (const control of ['pill', 'tab']) {
  test(`${control} with a one-character label offers a 44px touch target`, async ({ page }) => {
    await page.setContent(`<style>${styles}</style><div style="display: inline-flex"><button class="${control === 'pill' ? 'ui-pill' : 'ui-tab'}">1</button></div>`)
    const bounds = await page.getByRole('button', { name: '1' }).boundingBox()
    console.log(`${control}: ${bounds?.width.toFixed(2)} x ${bounds?.height.toFixed(2)}px`)
    expect(bounds?.width).toBeGreaterThanOrEqual(44)
    expect(bounds?.height).toBeGreaterThanOrEqual(44)
  })
}
