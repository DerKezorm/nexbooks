/**
 * The service worker itself (`public/sw.js`), run against a stand-in of its surroundings: a push shows as a
 * notification, and a tap opens a path of nexbooks and never another site, whatever the message says. Browsers drop
 * tabs and line breaks from an address before they read it; those tricks are among the cases.
 */
import source from '../public/sw.js?raw'

const ORIGIN = 'https://books.example.com'

type Handler = (event: unknown) => void

async function tap(url: unknown, agent = 'Mozilla/5.0 (iPhone)'): Promise<{ data: { url: string }; opened: string[] }> {
  const handlers: Record<string, Handler> = {}
  const shown: { title: string; options: { data: { url: string } } }[] = []
  const opened: string[] = []
  const self = {
    addEventListener: (type: string, handler: Handler) => (handlers[type] = handler),
    skipWaiting() {},
    clients: { claim() {}, matchAll: async () => [], openWindow: async (address: string) => opened.push(address) },
    registration: { showNotification: async (title: string, options: { data: { url: string } }) => shown.push({ title, options }) },
    navigator: { userAgent: agent },
    location: { origin: ORIGIN },
  }
  new Function('self', 'URL', source)(self, URL)
  let pending: Promise<unknown> = Promise.resolve()
  handlers.push({ data: { json: () => ({ title: 'nexbooks', body: 'A new sign-in', url, tag: 'x' }) }, waitUntil: (work: Promise<unknown>) => (pending = work) })
  await pending
  handlers.notificationclick({ notification: { data: shown[0].options.data, close() {} }, waitUntil: (work: Promise<unknown>) => (pending = work) })
  await pending
  return { data: shown[0].options.data, opened }
}

describe('the service worker', () => {
  it('opens a path of nexbooks where a tap leads', async () => {
    const { opened } = await tap('/discover?x=1')
    expect(opened).toEqual([ORIGIN + '/discover?x=1'])
  })

  it.each([
    'https://evil.example.org/x',
    '//evil.example.org',
    '/\t/evil.example.org/x',
    '/\n/evil.example.org',
    '/\r/evil.example.org',
    '/\\evil.example.org',
    'javascript:alert(1)',
    '/\u007f/evil.example.org',
    42,
  ])('never leads elsewhere: %j', async (url) => {
    const { data, opened } = await tap(url)
    expect(data.url).toBe('/')
    expect(opened).toEqual([ORIGIN + '/'])
  })
})

describe('the service worker has no fetch handler', () => {
  it('registers no handler for requests, so it never stands between the page and the server', () => {
    const handlers: Record<string, unknown> = {}
    const self = {
      addEventListener: (type: string, handler: unknown) => (handlers[type] = handler),
      skipWaiting() {},
      clients: { claim() {} },
      location: { origin: ORIGIN },
      navigator: { userAgent: 'Android' },
    }
    new Function('self', 'URL', source)(self, URL)
    expect(Object.keys(handlers).sort()).toEqual(['activate', 'install', 'notificationclick', 'push'])
  })
})
