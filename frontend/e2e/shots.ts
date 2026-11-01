/**
 * Pictures of a place of the interface for the person who looks at a change: with `E2E_SHOTS` set to a folder, the page as
 * it stands is taken at the width of a laptop and of a phone, light and dark, in Chromium (the other engines draw the
 * same and would only take the same pictures again). Without `E2E_SHOTS` nothing happens; the test is the same.
 */
import { test, type Page } from '@playwright/test'
import path from 'node:path'

const SIZES = [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
] as const

export async function shots(page: Page, name: string, element?: string): Promise<void> {
  const folder = process.env.E2E_SHOTS
  if (!folder || test.info().project.name !== 'chromium') return
  const before = page.viewportSize()
  try {
    for (const size of SIZES) {
      for (const colorScheme of ['light', 'dark'] as const) {
        await page.setViewportSize(size)
        await page.emulateMedia({ colorScheme })
        await page.waitForTimeout(250)
        const file = path.join(folder, `${name}-${size.width}-${colorScheme === 'light' ? 'hell' : 'dunkel'}.png`)
        const target = element ? page.locator(element).first() : null
        if (target && (await target.count())) await target.screenshot({ path: file })
        else await page.screenshot({ path: file, fullPage: false })
      }
    }
  } finally {
    await page.emulateMedia({ colorScheme: 'light' })
    if (before) await page.setViewportSize(before)
  }
}
