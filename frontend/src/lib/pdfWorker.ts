/**
 * pdf.js's worker as a classic script. It comes inside this script, not from an address: a frame without an origin
 * may start no worker of the server, and Chrome starts no module worker in it at all (measured: a classic one from a
 * blob answers, a module one fails). pdf.js ships the worker as a module; its only module parts are the export at the
 * end and `import.meta.url` in two image decoders, both replaced here. Anything else of a module stops right here.
 */
export function classicWorker(source: string): string {
  const classic = source
    .replace(/export\s*\{\s*WorkerMessageHandler\s*\};?\s*(\/\/# sourceMappingURL=\S*)?\s*$/, '')
    .replaceAll('import.meta.url', 'self.location.href')
  if (/(^|[;}\s])export\s*\{|import\.meta|^\s*import\s/m.test(classic)) throw new Error('pdf.js worker is no longer a plain script')
  return classic
}
