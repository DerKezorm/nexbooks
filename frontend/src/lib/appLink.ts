/** Whether this browser runs on an iPhone or an iPad (the iPad asks for desktop pages and then calls itself a Mac). */
export function onApplePhone(): boolean {
  if (/iPhone|iPad|iPod/.test(navigator.userAgent)) return true
  return navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1
}

/** Whether an address leads only to the device that opens it (`localhost`, `127.x.x.x`, `::1`): a phone cannot reach it. */
export function reachesOnlyThisDevice(address: string): boolean {
  let host: string
  try {
    host = new URL(address).hostname.toLowerCase()
  } catch {
    return false
  }
  host = host.replace(/^\[|\]$/g, '')
  return host === 'localhost' || host.endsWith('.localhost') || host === '::1' || host === '0.0.0.0' || /^127(\.\d{1,3}){3}$/.test(host)
}
