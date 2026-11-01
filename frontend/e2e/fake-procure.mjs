// Stand-ins for the end-to-end tests of fetching books (procure.spec.ts, releases.spec.ts), never anything real: one server on 127.0.0.1 that
// plays a Newznab indexer (/indexer/api), a Prowlarr (/prowlarr/...) with two indexers behind it, a SABnzbd (/sab/api) and a
// qBittorrent (/qbt/api/v2/...). The keys are made up for the run and handed to the tests through GET /__keys. Every
// visit is counted: GET /__hits tells what came. What the browser tests reach is the real way out of the backend (pinned
// address, real sockets), only the far end is made up.
import crypto from 'node:crypto'
import http from 'node:http'

const PORT = Number(process.env.PROCURE_PORT ?? 18710)
const KEYS = {
  indexer: crypto.randomBytes(12).toString('hex'),
  prowlarr: crypto.randomBytes(12).toString('hex'),
  sab: crypto.randomBytes(12).toString('hex'),
  qbt: crypto.randomBytes(12).toString('hex'),
}
const SESSION = crypto.randomBytes(8).toString('hex')
let hits = []
const sabCategories = []
const qbtCategories = {}
/** Added to the group of the releases of "Schwarzer Sand" (releases.spec.ts): each browser loads releases of its own, so that
 * the one of another browser is not "on its way already" in the same data folder. Set by GET /__reset?salt=... */
let salt = ''
/** The jobs SABnzbd was handed (releases.spec.ts, acquire.spec.ts): nzo_id to what it shows of the job. The tests move a job on
 * with GET /__sab?job=last&pct=40, &storage=... (done) or &fail=... (failed), and add one nobody asked for with
 * GET /__sab/foreign?name=... */
const sabJobs = {}
let sabLast = ''

const CAPS = `<?xml version="1.0" encoding="UTF-8"?>
<caps><server title="Example"/><limits max="100" default="50"/>
<searching><search available="yes" supportedParams="q"/><book-search available="yes" supportedParams="q,author,isbn"/></searching>
<categories>
<category id="3000" name="Audio"><subcat id="3030" name="Audio/Audiobook"/><subcat id="3130" name="Audio/Audiobook German"/></category>
<category id="7000" name="Books"><subcat id="7020" name="Books/EBook"/><subcat id="7030" name="Books/Comics"/><subcat id="7120" name="Books/EBook German"/><subcat id="7320" name="Books/EBook French"/></category>
</categories></caps>`

const FEED = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="http://www.newznab.com/dtd/2010/feeds/attributes/"><channel><title>Example</title>
<item><title>Example.Author.The.Lantern.Road.2019.EPUB-EXAMPLE</title><guid>https://indexer.example.com/details/1</guid>
<enclosure url="https://indexer.example.com/getnzb/1.nzb" length="1048576" type="application/x-nzb"/><newznab:attr name="category" value="7020"/></item>
</channel></rss>`

/** What a search for the invented book "Schwarzer Sand" by Jonas Lindholm finds (releases.spec.ts): a recording that fits, an
 * abridged one, the e-book there is already, and one in another language. The links lead back here. */
function searchFeed() {
  const item = (n, title, category, size) => `<item><title>${title}${salt}</title><guid>release-${n}</guid>
<pubDate>Mon, 12 Oct 2026 10:00:00 +0000</pubDate>
<enclosure url="http://127.0.0.1:${PORT}/indexer/get/${n}?apikey=${KEYS.indexer}" length="${size}" type="application/x-nzb"/>
<newznab:attr name="category" value="${category}"/><newznab:attr name="size" value="${size}"/><newznab:attr name="grabs" value="${10 + n}"/></item>`
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="http://www.newznab.com/dtd/2010/feeds/attributes/"><channel><title>Example</title>
${item(1, 'Jonas.Lindholm-Schwarzer.Sand-MP3CD-DE-2021-EXAMPLE', 3130, 640000000)}
${item(2, 'Jonas Lindholm-Schwarzer Sand (Gekuerzt)-WEB-MP3-DE-2020-EXAMPLE', 3130, 210000000)}
${item(3, 'Jonas.Lindholm.-.Die.Deich-Krimis.02.-.Schwarzer.Sand.GERMAN.EPUB-EXAMPLE', 7120, 2300000)}
${item(4, 'Jonas.Lindholm.-.Schwarzer.Sand.FRENCH.EPUB-EXAMPLE', 7320, 2100000)}
</channel></rss>`
}

