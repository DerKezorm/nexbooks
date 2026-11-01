"""The sources nexbooks asks about a book it found in a library: Hardcover, Open Library, the catalogue of Audible and,
if the operator wants it, the German National Library.

**No source is required and none belongs to the project.** Hardcover needs a token of the operator's own; the others ask
nothing. Every source can be switched off, and so can all of them at once; with them off everything a library gives
(``services/scanner.py``) stays as it is. What is sent to a source is a title, an author and an ISBN, never a file.
Everything that comes back is kept for a while (``cache``), so that a book is asked about once.

* ``config``: the sources, their hosts and limits, whether one may be asked now.
* ``http``: the one way out, with the walls of ``services/outbound.py``.
* ``throttle``, ``cache``: how fast, and what is already known.
* ``hardcover``, ``openlibrary``, ``audible``, ``dnb``: the clients, each turning its answers into
  ``model.Candidate``.
"""
