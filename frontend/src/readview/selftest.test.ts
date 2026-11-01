import { scriptRuns } from './selftest'

let made: Blob[] = []

beforeEach(() => {
  made = []
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => (made.push(blob as Blob), `blob:null/${made.length}`))
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

const probeFrame = () => document.querySelector('iframe') as HTMLIFrameElement

describe('the test of the walls before a book is opened', () => {
  it('makes a page like a page of a book, with a script of its own in it, in a frame with the sandbox it is given', async () => {
    const task = scriptRuns('allow-same-origin')
    expect(probeFrame().getAttribute('sandbox')).toBe('allow-same-origin')
    expect(probeFrame().src).toBe('blob:null/1')
    expect(made[0].type).toBe('text/html')
    expect(await made[0].text()).toContain('<script>')
    probeFrame().dispatchEvent(new Event('load'))
    expect(await task).toBe(false)
  })

  it('finds that the walls hold when the page loads and its script has said nothing', async () => {
    const task = scriptRuns('allow-same-origin')
    probeFrame().dispatchEvent(new Event('load'))
    expect(await task).toBe(false)
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('finds that the walls do not hold when the script of the page has run', async () => {
    const task = scriptRuns('allow-same-origin allow-scripts')
    window.dispatchEvent(new MessageEvent('message', { data: { nexbooksProbe: 1 }, source: probeFrame().contentWindow }))
    expect(await task).toBe(true)
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('does not take a message from anywhere else for the script of the page', async () => {
    vi.useFakeTimers()
    const task = scriptRuns('allow-same-origin')
    window.dispatchEvent(new MessageEvent('message', { data: { nexbooksProbe: 1 }, source: window }))
    window.dispatchEvent(new MessageEvent('message', { data: { other: 1 }, source: probeFrame().contentWindow }))
    window.dispatchEvent(new MessageEvent('message', { data: 'nexbooksProbe', source: probeFrame().contentWindow }))
    probeFrame().dispatchEvent(new Event('load'))
    await vi.advanceTimersByTimeAsync(200)
    expect(await task).toBe(false)
  })

  it('gives up on a page that never loads, and has found no script', async () => {
    vi.useFakeTimers()
    const task = scriptRuns('allow-same-origin')
    await vi.advanceTimersByTimeAsync(3000)
    expect(await task).toBe(false)
    expect(document.querySelector('iframe')).toBeNull()
  })
})
