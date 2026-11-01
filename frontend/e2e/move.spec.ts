/**
 * Moving an edition into another library in a real browser against the real backend: the button on
 * the card of the e-book, the dialog with the target, the place the files will lie and what happens, the question, the
 * move in the background, and afterwards the files on the disk in the other library, the same edition with the place the
 * person had in it, and nothing left in the old folder. The libraries are made fresh for each engine
 * (`e2e/move-books.json`) and go again at the end.
 */
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { shots } from './shots'

const TAB = { 'X-Nexbooks-Client': 'tab-e2e-move0000' }
const SHOTS = process.env.E2E_SHOTS

test.describe.configure({ mode: 'serial', timeout: 180_000 })

let folder = ''
const made: number[] = []

function python(): string {
  const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  return fs.existsSync(path.join('..', 'backend', venv)) ? path.resolve('..', 'backend', venv) : 'python'
}

function files(root: string): string[] {
  const found: string[] = []
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else found.push(path.relative(root, full).split(path.sep).join('/'))
    }
  }
  if (fs.existsSync(root)) walk(root)
  return found.sort()
}

async function library(page: Page, name: string, where: string): Promise<number> {
  const answer = await page.request.post('/api/v1/libraries', { headers: TAB, data: { name, path: where, kind: 'both', scan_enabled: true } })
  expect(answer.ok(), await answer.text()).toBe(true)
  const id = ((await answer.json()) as { id: number }).id
  made.push(id)
  return id
}

/** The language of the account. A connection the server closed while the page was busy is opened again (Firefox's request
 * context keeps an idle one past the server's keep-alive). */
async function language(page: Page, code: string): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    try {
      const answer = await page.request.put('/api/v1/me/language', { headers: TAB, data: { language: code } })
      expect(answer.ok(), await answer.text()).toBe(true)
      return
    } catch (error) {
      if (attempt >= 3 || !String(error).includes('socket hang up')) throw error
    }
  }
}

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false })
}

test.afterAll(async ({ browser }) => {
  const context = await browser.newContext({ storageState: process.env.E2E_STATE })
  for (const id of made.splice(0)) await context.request.delete(`/api/v1/libraries/${id}`, { headers: TAB })
  await context.close()
})

test('moves the e-book into the other library: same edition, same place, files at the scheme, nothing left behind', async ({ page }, testInfo) => {
  folder = path.join(process.env.E2E_BOOKS!, `move-${testInfo.project.name}`)
  fs.rmSync(folder, { recursive: true, force: true })
  const source = path.join(folder, 'regal')
  const target = path.join(folder, 'annex')
  execFileSync(python(), ['tools/make_library.py', '--spec', path.resolve('e2e', 'move-books.json'), '--out', source], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  fs.mkdirSync(target, { recursive: true })
  const from = await library(page, `Regal ${testInfo.project.name}`, source)
  const to = await library(page, `Annex ${testInfo.project.name}`, target)
  await expect
    .poll(async () => ((await (await page.request.get('/api/v1/works/stats', { params: { library: from } })).json()) as { works: number }).works, { timeout: 90_000, intervals: [1000] })
    .toBe(2)
  const list = (await (await page.request.get('/api/v1/works', { params: { library: from, per_page: 50 } })).json()) as { items: { id: number; title: string; editions: { id: number; kind: string }[] }[] }
  const work = list.items.find((item) => item.title === 'Leuchtfeuer am Kai')!
  const ebook = work.editions.find((item) => item.kind === 'ebook')!.id
  expect((await page.request.put(`/api/v1/editions/${ebook}/progress`, { headers: TAB, data: { locator: 'epubcfi(/6/4!/4/2)', fraction: 0.37 } })).ok()).toBe(true)
  await language(page, 'de')
  try {
    await page.goto(`/work/${work.id}`)
    const button = page.locator('.twin[data-kind="ebook"] [data-testid="move-edition"]')
    await expect(button).toHaveText('In andere Bibliothek verschieben')
    // The recording's card offers it too (another library takes recordings as well).
    await expect(page.locator('.twin[data-kind="audio"] [data-testid="move-edition"]')).toHaveCount(1)
    await shots(page, 'verschieben-knopf', '.twins')
    await button.click()
    const dialog = page.getByRole('dialog', { name: 'In andere Bibliothek verschieben' })
    await expect(dialog.locator('[data-testid="move-target"]')).toHaveValue(String(to))
    await expect(dialog.locator('[data-testid="move-preview"] code')).toHaveText(['Ida Brandt/Leuchtfeuer am Kai (2021).epub'])
    await expect(dialog.locator('[data-testid="move-what"]')).toContainText('Die Fassung bleibt dieselbe')
    await shot(page, `move-dialog-${testInfo.project.name}`)
    await shots(page, 'verschieben-dialog', '[role="dialog"]')
    await dialog.locator('[data-act="move-go"]').click()
    const question = page.getByRole('dialog', { name: /verschieben\?$/ })
    await expect(question).toContainText('Zurück geht es auf demselben Weg.')
    await shots(page, 'verschieben-frage', '[role="dialog"]:last-of-type')
    await question.getByRole('button', { name: 'Verschieben' }).click()
    await expect(page.locator('[data-testid="work-status"]')).toContainText('Verschoben nach', { timeout: 60_000 })
    await expect(dialog.locator('[data-testid="move-state"]')).toHaveAttribute('data-state', 'done')
    await shot(page, `move-done-${testInfo.project.name}`)
    await shots(page, 'verschieben-fertig')
  } finally {
    await language(page, '')
  }
  // On the disk: the e-book in the other library, by the scheme; the old folder holds the recording only.
  expect(files(target)).toEqual(['Ida Brandt/Leuchtfeuer am Kai (2021).epub'])
  expect(files(source).filter((name) => name.endsWith('.epub'))).toEqual(['0001 Bleibt im Regal/Bleibt im Regal.epub'])
  // The same edition, in the other library, with the place the person had.
  const read = (await (await page.request.get(`/api/v1/editions/${ebook}`)).json()) as { edition: { id: number; library_id: number } }
  expect(read.edition).toMatchObject({ id: ebook, library_id: to })
  const place = (await (await page.request.get(`/api/v1/editions/${ebook}/progress`)).json()) as { fraction: number }
  expect(place.fraction).toBeCloseTo(0.37)
  // The scans afterwards see nothing gone and nothing new.
  for (const id of [from, to]) await page.request.post(`/api/v1/libraries/${id}/scan`, { headers: TAB, data: {} })
  await expect
    .poll(async () => {
      const mine = (await (await page.request.get('/api/v1/libraries')).json()) as { id: number; state: string }[]
      return mine.filter((item) => made.includes(item.id)).every((item) => item.state === 'idle')
    }, { timeout: 60_000, intervals: [500] })
    .toBe(true)
  const stats = async (id: number) => ((await (await page.request.get('/api/v1/works/stats', { params: { library: id } })).json()) as { editions: number }).editions
  expect([await stats(from), await stats(to)]).toEqual([2, 1])
})