/** A longer list for the pictures: the invented book "Stimmen im Moor" by Greta Ostermann, with the
 * kinds of release the dialog shows (formats, another language, a bundle, another author, a sample, abridged, a play). */
function richFeed() {
  const rows = [
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.EPUB-EXAMPLE', 7120, 2300000, 4],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.AZW3-TESTGRP', 7120, 2600000, 9],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.RETAIL.MOBI-DEMOGRP', 7120, 2900000, 21],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.ENGLISH.EPUB-SAMPLEX', 7020, 2100000, 13],
    ['Greta.Ostermann.Gesamtausgabe.Moorland.Band.1-5.GERMAN.EPUB.Collection-TESTGRP', 7120, 12400000, 30],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.RETAIL.PDF-DEMOGRP', 7120, 9800000, 17],
    ['Jonas.Lindholm.Stimmen.im.Moor.2019.GERMAN.EPUB-SAMPLEX', 7120, 1900000, 6],
    ['Greta.Ostermann.Stimmen.im.Moor.Leseprobe.GERMAN.EPUB-EXAMPLE', 7120, 200000, 2],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.Ungekuerzt.M4B.AAC.128kbps-EXAMPLE', 3130, 412000000, 11],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.Ungekuerzt.FLAC-DEMOGRP', 3130, 2610000000, 25],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.Ungekuerzt.MP3.192kbps-TESTGRP', 3130, 690000000, 8],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.Gekuerzt.MP3.128kbps-SAMPLEX', 3130, 210000000, 16],
    ['Greta.Ostermann.Stimmen.im.Moor.2019.GERMAN.Hoerspiel.MP3.128kbps-DEMOGRP', 3130, 380000000, 5],
  ]
  const day = (n) => new Date(Date.UTC(2026, 9, 15) - n * 86400000).toUTCString()
  const items = rows.map(([title, category, size, age], n) => `<item><title>${title}</title><guid>rich-${n}</guid><pubDate>${day(age)}</pubDate>
<enclosure url="http://127.0.0.1:${PORT}/indexer/get/${100 + n}?apikey=${KEYS.indexer}" length="${size}" type="application/x-nzb"/>
<newznab:attr name="category" value="${category}"/><newznab:attr name="size" value="${size}"/><newznab:attr name="grabs" value="${40 + n * 7}"/></item>`)
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="http://www.newznab.com/dtd/2010/feeds/attributes/"><channel><title>Example</title>
${items.join('')}
</channel></rss>`
}

const NZB = `<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><file poster="a@example.com" date="1" subject="x"><groups><group>alt.example</group></groups>
<segments><segment bytes="10" number="1">a@example.com</segment></segments></file></nzb>`

const PROWLARR_INDEXERS = [
  { id: 1, name: 'Example Books Usenet', enable: true, protocol: 'usenet', priority: 10, fields: [{ name: 'baseSettings.queryLimit', value: 150 }, { name: 'baseSettings.limitsUnit', value: 0 }],
    capabilities: { categories: [{ id: 7000, name: 'Books', subCategories: [{ id: 7020, name: 'Books/EBook', subCategories: [] }, { id: 7030, name: 'Books/Comics', subCategories: [] }] }, { id: 3000, name: 'Audio', subCategories: [{ id: 3030, name: 'Audio/Audiobook', subCategories: [] }] }] } },
  { id: 2, name: 'Example Movies Only', enable: true, protocol: 'usenet', priority: 25, fields: [], capabilities: { categories: [{ id: 2000, name: 'Movies', subCategories: [{ id: 2040, name: 'Movies/HD', subCategories: [] }] }] } },
]

function send(response, status, body, type = 'application/json', headers = {}) {
  response.writeHead(status, { 'Content-Type': type, ...headers })
  response.end(typeof body === 'string' ? body : JSON.stringify(body))
}

http
  .createServer((request, response) => {
    let raw = ''
    request.on('data', (chunk) => (raw += chunk))
    request.on('end', () => {
      const url = new URL(request.url, `http://127.0.0.1:${PORT}`)
      const path = url.pathname
      if (path === '/health') return send(response, 200, { ok: true })
      if (path === '/__keys') return send(response, 200, KEYS)
      if (path === '/__hits') return send(response, 200, hits)
      if (path === '/__reset') {
        hits = []
        for (const id of Object.keys(sabJobs)) delete sabJobs[id]
        salt = (url.searchParams.get('salt') ?? '').replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 12)
        return send(response, 200, { ok: true })
      }
      hits.push(`${request.method} ${path}`)
      const query = url.searchParams
      // A Newznab indexer: the key is the query parameter apikey.
      if (path === '/indexer/api' || /^\/prowlarr\/\d+\/api$/.test(path)) {
        const key = path === '/indexer/api' ? KEYS.indexer : KEYS.prowlarr
        if (query.get('apikey') !== key) return send(response, 200, '<?xml version="1.0"?><error code="100" description="Incorrect user credentials"/>', 'application/rss+xml')
        if (query.get('t') === 'caps') return send(response, 200, CAPS, 'application/rss+xml')
        if (query.get('t') === 'search' && /schwarzer/i.test(query.get('q') ?? '')) return send(response, 200, searchFeed(), 'application/rss+xml')
        if (query.get('t') === 'search' && /stimmen/i.test(query.get('q') ?? '')) return send(response, 200, richFeed(), 'application/rss+xml')
        return send(response, 200, FEED, 'application/rss+xml')
      }
      // The tests move the jobs of the stand-in SABnzbd on.
      if (path === '/__sab') {
        const job = query.get('job') === 'last' ? sabLast : query.get('job')
        const entry = sabJobs[job]
        if (!entry) return send(response, 404, '', 'text/plain')
        if (query.has('pct')) Object.assign(entry, { where: 'queue', pct: Number(query.get('pct')) })
        if (query.has('storage')) Object.assign(entry, { where: 'history', pct: 100, storage: query.get('storage'), fail: '' })
        if (query.has('fail')) Object.assign(entry, { where: 'history', storage: '', fail: query.get('fail') })
        return send(response, 200, { job, ...entry })
      }
      if (path === '/__sab/foreign') {
        const job = `SABnzbd_nzo_${crypto.randomBytes(4).toString('hex')}`
        sabJobs[job] = { cat: 'nexbooks', name: query.get('name') ?? 'Unbekannter.Autor.Unbekanntes.Buch.2019.GERMAN.EPUB-EXAMPLE', where: 'queue', pct: 10, storage: '', fail: '' }
        return send(response, 200, { job })
      }
      // The file of a release found above: an NZB, for the right key only.
      if (/^\/indexer\/get\/\d+$/.test(path)) {
        if (query.get('apikey') !== KEYS.indexer) return send(response, 403, 'Forbidden', 'text/plain')
        return send(response, 200, NZB, 'application/x-nzb')
      }
      // Prowlarr's own API: the key is the header X-Api-Key.
      if (path.startsWith('/prowlarr/api/v1/')) {
        if (request.headers['x-api-key'] !== KEYS.prowlarr) return send(response, 401, '', 'text/plain')
        if (path.endsWith('/system/status')) return send(response, 200, { appName: 'Prowlarr', version: '2.6.5.5623', urlBase: '/prowlarr' })
        if (path.endsWith('/indexer')) return send(response, 200, PROWLARR_INDEXERS)
        return send(response, 404, '', 'text/plain')
      }
      // SABnzbd.
      if (path === '/sab/api') {
        const mode = query.get('mode')
        if (mode === 'version') return send(response, 200, { version: '4.5.1' })
        if (query.get('apikey') !== KEYS.sab) return send(response, 403, 'API Key Incorrect', 'text/plain')
        if (mode === 'get_cats') return send(response, 200, { categories: ['*', ...sabCategories] })
        if (mode === 'set_config') {
          sabCategories.push(String(query.get('keyword')).toLowerCase())
          return send(response, 200, { config: { categories: [] } })
        }
        if (mode === 'get_config') {
          const section = query.get('section')
          const data = section === 'misc' ? { complete_dir: '/downloads/complete' } : section === 'categories' ? sabCategories.map((name) => ({ name, dir: name })) : []
          return send(response, 200, { config: { [section]: data } })
        }
        if (mode === 'addfile') {
          const job = `SABnzbd_nzo_${crypto.randomBytes(4).toString('hex')}`
          const name = /filename="([^"]+)"/.exec(raw)?.[1] ?? job
          sabJobs[job] = { cat: String(query.get('cat')), name: name.replace(/\.nzb$/i, ''), where: 'queue', pct: 0, storage: '', fail: '' }
          sabLast = job
          return send(response, 200, { status: true, nzo_ids: [job] })
        }
        if ((mode === 'queue' || mode === 'history') && query.get('name') === 'delete') {
          delete sabJobs[query.get('value')]
          return send(response, 200, { status: true })
        }
        if (mode === 'queue' || mode === 'history') {
          const wanted = (query.get('nzo_ids') ?? '').split(',').filter(Boolean)
          const category = query.get('category')
          const slots = Object.entries(sabJobs)
            .filter(([id, job]) => job.where === mode && (wanted.length === 0 || wanted.includes(id)) && (!category || job.cat === category))
            .map(([id, job]) =>
              mode === 'queue'
                ? { nzo_id: id, cat: job.cat, filename: job.name, status: job.pct > 0 ? 'Downloading' : 'Queued', percentage: String(job.pct), mb: '412', mbleft: String(412 - (412 * job.pct) / 100), timeleft: '0:03:00' }
                : { nzo_id: id, category: job.cat, name: job.name, status: job.fail ? 'Failed' : 'Completed', storage: job.storage, bytes: '412000000', fail_message: job.fail },
            )
          return send(response, 200, { [mode]: mode === 'queue' ? { paused: false, slots } : { slots } })
        }
        return send(response, 200, { status: false, error: 'not implemented' })
      }
      // qBittorrent.
      if (path.startsWith('/qbt/api/v2/')) {
        const route = path.slice('/qbt/api/v2/'.length)
        if (route === 'auth/login') {
          const form = new URLSearchParams(raw)
          if (form.get('password') !== KEYS.qbt) return send(response, 200, 'Fails.', 'text/plain')
          return send(response, 204, '', 'text/plain', { 'Set-Cookie': `QBT_SID_8080=${SESSION}; HttpOnly; path=/` })
        }
        if (!String(request.headers.cookie ?? '').includes(`QBT_SID_8080=${SESSION}`)) return send(response, 403, 'Forbidden', 'text/plain')
        if (route === 'app/webapiVersion') return send(response, 200, '2.11.2', 'text/plain')
        if (route === 'app/preferences') return send(response, 200, { save_path: '/downloads' })
        if (route === 'torrents/categories') return send(response, 200, qbtCategories)
        if (route === 'torrents/createCategory') {
          const name = new URLSearchParams(raw).get('category')
          if (name in qbtCategories) return send(response, 409, '', 'text/plain')
          qbtCategories[name] = { name, savePath: '' }
          return send(response, 200, '', 'text/plain')
        }
      }
      return send(response, 404, '', 'text/plain')
    })
  })
  .listen(PORT, '127.0.0.1')
