/**
 * Settings, Tidying: off from the start, the switch, the library and the scheme, the preview with what moves and
 * what is in the way, the question before the run and before the undoing (and nothing sent until the answer is yes), the
 * progress, the words for a folder that is read-only, and the scheme that does not work.
 */
import { act } from 'react'

import type { LibraryAdmin, OrganizeRun, OrganizeStatus } from '../../api/client'
import { changeLanguage } from '../../i18n'
import { Page, Server, plain } from '../../test/harness'
import { idle } from '../../test/wait'
import { OrganizePart } from './OrganizeCards'

const server = new Server()
const page = new Page()

const lib = { id: 1, name: 'Familie', kind: 'both', path: '/books/familie', my_right: 'manage' } as unknown as LibraryAdmin
const SCHEME = '{Author}/{Series}/{Volume} - {Title} ({Year})'

function run(over: Partial<OrganizeRun> = {}): OrganizeRun {
  return {
    id: 9, library_id: 1, scheme: SCHEME, state: 'preview', total: 3, done: 0, error: '', created_at: '2026-10-13T10:00:00Z', started_at: null, finished_at: null,
    summary: { move_books: 2, stay_books: 1, problem_books: 1, files: 5, move_files: 3, problems: { exists: 1 }, empty: { Series: 1 }, digest: 'd'.repeat(64) }, ...over,
  }
}
function status(over: Partial<OrganizeStatus> = {}): OrganizeStatus {
  return { enabled: true, writable: true, reason: '', scheme: SCHEME, placeholders: [{ name: 'Author', aliases: ['author', 'autor'] }, { name: 'Title', aliases: ['title', 'titel'] }], runs: [], ...over }
}
const move = (id: number, state: string, over: Record<string, unknown> = {}) => ({ id, edition_id: id, title: `Buch ${id}`, source: `alt/${id}.epub`, target: `Autor/${id}.epub`, state, reason: '', ...over })

function serve(over: { enabled?: boolean; status?: OrganizeStatus; extra?: Record<string, unknown> } = {}) {
  server.answers = {
    'GET /api/v1/organize/settings': { enabled: over.enabled ?? true },
    'GET /api/v1/libraries/admin': [lib],
    'GET /api/v1/organize/status?library=1': over.status ?? status(),
    'GET /api/v1/organize/runs/9/moves?offset=0&limit=100&state=planned': { items: [move(1, 'planned'), move(2, 'planned')], total: 2 },
    'GET /api/v1/organize/runs/9/moves?offset=0&limit=100&state=problem': { items: [move(3, 'problem', { reason: 'exists' })], total: 1 },
    ...over.extra,
  }
  server.fails = {}
  server.install()
}

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

async function show(): Promise<void> {
  await page.show(<OrganizePart />)
  await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 60))))
  await idle()
}
const text = () => plain(document.body.textContent)

describe('the switch', () => {
  it('is off from the start, and nothing else is offered while it is off', async () => {
    serve({ enabled: false })
    await show()
    const toggle = document.querySelector('[role="switch"]') as HTMLElement
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(document.querySelector('#organize-run')).toBeNull()
    expect(text()).toContain('Ab Werk aus: nexbooks liest deine Ordner nur.')
  })

  it('is turned on by the operator and says so to the server', async () => {
    serve({ enabled: false, extra: { 'PUT /api/v1/organize/settings': { enabled: true } } })
    await show()
    await page.click(document.querySelector('[role="switch"]') as HTMLElement)
    expect(server.called('PUT', '/api/v1/organize/settings')!.body).toEqual({ enabled: true })
    expect((document.querySelector('[role="switch"]') as HTMLElement).getAttribute('aria-checked')).toBe('true')
  })
})

