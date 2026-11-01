/** Where a turn starts and how long its steps take: the numbers of the design, the same here. */
import { originOf, TURN } from './motion'

describe('the turn of a book', () => {
  it('has the durations and curves of the design (360 ms out, 420 ms in, 520 ms for the cover of the grid)', () => {
    expect(TURN.out).toEqual({ ms: 360, easing: 'cubic-bezier(.5,0,.75,0)' })
    expect(TURN.in).toEqual({ ms: 420, easing: 'cubic-bezier(.2,.7,.3,1)' })
    expect(TURN.grow).toEqual({ ms: 520, easing: 'cubic-bezier(.2,.8,.2,1)' })
  })

  it('starts from the spine itself, and from the cover (not the whole card) of a book in the grid', () => {
    document.body.innerHTML = '<button class="spine" id="s"><span class="cover"></span></button><button class="book" id="b"><div class="cover" id="c"></div><b>Title</b></button><button class="book" id="n">no cover</button>'
    expect(originOf(document.getElementById('s')!).id).toBe('s')
    expect(originOf(document.getElementById('b')!).id).toBe('c')
    expect(originOf(document.getElementById('n')!).id).toBe('n')
    document.body.innerHTML = ''
  })
})
