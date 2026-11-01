/**
 * The operator's card for e-readers (Settings, API), against a stand-in of the server: the switch, every connection with
 * whose it is (never the password), and blocking one only after the question, which says that it is for good.
 */
import { changeLanguage } from '../../i18n'
import { Page, Server } from '../../test/harness'
import { EreadersCard, type ServerSettings } from './ServerCards'

const server = new Server()
const page = new Page()

const settings: ServerSettings = {
  public_url: '', password_login: true, two_factor_required: true, backup_schedule: 'daily', backup_keep: 7, smtp_host: '', smtp_port: 587,
  smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '', api_keys_allowed: false, ereaders_allowed: true, update_check: true, scan_minutes: 60,
}
const save = vi.fn(async () => undefined)
const card = (over: Partial<ServerSettings> = {}) => ({ settings: { ...settings, ...over }, save, setSettings: vi.fn(), busy: false, problem: null, done: null, run: vi.fn() }) as unknown as Parameters<typeof EreadersCard>[0]['server']

const links = [
  { id: 3, account: 'anna', kind: 'opds', name: 'Wohnzimmer', prefix: 'nbo_Ab3d', created_at: '2026-10-01T10:00:00Z', last_used_at: null, blocked: false },
  { id: 4, account: 'ben', kind: 'opds', name: 'Tablet', prefix: 'nbo_Zz9y', created_at: '2026-10-01T10:00:00Z', last_used_at: null, blocked: true },
]

beforeEach(async () => {
  save.mockClear()
  await changeLanguage('de', false)
  server.answers = { 'GET /api/v1/admin/ereaders/links': links, 'POST /api/v1/admin/ereaders/links/3/block': { ...links[0], blocked: true } }
  server.fails = {}
  server.install()
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

describe('the card of the operator', () => {
  it('has a switch that saves at once and says what off means', async () => {
    await page.show(<EreadersCard server={card()} />)
    const toggle = document.querySelector<HTMLButtonElement>('button[role="switch"]')!
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    expect(page.text).toContain('vorhandene Verbindungen gehen nicht mehr')
    await page.click(toggle)
    expect(save).toHaveBeenCalledWith({ ereaders_allowed: false })
  })

  it('shows every connection with whose it is and its first characters, and never a password', async () => {
    await page.show(<EreadersCard server={card()} />)
    const rows = [...document.querySelectorAll('[data-testid="admin-ereader-links"] li')]
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('anna')
    expect(rows[0].textContent).toContain('nbo_Ab3d')
    expect(rows[0].textContent).toContain('Wohnzimmer')
    expect(rows[1].textContent).toContain('gesperrt')
    expect(rows[1].querySelector('button')).not.toBeNull()
    expect(rows[0].textContent).not.toContain('Löschen')
  })

  it('deletes a blocked connection only after the question, which says the block is lifted by it', async () => {
    server.answers['DELETE /api/v1/admin/ereaders/links/4'] = null
    await page.show(<EreadersCard server={card()} />)
    await page.click(page.button('Gesperrte Verbindung Tablet von ben löschen'))
    expect(server.changes()).toEqual([])
    expect(page.dialog!.textContent).toContain('Die Sperre ist danach aufgehoben')
    await page.click(page.button('Abbrechen', page.dialog!))
    expect(server.changes()).toEqual([])
    await page.click(page.button('Gesperrte Verbindung Tablet von ben löschen'))
    await page.inDialog('Löschen')
    expect(server.changes()).toEqual(['DELETE /api/v1/admin/ereaders/links/4'])
  })

  it('blocks a connection only after the question, which says that it is for good', async () => {
    await page.show(<EreadersCard server={card()} />)
    await page.click(page.button('Verbindung Wohnzimmer von anna sperren'))
    expect(server.changes()).toEqual([])
    expect(page.dialog!.textContent).toContain('für immer')
    await page.click(page.button('Abbrechen', page.dialog!))
    expect(server.changes()).toEqual([])
    await page.click(page.button('Verbindung Wohnzimmer von anna sperren'))
    await page.inDialog('Sperren')
    expect(server.changes()).toEqual(['POST /api/v1/admin/ereaders/links/3/block'])
  })

  it('is in English too', async () => {
    await changeLanguage('en', false)
    await page.show(<EreadersCard server={card()} />)
    expect(page.text).toContain('Allow e-readers')
    expect(page.button('Block the connection Wohnzimmer of anna')).toBeTruthy()
  })
})
