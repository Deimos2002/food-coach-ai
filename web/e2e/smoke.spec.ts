import { expect, test } from '@playwright/test'

/**
 * End-to-end smoke test against the REAL backend (see docs/dev-setup.md): dev-auth JWKS server,
 * uvicorn backend, and a Postgres database with Ciqual imported. Runs at a phone viewport
 * (390x844) and walks the core logging loop plus a resize check for horizontal scroll.
 */
test('log, complete, delete, weigh, and fit on a phone', async ({ page }) => {
  await page.goto('/')

  // 1. Open Today.
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

  // 2. Add breakfast.
  const breakfast = page.getByTestId('meal-breakfast')
  await breakfast.getByRole('button', { name: 'Add' }).click()

  const sheet = page.getByRole('dialog', { name: 'Add item' })
  await expect(sheet).toBeVisible()

  // 3. Search "pain" and pick the first result.
  await sheet.getByRole('searchbox', { name: 'Search food' }).fill('pain')
  const results = sheet.getByRole('list', { name: 'Search results' })
  const firstResult = results.getByRole('listitem').first()
  await expect(firstResult).toBeVisible()
  await firstResult.getByRole('button').click()

  // 4. Enter 60 g and add it. Expect the item to appear with an energy value.
  await sheet.getByLabel('Amount').fill('60')
  await sheet.getByRole('button', { name: 'Add' }).click()
  await expect(sheet).not.toBeVisible()

  const itemRow = breakfast.getByRole('listitem').first()
  await expect(itemRow).toBeVisible()
  await expect(itemRow).not.toContainText('?')

  // 5. Mark the day complete. Expect "Based on your completed log".
  await page.getByRole('button', { name: 'Mark day complete' }).click()
  await expect(page.getByText('Based on your completed log')).toBeVisible()

  // 6. Delete the item. Expect the status to show "Partial".
  await itemRow.getByRole('button', { name: 'Edit' }).click()
  const editDialog = page.getByRole('dialog', { name: 'Edit item' })
  await editDialog.getByRole('button', { name: 'Delete' }).click()
  await editDialog.getByRole('button', { name: 'Confirm delete' }).click()
  await expect(page.getByText('Partial')).toBeVisible()

  // 7. Log a weight of 72.5. Expect it in the list.
  await page.getByRole('link', { name: 'Weight' }).click()
  await page.getByLabel("Today's weight (kg)").fill('72.5')
  await page.getByRole('button', { name: 'Log weight' }).click()
  await expect(page.getByText(/72[.,]5/)).toBeVisible()

  // 8. Resize to 360 px wide. Expect no horizontal scroll.
  await page.setViewportSize({ width: 360, height: 800 })
  const hasHorizontalScroll = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(hasHorizontalScroll).toBe(false)
})
