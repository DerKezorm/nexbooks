/**
 * A library nexbooks cannot write in (here: its folder is gone, which the probe of "Ordnen" reports as `missing`; a folder
 * mounted read-only reads the same way) is named in the dialog "Beschaffen" and in the settings "Formate und Ablage", in a real
 * browser against the real backend, and the dialog does not offer it as the place by default.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-target' }
const FAR = process.env.E2E_PROCURE!
const SHOTS = process.env.E2E_SHOTS

test.describe.configure({ mode: 'serial' })

let annex = 0
let folder = ''
let indexer = 0

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false })
}

/** Other specs leave wishes and downloads of their own behind: the page of the book must show a recording still to ask for. */
function forget(): void {
  const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const python = fs.existsSync(path.join('..', 'backend', venv)) ? path.join('..', 'backend', venv) : 'python'
  const code = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1], timeout=30)',
    "for table in ('downloads', 'procure_wishes', 'procure_history', 'foreign_jobs', 'path_mappings', 'release_searches', 'blocklist'):",
    "    db.execute('delete from ' + table)",
    'db.commit()',
  ].join(String.fromCharCode(10))
  execFileSync(python, ['-c', code, path.join(process.env.E2E_DATA_DIR!, 'nexbooks.db')], { stdio: 'ignore' })
}

test.beforeEach(async ({ page }, testInfo) => {
  forget()
  const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; name: string }[]
  if (!mine.some((item) => item.name === 'Regal')) {
    const made = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Regal', path: path.join(process.env.E2E_BOOKS!, 'shelf'), kind: 'both', scan_enabled: true } })
    expect(made.ok(), await made.text()).toBe(true)
  }
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats')).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBeGreaterThanOrEqual(6)
  // A second library that sorts before "Regal" and whose folder is gone again at once.
  folder = path.join(process.env.E2E_BOOKS!, `annex-${testInfo.project.name}`)
  fs.mkdirSync(folder, { recursive: true })
  const second = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name: 'Annex', path: folder, kind: 'both', scan_enabled: false } })
  expect(second.ok(), await second.text()).toBe(true)
  annex = ((await second.json()) as { id: number }).id
  fs.rmSync(folder, { recursive: true, force: true })
  expect((await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: 'de' } })).ok()).toBe(true)
  const keys = (await (await page.request.get(`${FAR}/__keys`)).json()) as { indexer: string }
  const made = await page.request.post('/api/v1/procure/indexers', { headers: TAB, data: { name: 'Bücherei', kind: 'newznab', url: `${FAR}/indexer/api`, api_key: keys.indexer, priority: 25, daily_limit: 0, language: 'de' } })
  expect(made.ok(), await made.text()).toBe(true)
  indexer = ((await made.json()) as { id: number }).id
})

test.afterEach(async ({ page }) => {
  await page.request.delete(`/api/v1/procure/indexers/${indexer}`, { headers: TAB })
  await page.request.delete(`/api/v1/libraries/${annex}`, { headers: TAB })
  await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: '' } })
  fs.rmSync(folder, { recursive: true, force: true })
})

test('the dialog names a library that takes nothing and does not make it the default place', async ({ page }) => {
  const works = (await (await page.request.get('/api/v1/works', { params: { per_page: 200 } })).json()) as { items: { id: number; title: string }[] }
  const work = works.items.find((item) => item.title.startsWith('Schwarzer'))!.id
  await page.goto(`/work/${work}`)
  await page.locator('.twin.missing[data-kind="audio"] [data-testid="acquire"]').click()
  const dialog = page.getByRole('dialog', { name: 'Beschaffen' })
  const place = dialog.locator('#acq-lib')
  // The library that takes nothing is not left out, it is greyed out with its reason.
  await expect(place.locator('option')).toHaveText(['Nach Art (E-Books in Regal, Hörbücher in Regal)', 'Annex (nicht beschreibbar: nicht erreichbar)', 'Regal'])
  await expect(place.locator(`option[value="${annex}"]`)).toBeDisabled()
  await expect(dialog.locator('[data-testid="acquire-unwritable"]')).toHaveText('Ausgegraut, weil nexbooks dort nicht schreiben darf: Annex (nicht erreichbar).')
  await expect(place).toHaveValue('auto')
  await expect(dialog.locator('[data-testid="acquire-sum"]')).toContainText('Ablage in Regal.')
  await expect(dialog.locator('[data-testid="acquire-readonly"]')).toHaveCount(0)
  await shot(page, 'app-dialog-place')
  await shots(page, 'beschaffen-ausgegraut', '[role="dialog"]')
})

test('the settings say at once that the chosen place cannot be written to, and grey out the others that cannot', async ({ page }) => {
  // The place chosen while the library could still be written to: it stays chosen and says what is wrong.
  const before = (await (await page.request.get('/api/v1/procure/formats')).json()) as Record<string, unknown> & { library: Record<string, number | null> }
  const keep = { ...before }
  for (const key of ['libraries', 'known_ebook', 'known_audio', 'known_languages', 'samples']) delete keep[key]
  const chosen = await page.request.put('/api/v1/procure/formats', { headers: TAB, data: { ...keep, library: { ...before.library, audio: annex } } })
  expect(chosen.ok(), await chosen.text()).toBe(true)
  await page.goto('/settings?tab=procure&sub=formats')
  const audio = page.locator('select[aria-label="Hörbücher legt nexbooks in"]')
  await expect(audio).toHaveValue(String(annex))
  await expect(audio.locator(`option[value="${annex}"]`)).toHaveText('Annex (nicht beschreibbar: nicht erreichbar)')
  const ebook = page.locator('select[aria-label="E-Books legt nexbooks in"]')
  await expect(ebook.locator(`option[value="${annex}"]`)).toBeDisabled()
  const hint = page.locator('[data-testid="unwritable-audio"]')
  await expect(hint).toContainText('„Annex“ ist für nexbooks nicht erreichbar.')
  await expect(hint).toContainText('Beschreibbar sind: Regal.')
  await hint.scrollIntoViewIfNeeded()
  await page.evaluate(() => window.scrollBy(0, 160))
  await shot(page, 'app-settings-place')
  await shots(page, 'ablage-ausgegraut', '#formats-place')
  await audio.selectOption('')
  await expect(hint).toHaveCount(0)
  await expect(audio.locator(`option[value="${annex}"]`)).toBeDisabled()
})