describe('the library and the scheme', () => {
  it('offer only the libraries the operator manages as a member', async () => {
    const other = { ...lib, id: 2, name: 'Nur gelesen', my_right: 'read' } as unknown as LibraryAdmin
    const none = { ...lib, id: 3, name: 'Fremd', my_right: null } as unknown as LibraryAdmin
    serve({ extra: { 'GET /api/v1/libraries/admin': [none, other, lib] } })
    await show()
    const options = [...document.querySelectorAll('#organize-run option')].map((item) => item.textContent)
    expect(options).toEqual(['Familie'])
    expect(server.called('GET', '/api/v1/organize/status?library=3')).toBeUndefined()
  })

  it('say so when libraries are left out, and when none is left', async () => {
    const none = { ...lib, id: 3, name: 'Fremd', my_right: null } as unknown as LibraryAdmin
    serve({ extra: { 'GET /api/v1/libraries/admin': [none, lib] } })
    await show()
    expect(plain(document.querySelector('[data-testid="org-kept"]')!.textContent)).toContain('die du als Mitglied verwaltest')
  })

  it('say it too when every library is closed to the operator, instead of asking for a folder', async () => {
    const none = { ...lib, id: 3, name: 'Fremd', my_right: null } as unknown as LibraryAdmin
    serve({ extra: { 'GET /api/v1/libraries/admin': [none] } })
    await show()
    expect(document.querySelector('#organize-run')).toBeNull()
    expect(plain(document.body.textContent)).toContain('die du als Mitglied verwaltest')
    expect(plain(document.body.textContent)).not.toContain('Lege zuerst unter Bibliotheken einen Ordner an')
  })

  it('show no hint when every library is offered', async () => {
    serve()
    await show()
    expect(document.querySelector('[data-testid="org-kept"]')).toBeNull()
  })

  it('offer the library, the scheme with its names, and say that nexbooks may write', async () => {
    serve()
    await show()
    expect((document.querySelector('#organize-run select') as HTMLSelectElement).value).toBe('1')
    expect((document.querySelector('#organize-run input') as HTMLInputElement).value).toBe(SCHEME)
    expect([...document.querySelectorAll('.org-names code')].map((c) => c.textContent)).toEqual(['{Author}', '{Title}'])
    expect(plain(document.querySelector('[data-testid="org-writable"]')!.textContent)).toBe('nexbooks darf in diesem Ordner schreiben.')
  })

  it('say what is wrong when the folder is mounted read-only, and that the preview still works', async () => {
    serve({ status: status({ writable: false, reason: 'read_only' }) })
    await show()
    const said = plain(document.querySelector('[data-testid="org-writable"]')!.textContent)
    expect(said).toContain('schreibgeschützt eingebunden')
    expect(said).toContain(':ro')
    expect(said).toContain('Die Vorschau geht')
  })

  it('say it too when there is no right to write', async () => {
    serve({ status: status({ writable: false, reason: 'no_permission' }) })
    await show()
    expect(plain(document.querySelector('[data-testid="org-writable"]')!.textContent)).toContain('kein Schreibrecht')
  })

  it('send the scheme that was typed with the preview', async () => {
    serve({ extra: { 'POST /api/v1/organize/preview': run() } })
    await show()
    await page.type(document.querySelector('#organize-run input') as HTMLInputElement, '{Author}/{Title}')
    await page.click(page.button('Vorschau'))
    expect(server.called('POST', '/api/v1/organize/preview')!.body).toEqual({ library_id: 1, scheme: '{Author}/{Title}' })
  })

  it('say why a scheme does not work, in words of its own', async () => {
    serve()
    server.fails = { 'POST /api/v1/organize/preview': { status: 422, code: 'scheme_invalid', values: { reason: 'no_title', detail: '' } } }
    server.install()
    await show()
    await page.click(page.button('Vorschau'))
    expect(plain(document.querySelector('[data-testid="org-scheme-problem"]')!.textContent)).toBe('Der letzte Teil braucht {Title} oder {Titel}.')
    server.fails = { 'POST /api/v1/organize/preview': { status: 422, code: 'scheme_invalid', values: { reason: 'unknown_placeholder', detail: 'Farbe' } } }
    server.install()
    await page.click(page.button('Vorschau'))
    expect(plain(document.querySelector('[data-testid="org-scheme-problem"]')!.textContent)).toBe('Diesen Namen gibt es nicht: Farbe')
  })
})

