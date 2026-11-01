"""The search of the catalogues as a library has it: one hit for a book, named the way it is called in the languages
the operator fetches in, the book that was asked for first.

A person goes to the library and asks for a book by its title, by the series and the volume, or in the language it was
written in; they get that book, not thirty records of it. So:

* **One hit for a work** (``build.py``): what the sources give is put together with the same rules as the catalogue of
  an author's works (``services/oeuvre/works.py``: the core of the title, the work of Open Library and Hardcover, the
  original title a translation names, two ISBNs from two sources, the series and its volume, the spellings of a name,
  ``&``/and/und). The e-book and the recording are forms of that hit. A box, a study guide, a book about the book, a
  play made of it and a part of it stay hits of their own, after the book and named for what they are.
* **The name in the languages fetched in** (``formats.config``: the operator's languages, not the person's): the hit
  is called what the book is called in the first of them, with its author, series and volume, the year it first came
  out and a cover of that language. A book that exists in none of them stands at the end, with the languages it does
  exist in.
* **The order of a library**: the book asked for first (its title or its series and volume fit the words, a known
  author), then the other volumes of its series, then the rest.
* **Fast** (``ask.py``): the sources are asked side by side; the answer is given with what is there after a moment,
  and the rest follows when the slower sources have answered (``pending`` and a ticket to ask again).

What goes out is the words a person typed, nothing else; every request goes through ``sources/http.py``.
"""

from .ask import Run, start, wait_for
from .build import Hit, build

__all__ = ["Hit", "Run", "build", "start", "wait_for"]
