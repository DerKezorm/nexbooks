/**
 * What a book says of itself (title, authors, series, description) can be written from right to left, whatever language the
 * page is in. Every place that shows such a text leaves the direction to the text (`dir="auto"`): the spine, the card of the
 * grid and its drawn cover, the tip, the search hits and the page of a book.
 */
import { act } from 'react'
import { Route, Routes } from 'react-router-dom'

import { changeLanguage } from '../i18n'
import { WorkPage } from '../pages/WorkPage'
import { ebook, library, work, workOut } from '../test/books'
import { Page, Server } from '../test/harness'
import { idle } from '../test/wait'
import { BookCard } from './BookCard'
import { SearchBox } from './SearchBox'
import { SpineTip } from './Shelf'
import { Spine } from './Spine'

const page = new Page()
const server = new Server()

const arabic = work(7, { title: 'مدينة الملح', authors: [{ id: 3, name: 'عبد الرحمن' }], series: { id: 4, name: 'خماسية', pos: 2 }, genre: 'roman' })

beforeEach(async () => {
  await changeLanguage('de', false)
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }))
})
afterEach(() => {
  page.close()
  vi.unstubAllGlobals()
})

const dirs = (selector: string) => [...document.querySelectorAll(selector)].map((element) => element.getAttribute('dir'))

describe('the direction of the text of a book is left to the text', () => {
  it('on the spine: the title and the author at the foot', async () => {
    await page.show(<Spine work={arabic} />)
    expect(dirs('.spine .t, .spine .a')).toEqual(['auto', 'auto'])
  })

  it('on the card of the grid and on the cover drawn for a book without a picture', async () => {
    await page.show(<BookCard work={arabic} />)
    expect(dirs('.book > b, .book > i, .book > small')).toEqual(['auto', 'auto', 'auto'])
    expect(dirs('.cover .c-author, .cover .c-title, .cover .c-series')).toEqual(['auto', 'auto', 'auto'])
  })

  it('in the tip over a spine', async () => {
    await page.show(<SpineTip tip={{ work: arabic, x: 1, y: 1 }} />)
    expect(dirs('.tip > b, .tip > span:not(.chips)')).toEqual(['auto', 'auto'])
  })

  it('in the hits of the search: the title and the people', async () => {
    server.answers = { 'GET /api/v1/search?q=%D9%85%D8%AF%D9%8A%D9%86%D8%A9&limit=8': { query: 'x', items: [arabic] } }
    server.fails = {}
    server.install()
    await page.show(<SearchBox />)
    await page.type(document.querySelector('#q') as HTMLInputElement, 'مدينة')
    await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 400))))
    await idle()
    expect(document.querySelector('.res')).toBeTruthy()
    expect(dirs('.res b, .res small span')).toEqual(['auto', 'auto'])
  })

  it('on the page of a book: the line above the title, the title, the author and the description', async () => {
    const book = workOut(7, [ebook(1)], { title: arabic.title, authors: arabic.authors, series: arabic.series, genre: 'roman', description: 'وصف الكتاب\n\nفقرة ثانية' })
    server.answers = {
      'GET /api/v1/works/7': book,
      'GET /api/v1/libraries': [library()],
      'GET /api/v1/series/4': { id: 4, name: 'خماسية', works: [] },
      'GET /api/v1/people/3': { id: 3, name: 'عبد الرحمن', wrote: [], narrated: [] },
    }
    server.fails = {}
    server.install()
    await page.show(
      <Routes>
        <Route path="work/:id" element={<WorkPage />} />
      </Routes>,
      '/work/7',
    )
    expect(dirs('.d-eyebrow, .d-title, .d-by a, .d-blurb p')).toEqual(['auto', 'auto', 'auto', 'auto', 'auto'])
  })
})
