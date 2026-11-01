/**
 * The shelf: a spine is built (its variables, its classes, its parts), a section
 * for every genre in the order of the shelf with the number the server gave, the twins of a book (the missing one dashed,
 * further editions as chips, files to download, no button to read yet).
 */
import type { WorkBrief } from '../api/client'
import { changeLanguage } from '../i18n'
import { edition, ebook, recording, work, workOut } from '../test/books'
import { Page } from '../test/harness'
import { MiniShelf, ShelfSections } from './Shelf'
import { Spine } from './Spine'
import { Twins } from './Twins'

const page = new Page()

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => page.close())

describe('a spine', () => {
  it('carries the variables of the design and the class of its genre, and names the book for a screen reader', async () => {
    await page.show(<Spine work={work(5, { title: 'Der Sommergast', genre: 'krimi', color: '#1d1c1f', pages: 300, tall: 0.5, authors: [{ id: 1, name: 'Aurelio Castellan' }] })} />)
    const spine = document.querySelector('button.spine') as HTMLButtonElement
    expect(spine.className).toBe('spine s-krimi')
    expect(spine.getAttribute('aria-label')).toBe('Der Sommergast, Aurelio Castellan')
    expect(spine.style.getPropertyValue('--c')).toBe('#1d1c1f')
    expect(spine.style.getPropertyValue('--ink')).toBe('#efe6d2')
    expect(spine.style.getPropertyValue('--t')).toBe('25px')
    expect(spine.style.getPropertyValue('--h')).toBe('187px')
    expect(spine.style.getPropertyValue('--band')).toBe('#b5463c')
    expect(spine.querySelector('.t')!.textContent).toBe('Der Sommergast')
    expect(spine.querySelector('.a')!.textContent).toBe('Castellan')
    expect(spine.querySelector('.ic')).toBeNull()
  })

  it('shows headphones for a book that is also a recording, and the word HÖRBUCH on the case of a recording alone', async () => {
    await page.show(
      <>
        <Spine work={work(1, { editions: [ebook(1), recording(2)] })} />
        <Spine work={work(2, { audio_only: true, editions: [recording(3)] })} />
      </>,
    )
    const [both, alone] = [...document.querySelectorAll('button.spine')]
    expect(both!.querySelector('.ic svg')).toBeTruthy()
    expect(both!.querySelector('.top-deco')!.textContent).toBe('')
    expect(alone!.className).toContain('is-audio')
    expect(alone!.querySelector('.ic')).toBeNull()
    expect(alone!.querySelector('.top-deco')!.textContent).toBe('HÖRBUCH')
    expect((alone as HTMLElement).style.getPropertyValue('--t')).toBe('26px')
    expect((alone as HTMLElement).style.getPropertyValue('--h')).toBe('166px')
  })

  it('leans when its number says so, and says AUDIOBOOK in English', async () => {
    await page.show(<Spine work={work(8)} />)
    expect(document.querySelector('.spine')!.classList.contains('tilt')).toBe(false)
    page.close()
    await page.show(<Spine work={work(30)} />)
    expect(document.querySelector('.spine')!.classList.contains('tilt')).toBe(true)
    page.close()
    await changeLanguage('en', false)
    await page.show(<Spine work={work(1, { audio_only: true })} />)
    expect(document.querySelector('.top-deco')!.textContent).toBe('AUDIOBOOK')
  })

  it('gives a book with no genre the plain look, and hands the book and the element to the one who opens it', async () => {
    const opened: [WorkBrief, HTMLElement][] = []
    await page.show(<Spine work={work(2, { genre: '' })} onOpen={(w, element) => opened.push([w, element])} />)
    const spine = document.querySelector('button.spine') as HTMLButtonElement
    expect(spine.classList.contains('s-none')).toBe(true)
    await page.click(spine)
    expect(opened.map(([w]) => w.id)).toEqual([2])
    expect(opened[0]![1]).toBe(spine)
  })
})

