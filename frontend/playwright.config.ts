/**
 * End to end, headless: the sign-in through a provider. The
 * built interface is served by the real backend, as in the container. Own data folder and own ports (the backend on
 * 18586, the stand-in sign-in provider on 18587, the stand-ins of the indexers and download programs on 18710), so that a running development server (8580, 5581) is never measured.
 *
 *   npx playwright install chromium firefox webkit     once
 *   npm run e2e                         build, start the backend and the stand-in, test
 */
import { defineConfig } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const PORT = Number(process.env.E2E_PORT ?? 18586)
const PROVIDER_PORT = Number(process.env.E2E_PROVIDER_PORT ?? 18587)
/** The stand-ins of the indexers, Prowlarr and the download programs (procure.spec.ts). */
const PROCURE_PORT = Number(process.env.E2E_PROCURE_PORT ?? 18710)

/** A fresh data folder per run. Made here and not in a global set-up: the server starts first. The workers load this
 * file again and inherit the folder through the environment. */
function dataDir(): string {
  if (process.env.E2E_DATA_DIR) return process.env.E2E_DATA_DIR
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nexbooks-e2e-'))
  process.env.E2E_DATA_DIR = dir
  return dir
}

const DATA_DIR = dataDir()
/** Where the hostile book of the reader tests (e2e/reader.spec.ts) tries to reach, and the listener that counts the visits. */
const CANARY_PORT = Number(process.env.E2E_CANARY_PORT ?? 18588)

/** The folder libraries may lie in, with a few invented books in it (backend/tools/make_library.py), made once per run:
 * the workers load this file again and find the folder in the environment. */
