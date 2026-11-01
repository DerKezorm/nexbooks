import workerSource from 'pdfjs-dist/build/pdf.worker.min.mjs?raw'

import { classicWorker } from './pdfWorker'

describe("pdf.js's worker", () => {
  it('runs as a classic script once the module parts are gone', () => {
    const classic = classicWorker(workerSource)
    expect(classic).not.toMatch(/import\.meta/)
    expect(classic).not.toMatch(/export\s*\{/)
    // Parsed, not run, as plain script code: a module's syntax (import, export) would throw here.
    expect(() => new Function(classic)).not.toThrow()
    expect(classic).toContain('WorkerMessageHandler')
  })

  it('refuses a worker that is still a module', () => {
    expect(() => classicWorker('import x from "y"\nexport { WorkerMessageHandler }')).toThrow()
    expect(() => classicWorker('const a = import.meta.url; export{Other};')).toThrow()
  })
})