describe('the sections of the shelf', () => {
  const works = [
    work(1, { genre: 'krimi', title: 'Eins' }), work(2, { genre: 'roman', title: 'Zwei' }), work(3, { genre: '', title: 'Drei' }), work(4, { genre: 'roman', title: 'Vier' }),
    work(5, { genre: 'lyrik', title: 'Fünf' }),
  ]

  it('makes a section for each genre in the order of the shelf, the books without a genre last, and none for an empty genre', async () => {
    await page.show(<ShelfSections works={[works[1]!, works[3]!, works[0]!, works[2]!, works[4]!]} counts={{ roman: 535, krimi: 7, lyrik: 1, none: 1 }} found={null} onOpen={() => undefined} />)
    const sections = [...document.querySelectorAll('section.section')]
    expect(sections.map((s) => s.getAttribute('data-genre'))).toEqual(['roman', 'krimi', 'lyrik', 'none'])
    // The heading says what the library has, not what has come so far; the German thousands separator is used.
    expect(sections.map((s) => s.querySelector('h2')!.textContent)).toEqual(['Romane 535', 'Krimi & Thriller 7', 'Lyrik 1', 'Ohne Genre 1'])
    expect(sections[0]!.querySelectorAll('.shelf .spine').length).toBe(2)
    // From the third section on, a section is not drawn until it comes near.
    expect(sections.map((s) => s.classList.contains('is-far'))).toEqual([false, false, true, true])
  })

  it('lets the book the search led to glow', async () => {
    await page.show(<ShelfSections works={works} counts={{}} found={2} onOpen={() => undefined} />)
    expect([...document.querySelectorAll('.spine.found')].map((e) => e.getAttribute('data-work'))).toEqual(['2'])
  })

  it('marks the current volume on the small shelf', async () => {
    await page.show(<MiniShelf works={works} current={4} onOpen={() => undefined} label="Reihe" />)
    expect([...document.querySelectorAll('.mini-shelf .spine.current')].map((e) => e.getAttribute('data-work'))).toEqual(['4'])
  })
})

