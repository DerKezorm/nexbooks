/**
 * Nothing the interface loads comes from another host: no `@import` or `url(` in a style sheet, no `<link>`,
 * `<script src>` or picture in a page, no `fetch`, `import()` or `new Worker` in the code points at `http://`, `https://`
 * or `//host`. Fonts, icons and scripts all come from the site itself (the Content Security Policy refuses the rest, and
 * `backend/tests/test_no_foreign_sources.py` guards that side). Swept: the pages, the style sheets and the code in
 * `src` (the tests excluded, the shared sign-in module included), `public`, and the built `dist` when there is one.
 *
 * Comments are left out (a licence note may name a host); so are plain links a person can click (`<a href>`, the
 * `Out` component), which load nothing. An address inside a string that is only shown (a placeholder) is not a load.
 */
type Fs = {
  readFileSync(path: string, encoding: 'utf-8'): string
  readdirSync(path: string, options: { recursive: true }): string[]
}

/** Read from the disk and not through Vite's `?raw`: the test runner leaves style sheets empty (`css: false`), and an
 * empty sweep of them would pass for ever. (No node types in this project, hence the loose import.) */
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as unknown as Fs
// Relative to the folder the tests run in (`frontend`).
// The built scripts are libraries with addresses of their own for parsing; the sources above them are swept instead.
const KINDS: Record<string, RegExp> = { public: /\.(html|css|js|svg|webmanifest)$/, src: /\.(html|css|ts|tsx)$/, dist: /\.(html|css)$/ }

function sweep(folder: string, where: Record<string, string>): void {
  let names: string[]
  try {
    names = fs.readdirSync(folder, { recursive: true })
  } catch {
    return // no such folder (dist before the first build)
  }
  for (const name of names.map((item) => item.replaceAll(String.fromCharCode(92), '/'))) {
    if (!KINDS[folder].test(name) || /\.test\.(ts|tsx)$/.test(name)) continue
    try {
      where[`/${folder}/${name}`] = fs.readFileSync(`${folder}/${name}`, 'utf-8')
    } catch {
      // a folder named like a file
    }
  }
}

const files: Record<string, string> = { '/index.html': fs.readFileSync('index.html', 'utf-8') }
for (const folder of ['public', 'src', 'dist']) sweep(folder, files)

/** Hosts a file may name where it loads nothing from them (an XML namespace is no load). */
const NAMESPACES = ['http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink', 'http://www.w3.org/1999/xhtml']

const FOREIGN = String.raw`(?:https?:|wss?:)?\/\/`

/** The text without its comments: block comments, `<!-- -->` and `//` lines after a space or at the start of a line
 * (a `//` in an address follows a colon or a quote, and stays). */
export function withoutComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/(^|[\s;,{}()])\/\/[^\n]*/g, '$1')
}

const LOADS: [string, RegExp][] = [
  ['@import', new RegExp(String.raw`@import\s+(?:url\()?\s*['"]?\s*${FOREIGN}`, 'i')],
  ['url()', new RegExp(String.raw`url\(\s*['"]?\s*${FOREIGN}`, 'i')],
  ['element', new RegExp(String.raw`<(?:link|script|img|image|iframe|source|audio|video|track|embed|object|use)\b[^>]*?\b(?:src|href|xlink:href|srcset|data|poster)\s*=\s*["'{]?\s*['"]?${FOREIGN}`, 'i')],
  ['call', new RegExp(String.raw`\b(?:fetch|import|importScripts|EventSource|WebSocket|Worker|SharedWorker|sendBeacon)\(\s*['"\x60]\s*${FOREIGN}`)],
  ['src', new RegExp(String.raw`\.src\s*=\s*['"\x60]\s*${FOREIGN}`)],
  ['import from', new RegExp(String.raw`\bfrom\s+['"]\s*${FOREIGN}`)],
]

/** What in `text` loads from another host: the kind of load and the line. */
export function foreignLoads(text: string): string[] {
  let clean = withoutComments(text)
  for (const space of NAMESPACES) clean = clean.split(space).join('namespace')
  const found: string[] = []
  for (const [kind, pattern] of LOADS) {
    const hit = pattern.exec(clean)
    if (hit) found.push(`${kind}: ${hit[0].slice(0, 90)}`)
  }
  return found
}

describe('nothing is loaded from another host', () => {
  it('sweeps the pages, style sheets and code (and finds them)', () => {
    const names = Object.keys(files)
    // The sweep reads what it should: if a glob stopped matching, an empty sweep would pass for ever.
    expect(names).toContain('/index.html')
    expect(names).toContain('/src/index.css')
    expect(names).toContain('/src/main.tsx')
    expect(names).toContain('/public/sw.js')
    expect(files['/src/index.css']).toContain('--accent')
    expect(names.length).toBeGreaterThan(40)
  })

  it('finds no address of another host in anything that loads', () => {
    const problems = Object.entries(files).flatMap(([name, text]) => foreignLoads(text).map((what) => `${name} -> ${what}`))
    expect(problems).toEqual([])
  })

  it('does catch each way of loading from outside (and lets comments and links alone)', () => {
    for (const bad of [
      "@import url('https://fonts.googleapis.com/css2?family=Inter');",
      '@import "//fonts.example.com/x.css";',
      "@import 'http://cdn.example.com/x.css';",
      '.a { background: url(https://cdn.example.com/a.png) }',
      '@font-face { src: url( "//cdn.example.com/a.woff2" ) }',
      '<link rel="stylesheet" href="https://cdn.example.com/x.css">',
      '<link href=//cdn.example.com/x.css rel=stylesheet>',
      '<script src="https://cdn.example.com/x.js"></script>',
      '<img src="http://example.com/a.png">',
      '<img src={"https://example.com/a.png"} />',
      'const r = await fetch("https://api.example.com/x")',
      "const m = await import('https://cdn.example.com/m.js')",
      'new Worker("https://cdn.example.com/w.js")',
      'importScripts("//cdn.example.com/w.js")',
      "image.src = 'https://example.com/a.png'",
      "import x from 'https://cdn.example.com/x.js'",
    ]) {
      expect(foreignLoads(bad), bad).not.toEqual([])
    }
    for (const fine of [
      "@import 'tailwindcss';",
      '@import url(./local.css);',
      '.a { background: url(/assets/a.png); src: url("./files/inter.woff2") format("woff2") }',
      '/* @import url(https://fonts.example.com/x.css); */ .a { color: red }',
      '<!-- <script src="https://cdn.example.com/x.js"></script> -->',
      '// fetch("https://api.example.com/x")\nconst a = 1',
      '<svg xmlns="http://www.w3.org/2000/svg"><use xlink:href="#a"/></svg>',
      '<Out href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL</Out>',
      '<a href="https://github.com/example/nexbooks">source</a>',
      '<input placeholder="https://books.example.com" />',
      'const base = "https://github.com/example/nexbooks"',
      '<script type="module" src="/src/main.tsx"></script>',
      'fetch("/api/v1/health")',
    ]) {
      expect(foreignLoads(fine), fine).toEqual([])
    }
  })
})
