// The service worker of nexbooks: Web Push.
//
// It shows what the server sends (a notice of a new sign-in, a test) and opens nexbooks where a tap leads.
//
// It has no fetch handler: it never stands between the page and the server, so it keeps no pages and no answers of
// the API.

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

/**
 * Only a path of nexbooks itself, never another site. A browser drops tabs and line breaks from an address before it
 * reads it ("/" tab "//evil.example.org" becomes "//evil.example.org"), so control characters and backslashes are
 * refused outright, and what is left must still be of this origin once the browser has read it.
 */
function ownPath(value, fallback) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\u0000-\u001f\u007f\\]/.test(value)) return fallback
  try {
    const target = new URL(value, self.location.origin)
    return target.origin === self.location.origin ? target.pathname + target.search + target.hash : fallback
  } catch {
    return fallback
  }
}

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = {}
  }
  const title = typeof data.title === 'string' && data.title ? data.title.slice(0, 80) : 'nexbooks'
  const body = typeof data.body === 'string' ? data.body.slice(0, 600) : ''
  const target = ownPath(data.url, '/')
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      // PNG, not SVG: Android and Windows draw no SVG in a notification. The badge is read as a mask, white on clear.
      icon: '/icon-192.png',
      badge: '/badge-96.png',
      tag: typeof data.tag === 'string' ? data.tag.slice(0, 40) : undefined,
      data: { url: target },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = new URL(ownPath(event.notification.data && event.notification.data.url, '/'), self.location.origin)
  event.waitUntil(
    (async () => {
      const open = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of open) {
        if (new URL(client.url).origin !== self.location.origin) continue
        try {
          await client.focus()
          await client.navigate(target.href)
          return
        } catch {
          // A window this worker does not control cannot be led elsewhere: a new one instead.
          break
        }
      }
      await self.clients.openWindow(target.href)
    })(),
  )
})
