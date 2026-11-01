/** The person's menu holds, in this order: own account, settings, about, sign out. */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'

import '../i18n'
import { changeLanguage } from '../i18n'
import { AccountMenu } from './AccountMenu'

const me = { id: 1, name: 'jule', display_name: 'Jule', avatar: null, role: 'operator' }
const asMember = vi.hoisted(() => ({ on: false }))

vi.mock('../state/auth', () => ({ useAuth: () => ({ me: asMember.on ? { ...me, role: 'member' } : me, signOut: async () => undefined }) }))

let root: Root
let box: HTMLDivElement

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })))
  await changeLanguage('de', false)
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})

afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

async function open() {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <AccountMenu />
      </MemoryRouter>,
    )
  })
  await act(async () => {
    box.querySelector<HTMLButtonElement>('button[aria-haspopup]')!.click()
  })
}

describe('the account menu', () => {
  it('offers the own account, the settings, about nexbooks and signing out, in this order', async () => {
    await open()
    const entries = [...box.querySelectorAll('a, button:not([aria-haspopup])')].map((item) => [item.textContent?.trim(), item.getAttribute('href')])
    expect(entries).toEqual([
      ['Mein Konto', '/account'],
      ['Einstellungen', '/settings'],
      ['Über nexbooks', '/about'],
      ['Abmelden', null],
    ])
  })

  it('leaves the settings out for a member, as they belong to the operator', async () => {
    asMember.on = true
    try {
      await open()
      const entries = [...box.querySelectorAll('a, button:not([aria-haspopup])')].map((item) => item.textContent?.trim())
      expect(entries).toEqual(['Mein Konto', 'Über nexbooks', 'Abmelden'])
    } finally {
      asMember.on = false
    }
  })

  it('is the round picture alone, as big as the button of light and dark', async () => {
    await open()
    const trigger = box.querySelector('button[aria-haspopup]')!
    expect(trigger.textContent).toBe('J')
    expect(trigger.className).toContain('icon-btn')
    expect(trigger.className).toContain('round')
    expect(trigger.querySelector('span')!.getAttribute('style')).toContain('width: 40px')
  })
})
