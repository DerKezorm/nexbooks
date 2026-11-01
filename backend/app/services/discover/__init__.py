"""Discover: what the sources say sells, comes out and is read, set against what a person has on their shelves.

* ``markets``: the markets and the lists, and which source speaks for which list.
* ``foreign``: the books of the sources that are not on a shelf (public data, one row each), their covers and the page
of such a book.
* ``lists``: fetching the lists in the background, once in twelve hours at most, and keeping them.
* ``owned``: which of those books a person has, **by the books they may see and nothing else**.
* ``foryou``: the gaps in the series a person reads and more of their authors.
* ``wishlist``: the person's own list.
* ``view``: the answers, put together for one person.

**The rule that runs through all of it:** the lists, the gaps and the books are the same for everybody; what differs
from person to person (does it stand on my shelf? is it on my list? which series do I read?) is worked out from *the
person's own libraries and the person's own rows* at the moment of the question, never stored with the public data, so
that no answer can tell one person what another one owns.
"""