function booksDir(python: string): string {
  if (process.env.E2E_BOOKS) return process.env.E2E_BOOKS
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nexbooks-e2e-books-'))
  execFileSync(python, ['tools/make_library.py', '--spec', path.resolve('e2e', 'shelf-books.json'), '--out', path.join(dir, 'shelf')], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  // Ninety invented books with long and short titles, long names and up to four editions: the list (e2e/list.spec.ts).
  execFileSync(python, ['tools/make_library.py', '--spec', path.resolve('e2e', 'list-books.json'), '--out', path.join(dir, 'list')], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  // The books of the reader tests: a novel with a table of contents, a book with fixed pages, one that reads from right to
  // left, a MOBI, a PDF, books that are copy-protected, a hostile one and one that lies about its size.
  execFileSync(python, ['tools/make_reader_books.py', '--out', path.join(dir, 'reading'), '--canary', `http://127.0.0.1:${CANARY_PORT}`], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  // The recordings of player.spec.ts: chapters inside a file, parts in a folder, an hour, and Ogg Vorbis.
  execFileSync(python, ['tools/make_audio_books.py', '--out', path.join(dir, 'listening'), '--parts-out', path.join(dir, 'listening-parts')], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  // The hostile books of hostile.spec.ts: media types, CSS escapes, nesting.
  execFileSync(python, ['tools/make_hostile_books.py', '--out', path.join(dir, 'hostile'), '--canary', `http://127.0.0.1:${CANARY_PORT}`], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  // The libraries of the reading stack, the authors and tidying (stack.spec.ts): a library laid out by author with photos in
  // the authors' folders, and a few loose files to tidy.
  execFileSync(python, ['tools/make_author_books.py', '--out', path.join(dir, 'authors')], { cwd: path.join('..', 'backend'), stdio: 'ignore' })
  process.env.E2E_BOOKS = dir
  return dir
}
/** Where the operator's session is kept for the tests (global-setup.ts signs in once). */
export const SIGNED_IN = path.join(DATA_DIR, 'e2e-operator.json')
process.env.E2E_STATE = SIGNED_IN
process.env.E2E_PROVIDER = `http://127.0.0.1:${PROVIDER_PORT}`
process.env.E2E_PROCURE = `http://127.0.0.1:${PROCURE_PORT}`
// The project's venv on the development machines, the system Python elsewhere.
const venv = path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = fs.existsSync(path.join('..', 'backend', venv)) ? venv : 'python'
const venvPython = path.resolve('..', 'backend', venv)
const BOOKS = booksDir(fs.existsSync(venvPython) ? venvPython : 'python')
/** Where the stand-in download program says its finished jobs are (acquire.spec.ts): the backend may read below it. */
function downloadsDir(): string {
  if (process.env.E2E_DOWNLOADS) return process.env.E2E_DOWNLOADS
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nexbooks-e2e-downloads-'))
  process.env.E2E_DOWNLOADS = dir
  return dir
}
const DOWNLOADS = downloadsDir()

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
    locale: 'en-US',
    storageState: SIGNED_IN,
  },
  // All three engines: the walls around a book are not the same in each (Safari's frames need `allow-scripts`, the others'
  // run none), and a wall that holds in one says nothing about the others.
  // The shelf's animation times and the sign-in through a provider are measured in Chromium; the reader, the hostile books, the
  // player, Discover and the reading stack with the list, the authors and tidying (their pages, filters and wish list in a
  // real field) are the ones that must hold in every engine, and so is the bar of a library being read (its width is
  // measured on the screen).
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'firefox', use: { browserName: 'firefox' }, testMatch: /(reader|hostile|player|browse|discover|scanning|stack|procure|releases|notice|acquire|filing|automation|target|list|move|pairing|devicecode|authors|review|peek|search|watching)\.spec\.ts/ },
    { name: 'webkit', use: { browserName: 'webkit' }, testMatch: /(reader|hostile|player|browse|discover|scanning|stack|procure|releases|notice|acquire|filing|automation|target|list|move|pairing|devicecode|authors|review|peek|search|watching)\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT} --no-proxy-headers --no-server-header`,
      cwd: path.join('..', 'backend'),
      url: `http://127.0.0.1:${PORT}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 90_000,
      env: {
        NEXBOOKS_DATA_DIR: DATA_DIR,
        NEXBOOKS_FRONTEND_DIST: path.resolve('dist'),
        // The books of the shelf are read by the background loop (the scan of the library the tests make).
        NEXBOOKS_LIBRARY_ROOTS: BOOKS,
        NEXBOOKS_DOWNLOAD_ROOTS: DOWNLOADS,
        NEXBOOKS_ARGON2_TIME: '1',
        NEXBOOKS_ARGON2_MEMORY_KIB: '8192',
        NEXBOOKS_ARGON2_PARALLELISM: '1',
        // Never GitHub from a test: port 9 refuses at once.
        NEXBOOKS_UPDATE_URL: 'http://127.0.0.1:9/releases/latest',
        // The code the first account brings; global-setup.ts sends it.
        NEXBOOKS_SETUP_TOKEN: 'e2e-setup-code',
        NEXBOOKS_COOKIE_SUFFIX: '_e2e',
      },
    },
    // The listener of the hostile book (reader.spec.ts): every visit is counted and none may come.
    {
      command: 'node e2e/canary.mjs',
      url: `http://127.0.0.1:${CANARY_PORT}/health`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: { CANARY_PORT: String(CANARY_PORT) },
    },
    // A stand-in sign-in provider (nexoidc.spec.ts): signs in at once whoever POST /oidc/next named.
    {
      command: 'node e2e/fake-provider.mjs',
      url: `http://127.0.0.1:${PROVIDER_PORT}/health`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: { FAKE_PROVIDER_PORT: String(PROVIDER_PORT) },
    },
    // Stand-ins for an indexer, Prowlarr, SABnzbd and qBittorrent (procure.spec.ts): the far end of the way out is made up,
    // the way itself (pinned address, real sockets) is the backend's own.
    {
      command: 'node e2e/fake-procure.mjs',
      url: `http://127.0.0.1:${PROCURE_PORT}/health`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: { PROCURE_PORT: String(PROCURE_PORT) },
    },
  ],
})
