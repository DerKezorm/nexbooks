/* The icons for the home screen, rendered once from the light version of the mark.
 *
 * Same shape as components/Logo.tsx and public/logo.svg; the source of every version is nexapps/icons-vergleich/neu.py.
 * Rendered with Playwright, which the end-to-end tests bring anyway:  node scripts/app-icons.mjs
 *
 * The maskable icon is cropped by Android, so the mark sits smaller on a full white square. The Apple touch icon is
 * full bleed: iOS rounds it itself. The badge is one colour: Android tints everything that is not transparent.
 */
import { chromium } from '@playwright/test'

const VERLAUF = `<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
  <stop offset="0" stop-color="#d77f8b"/><stop offset=".5" stop-color="#a3404e"/><stop offset="1" stop-color="#5e1f29"/>
</linearGradient></defs>`
const ZEICHEN = `<rect x="14" y="20" width="9.5" height="29" rx="1.8" fill="none" stroke="url(#g)" stroke-width="3.8"/>
  <rect x="26.5" y="13.5" width="11" height="35.5" rx="1.8" fill="#c0283f" stroke="#c0283f" stroke-width="3.8" stroke-linejoin="round"/>
  <path d="M29 20.5h6M29 41.5h6" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>
  <rect x="41" y="19" width="9" height="30" rx="1.8" fill="none" stroke="url(#g)" stroke-width="3.8" transform="rotate(13 45.5 49)"/>
  <path d="M10.5 52.5h43" stroke="url(#g)" stroke-width="4.2" stroke-linecap="round"/>`

const kachel = (s) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${s}" height="${s}">${VERLAUF}
  <rect x="2" y="2" width="60" height="60" rx="16" fill="#fff"/>
  <rect x="2.75" y="2.75" width="58.5" height="58.5" rx="15.3" fill="none" stroke="#8e3b46" stroke-opacity=".35" stroke-width="1.5"/>
  ${ZEICHEN}</svg>`
const voll = (s, m) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${s}" height="${s}">${VERLAUF}
  <rect width="64" height="64" fill="#fff"/>
  <g transform="translate(32 32) scale(${m}) translate(-32 -33)">${ZEICHEN}</g></svg>`
const randlos = (s) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="2 2 60 60" width="${s}" height="${s}">${VERLAUF}
  <rect x="2" y="2" width="60" height="60" fill="#fff"/>${ZEICHEN}</svg>`
const abzeichen = (s) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${s}" height="${s}">
  <g fill="#fff"><rect x="12" y="19" width="12" height="31" rx="2"/><rect x="26" y="12" width="13" height="38" rx="2"/>
  <rect x="41" y="18" width="11" height="32" rx="2" transform="rotate(13 46.5 50)"/></g>
  <path d="M8 54h48" stroke="#fff" stroke-width="5" stroke-linecap="round"/></svg>`

const BILDER = [
  ['public/icon-192.png', kachel(192), 192],
  ['public/icon-512.png', kachel(512), 512],
  ['public/icon-maskable-512.png', voll(512, 0.8), 512],
  ['public/apple-touch-icon.png', randlos(180), 180],
  ['public/badge-96.png', abzeichen(96), 96],
]

const browser = await chromium.launch()
for (const [pfad, svg, groesse] of BILDER) {
  const seite = await browser.newPage({ viewport: { width: groesse, height: groesse } })
  await seite.setContent(`<body style="margin:0;background:transparent">${svg}</body>`)
  await seite.locator('svg').screenshot({ path: pfad, omitBackground: true })
  await seite.close()
  console.log('written:', pfad)
}
await browser.close()