describe('the preview', () => {
  const withPreview = () => serve({ status: status({ runs: [run()] }) })

  it('counts the books that move, stay and are in the way, and lists the files with where they go', async () => {
    withPreview()
    await show()
    const summary = [...document.querySelectorAll('[data-testid="org-summary"] span')].map((s) => plain(s.textContent))
    expect(summary).toEqual(['2 Bücher werden verschoben, 3 Dateien', '1 Bücher liegen schon richtig', '1 Bücher bleiben liegen, weil etwas im Weg ist'])
    expect(plain(document.querySelector('[data-testid="org-planned"]')!.textContent)).toContain('alt/1.epub')
    expect(plain(document.querySelector('[data-testid="org-planned"]')!.textContent)).toContain('Autor/1.epub')
    expect(plain(document.querySelector('[data-testid="org-problem"]')!.textContent)).toContain('Am Ziel liegt schon etwas')
    expect(text()).toContain('Bei 1 Büchern fehlt ein Wert für Series')
  })

  it('asks before it starts, says what happens and whether it can be undone, and sends nothing until the answer is yes', async () => {
    withPreview()
    server.answers['POST /api/v1/organize/runs/9/start'] = run({ state: 'queued' })
    await show()
    await page.click(page.button('Ordnen starten'))
    expect(page.dialog).toBeTruthy()
    const said = plain(page.dialog!.textContent)
    expect(said).toContain('3 Dateien in „Familie“')
    expect(said).toContain('Nichts wird überschrieben oder gelöscht')
    expect(said).toContain('Du kannst es danach rückgängig machen.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Ordnen starten'))
    await page.inDialog('Ordnen')
    expect(server.changes()).toEqual(['POST /api/v1/organize/runs/9/start'])
    // The operator's yes goes with it: the server starts nothing without it.
    expect(server.called('POST', '/api/v1/organize/runs/9/start')!.body).toEqual({ confirm: true })
  })

  it('cannot be started where nexbooks may not write, nor when there is nothing to move', async () => {
    serve({ status: status({ writable: false, reason: 'read_only', runs: [run()] }) })
    await show()
    expect(page.button('Ordnen starten')!.disabled).toBe(true)
    page.close()
    serve({ status: status({ runs: [run({ total: 0, summary: { move_books: 0, stay_books: 4, problem_books: 0, files: 4, move_files: 0 } })] }) })
    await show()
    expect(page.button('Ordnen starten')!.disabled).toBe(true)
    expect(text()).toContain('Es gibt nichts zu ordnen')
  })

  it('can be thrown away', async () => {
    withPreview()
    server.answers['DELETE /api/v1/organize/runs/9'] = 204
    await show()
    await page.click(page.button('Vorschau verwerfen'))
    expect(server.changes()).toEqual(['DELETE /api/v1/organize/runs/9'])
  })

  it('says what the server refuses to start', async () => {
    withPreview()
    server.fails = { 'POST /api/v1/organize/runs/9/start': { status: 409, code: 'preview_stale' } }
    server.install()
    await show()
    await page.click(page.button('Ordnen starten'))
    await page.inDialog('Ordnen')
    expect(plain(page.dialog!.textContent)).toContain('Die Bibliothek hat sich seit der Vorschau geändert.')
  })
})

describe('a run', () => {
  it('shows how far it is while it goes on, and looks again', async () => {
    serve({ status: status({ runs: [run({ state: 'running', done: 1, total: 4 })] }) })
    await show()
    expect(plain(document.querySelector('[data-testid="org-progress"]')!.textContent)).toBe('Es wird geordnet: 1 von 4 Dateien')
    expect(document.querySelector('[role="progressbar"]')!.getAttribute('aria-valuenow')).toBe('25')
    expect(page.button('Ordnen starten')).toBeUndefined()
  })

  it('says it waits for its place in the queue', async () => {
    serve({ status: status({ runs: [run({ state: 'queued', total: 4 })] }) })
    await show()
    expect(plain(document.querySelector('[data-testid="org-progress"]')!.textContent)).toContain('wartet auf seinen Platz')
  })

  it('can be undone when it is done, after a question that says what happens and sends nothing until the answer is yes', async () => {
    serve({
      status: status({ runs: [run({ state: 'done', done: 3 })] }),
      extra: {
        'GET /api/v1/organize/runs/9/moves?offset=0&limit=100&state=moved': { items: [move(1, 'moved')], total: 1 },
        'POST /api/v1/organize/runs/9/undo': run({ state: 'undoing', done: 3 }),
      },
    })
    await show()
    expect(text()).toContain('Fertig: 3 Dateien sind an ihrem Platz.')
    await page.click(page.button('Rückgängig machen'))
    expect(plain(page.dialog!.textContent)).toContain('Alle Dateien dieses Laufs gehen an ihren alten Platz zurück.')
    expect(server.changes()).toEqual([])
    await page.inDialog('Abbrechen')
    expect(server.changes()).toEqual([])
    await page.click(page.button('Rückgängig machen'))
    await page.inDialog('Zurücksetzen')
    expect(server.changes()).toEqual(['POST /api/v1/organize/runs/9/undo'])
  })

  it('has no undo while it is read-only, and says what stopped a run that failed', async () => {
    serve({ status: status({ writable: false, reason: 'read_only', runs: [run({ state: 'failed', done: 1, error: 'no_write_access' })] }) })
    await show()
    expect(plain(document.querySelector('[data-testid="org-failed"]')!.textContent)).toBe('Angehalten. nexbooks darf nicht schreiben.')
    expect(page.button('Rückgängig machen')!.disabled).toBe(true)
  })

  it('says that everything is back when it was undone, and lists the earlier runs', async () => {
    serve({ status: status({ runs: [run({ state: 'undone', done: 3 }), run({ id: 8, state: 'done', scheme: '{Title}' })] }) })
    await show()
    expect(text()).toContain('Zurückgesetzt: Alles liegt wieder, wo es lag.')
    expect(plain(document.querySelector('[data-testid="org-history"]')!.textContent)).toContain('fertig')
    expect(page.button('Rückgängig machen')).toBeUndefined()
  })
})

describe('in English', () => {
  it('says the same', async () => {
    await changeLanguage('en', false)
    serve({ status: status({ runs: [run()] }) })
    await show()
    expect(text()).toContain('Off from the start')
    expect(page.button('Start tidying')).toBeTruthy()
  })
})
