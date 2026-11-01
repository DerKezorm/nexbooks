/**
 * The cover of a book: the picture the server gives, and, when it cannot (the source has none or is busy), the drawn
 * cover in the same box and colour, without a message.
 */
import { act, useState } from 'react'

import { changeLanguage } from '../i18n'
import { work } from '../test/books'
import { Page } from '../test/harness'
import { Cover } from './BookCard'

const page = new Page()

beforeEach(async () => {
  await changeLanguage('de', false)
})
afterEach(() => page.close())

async function failToLoad(): Promise<void> {
  await act(async () => {
    document.querySelector('img')!.dispatchEvent(new Event('error'))
  })
}

describe('the cover of a book of a source', () => {
  it('sets a picture of another shape than a book whole into the box, and leaves one of the shape of a book', async () => {
    const seen = (width: number, height: number) => {
      const picture = document.querySelector('[data-testid="cover-picture"] img:not(.c-back)') as HTMLImageElement
      Object.defineProperty(picture, 'naturalWidth', { value: width, configurable: true })
      Object.defineProperty(picture, 'naturalHeight', { value: height, configurable: true })
      return act(async () => {
        picture.dispatchEvent(new Event('load'))
      })
    }
    await page.show(<Cover work={work(1, { cover_url: '/a.jpg' })} />)
    await seen(400, 600)
    expect(document.querySelector('.cover')!.classList.contains('is-fit')).toBe(false)
    expect(document.querySelector('.c-back')).toBeNull()
    await seen(600, 600)
    expect(document.querySelector('.cover')!.classList.contains('is-fit')).toBe(true)
    expect(document.querySelector('.c-back')).not.toBeNull()
  })

  it('is a lazy picture of the address of the server while it loads', async () => {
    await page.show(<Cover work={work(1, { title: 'Weit Weg', color: '#215258', cover_url: '/api/v1/discover/books/t1/cover' })} />)
    const picture = document.querySelector('[data-testid="cover-picture"] img') as HTMLImageElement
    expect(picture.getAttribute('src')).toBe('/api/v1/discover/books/t1/cover')
    expect(picture.getAttribute('loading')).toBe('lazy')
  })

  it('falls back to the drawn cover when the server has none, in the same box and colour and without a message', async () => {
    await page.show(<Cover work={work(1, { title: 'Weit Weg', color: '#215258', cover_url: '/api/v1/discover/books/t1/cover' })} />)
    const classes = (document.querySelector('.cover') as HTMLElement).className
    await failToLoad()
    const drawn = document.querySelector('[data-testid="cover-drawn"]') as HTMLElement
    expect(drawn).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
    expect(drawn.className).toBe(classes)
    expect(drawn.style.getPropertyValue('--c')).toBe('#215258')
    expect(drawn.textContent).toContain('Weit Weg')
    expect(document.querySelector('[role="alert"]')).toBeNull()
  })

  it('tries the picture again when the book gets another address', async () => {
    function Swap() {
      const [address, setAddress] = useState('/api/v1/discover/books/t1/cover')
      return (
        <>
          <button type="button" onClick={() => setAddress('/api/v1/discover/books/t1/cover?v=abcdef012345')}>
            swap
          </button>
          <Cover work={work(1, { title: 'Weit Weg', color: '#215258', cover_url: address })} />
        </>
      )
    }
    await page.show(<Swap />)
    await failToLoad()
    expect(document.querySelector('img')).toBeNull()
    await page.click(page.button('swap'))
    expect(document.querySelector('img')!.getAttribute('src')).toBe('/api/v1/discover/books/t1/cover?v=abcdef012345')
  })
})