describe('the twins of a book', () => {
  it('shows the e-book and the recording side by side with their words, the formats, the running time and who reads', async () => {
    const w = workOut(1, [ebook(1, { formats: ['epub', 'azw3', 'mobi'], pages: 320 }), recording(2, { duration: 9 * 3600 + 1800, narrators: ['Ben Hollister'] })])
    await page.show(<Twins work={w} />)
    const twins = [...document.querySelectorAll('.twin')]
    expect(twins.map((t) => t.getAttribute('data-kind'))).toEqual(['ebook', 'audio'])
    expect(twins[0]!.querySelector('h4')!.textContent).toBe('E-Book · Deutsch')
    expect(twins[0]!.querySelector('.tw-sub')!.textContent).toBe('EPUB · AZW3 · MOBI · 320 Seiten')
    expect(twins[1]!.querySelector('h4')!.textContent).toBe('Hörbuch · ungekürzt')
    expect(twins[1]!.querySelector('.tw-sub')!.textContent).toBe('9 Std 30 Min · gelesen von Ben Hollister')
    expect(document.querySelector('.tw-link span')!.textContent).toBe('⇄')
    expect(twins.every((t) => t.querySelector('.tw-pos')!.textContent === 'Noch nicht begonnen')).toBe(true)
  })

  it('offers to read the e-book and to listen to the recording, with its words', async () => {
    await page.show(<Twins work={workOut(1, [ebook(1), recording(2)])} />)
    const labels = [...document.querySelectorAll('.twin button')].map((b) => b.textContent)
    expect(labels).toEqual(['Lesen', 'Hören'])
    expect(document.querySelector('.twin[data-kind="ebook"] .go')).toBeTruthy()
    expect(document.querySelector('.twin[data-kind="audio"] .go')!.textContent).toBe('Hören')
  })

  it('says where the reader stands on the e-book card, with the bar of the design, and goes on reading', async () => {
    const place = { edition_id: 1, work_id: 1, locator: 'epubcfi(/6/2)', position: null, fraction: 0.37, unit: 'chapter' as const, chapter: 14, chapter_count: 38, chapter_title: 'Vierzehn', device_key: 'x', device: 'Pixel 8', moved_at: '2026-10-13T10:00:00Z', received_at: '2026-10-13T10:00:01Z', age: 60 }
    await page.show(<Twins work={workOut(1, [ebook(1), recording(2)])} places={[place]} />)
    const card = document.querySelector('.twin[data-kind="ebook"]') as HTMLElement
    expect(card.querySelector('.tw-pos')!.textContent).toBe('Kapitel 14 von 38 · 37 %')
    expect(card.querySelector('.tw-pos b')!.textContent).toBe('Kapitel 14')
    expect((card.querySelector('.bar2 i') as HTMLElement).style.width).toBe('37%')
    expect(card.querySelector('.go')!.textContent).toBe('Weiterlesen')
    // The recording knows nothing of it.
    expect(document.querySelector('.twin[data-kind="audio"] .tw-pos')!.textContent).toBe('Noch nicht begonnen')
    expect(document.querySelector('.twin[data-kind="audio"] .bar2')).toBeNull()
  })

  it('says pages for a PDF, and only the percentage for a book without chapters', async () => {
    const base = { edition_id: 1, work_id: 1, locator: 'page:12', position: null, fraction: 0.5, unit: 'page' as const, chapter: 12, chapter_count: 24, chapter_title: '', device_key: 'x', device: 'Pixel 8', moved_at: '2026-10-13T10:00:00Z', received_at: '2026-10-13T10:00:01Z', age: 60 }
    await page.show(<Twins work={workOut(1, [ebook(1)])} places={[base]} />)
    expect(document.querySelector('.twin[data-kind="ebook"] .tw-pos')!.textContent).toBe('Seite 12 von 24 · 50 %')
    page.close()
    await page.show(<Twins work={workOut(1, [ebook(1)])} places={[{ ...base, unit: 'chapter', chapter: 0, chapter_count: 0 }]} />)
    expect(document.querySelector('.twin[data-kind="ebook"] .tw-pos')!.textContent).toBe('50 %')
  })

  it('shows only the place of its own edition', async () => {
    const other = { edition_id: 99, work_id: 1, locator: 'x', position: null, fraction: 0.9, unit: 'chapter' as const, chapter: 3, chapter_count: 4, chapter_title: '', device_key: 'x', device: '', moved_at: '2026-10-13T10:00:00Z', received_at: '2026-10-13T10:00:01Z', age: 60 }
    await page.show(<Twins work={workOut(1, [ebook(1)])} places={[other]} />)
    expect(document.querySelector('.twin[data-kind="ebook"] .tw-pos')!.textContent).toBe('Noch nicht begonnen')
    expect(document.querySelector('.twin[data-kind="ebook"] .go')!.textContent).toBe('Lesen')
  })

  it('draws the missing edition dashed, says so, and offers "Releases ansehen" only to who may fetch that kind', async () => {
    await page.show(<Twins work={workOut(1, [ebook(1)])} />)
    const missing = document.querySelector('.twin.missing') as HTMLElement
    expect(missing.getAttribute('data-kind')).toBe('audio')
    expect(missing.querySelector('h4')!.textContent).toBe('Kein Hörbuch')
    expect(missing.querySelector('.tw-sub')!.textContent).toBe('Diese Fassung fehlt noch.')
    expect(missing.querySelector('button')).toBeNull()
    page.close()
    const asked: string[] = []
    const status = { allowed: true, kinds: [{ kind: 'audio' as const, library: 'Hörbücher', have: null, goal: 'flac', upgrade: false }] }
    await page.show(<Twins work={workOut(1, [ebook(1)])} procuring={{ status, onReleases: (kind) => asked.push(kind) , onAcquire: () => undefined, workId: 1 }} />)
    // Who may fetch it is offered "Beschaffen" first and "Releases ansehen" after it.
    const acquire = document.querySelector('.twin.missing [data-testid="acquire"]') as HTMLButtonElement
    expect(acquire.textContent).toBe('Hörbuch beschaffen')
    expect([...document.querySelectorAll('.twin.missing button')].map((item) => item.textContent)).toEqual(['Hörbuch beschaffen', 'Releases ansehen'])
    const button = document.querySelector('.twin.missing [data-testid="releases"]') as HTMLButtonElement
    expect(button.textContent).toBe('Releases ansehen')
    await page.click(button)
    expect(asked).toEqual(['audio'])
    page.close()
    // Who may fetch e-books only is offered nothing for a missing recording.
    await page.show(<Twins work={workOut(1, [ebook(1)])} procuring={{ status: { allowed: true, kinds: [] }, onReleases: () => undefined , onAcquire: () => undefined, workId: 1 }} />)
    expect(document.querySelector('.twin.missing button')).toBeNull()
    page.close()
    await page.show(<Twins work={workOut(1, [recording(2)])} />)
    expect(document.querySelector('.twin.missing h4')!.textContent).toBe('Kein E-Book')
  })

  it('offers a better format where the edition there is lies below the goal', async () => {
    const asked: string[] = []
    const status = { allowed: true, kinds: [{ kind: 'ebook' as const, library: 'Bücher', have: 'mobi', goal: 'epub', upgrade: true }] }
    await page.show(<Twins work={workOut(1, [ebook(1)])} procuring={{ status, onReleases: (kind) => asked.push(kind) , onAcquire: () => undefined, workId: 1 }} />)
    const button = document.querySelector('[data-testid="releases-upgrade"]') as HTMLButtonElement
    expect(button.textContent).toBe('Besseres Format suchen')
    await page.click(button)
    expect(asked).toEqual(['ebook'])
    page.close()
    await page.show(<Twins work={workOut(1, [ebook(1)])} procuring={{ status: { allowed: true, kinds: [{ ...status.kinds[0], upgrade: false }] }, onReleases: () => undefined , onAcquire: () => undefined, workId: 1 }} />)
    expect(document.querySelector('[data-testid="releases-upgrade"]')).toBeNull()
  })

  it('takes the unabridged recording for the card, and shows the others as chips below', async () => {
    const w = workOut(1, [ebook(1), recording(2, { variant: 'abridged', duration: 3600 }), recording(3, { variant: 'unabridged' }), recording(4, { variant: 'dramatized', duration: 7200, narrators: ['mehrere Sprecher'] })])
    await page.show(<Twins work={w} />)
    expect(document.querySelectorAll('.twin').length).toBe(2)
    expect(document.querySelector('.twin[data-kind="audio"] h4')!.textContent).toBe('Hörbuch · ungekürzt')
    const more = [...document.querySelectorAll('.more-fass span')].map((s) => s.textContent)
    expect(more).toEqual(['Hörbuch · gekürzt · 1 Std 0 Min', 'Hörbuch · Hörspiel · 2 Std 0 Min'])
  })

  it('offers each file for download by its address, one button for each format', async () => {
    const w = workOut(1, [ebook(1, { formats: ['epub', 'pdf'] })])
    await page.show(<Twins work={w} />)
    const links = [...document.querySelectorAll('.twin a.tw-file')] as HTMLAnchorElement[]
    expect(links.map((a) => a.textContent)).toEqual(['EPUB', 'PDF'])
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['/api/v1/files/10/download', '/api/v1/files/11/download'])
    expect(links.every((a) => a.hasAttribute('download'))).toBe(true)
  })

  it('puts a recording of many parts into a list that opens, not a row of buttons', async () => {
    const parts = recording(2, { formats: ['mp3'] })
    const w = workOut(1, [ebook(1), parts])
    w.editions[1] = edition(parts, { files: Array.from({ length: 12 }, (_, index) => ({ id: 100 + index, format: 'mp3', path: `teil${index + 1}.mp3`, size: 5_000_000, part: index + 1, duration: 60, sha256: 'b'.repeat(64), protected: false })) })
    await page.show(<Twins work={w} />)
    const details = document.querySelector('.twin[data-kind="audio"] details') as HTMLElement
    expect(details.querySelector('summary')!.textContent).toBe('12 Dateien')
    expect(details.querySelectorAll('a').length).toBe(12)
    expect(details.querySelector('a')!.textContent).toBe('Teil 1 · MP3 · 5 MB')
  })

  it('says it in English, and never pauses with a dash', async () => {
    await changeLanguage('en', false)
    await page.show(<Twins work={workOut(1, [ebook(1, { language: 'en' }), recording(2)])} />)
    const text = document.body.textContent ?? ''
    expect(text).toContain('E-book · English')
    expect(text).toContain('Audiobook · unabridged')
    expect(text).toContain('10 h 0 min · read by Ben Hollister')
    expect(text).toContain('Not started yet')
    expect(text).not.toMatch(/[–—]/)
  })
})
