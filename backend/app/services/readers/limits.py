"""The limits every reader keeps, in one place. A file that goes past one is not read: ``ReadError`` with a code the
run records (and the interface words in the operator's language)."""

from __future__ import annotations


class ReadError(Exception):
    """A file that cannot be read, or not within the limits. ``code`` is one of ``REASONS`` (the interface has a
    sentence for each); nothing else about the file, and no trace, goes to the person who sees the list."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


#: Every code a problem of a file can have.
REASONS = (
    "unreadable",  # the format is broken or not what its name says
    "unsupported",  # a format nexbooks does not read
    "too_large",  # the file or a part of it is larger than a limit
    "too_many_entries",  # an EPUB with more files than a book has
    "zip_bomb",  # an EPUB that unpacks to far more than it weighs
    "encrypted",  # a file with DRM or a password
    "timeout",  # reading took too long
    "crashed",  # the reader stopped
    "link",  # a link: not followed
    "changed",  # the file changed while it was read
    "bad_name",  # a folder or file whose name is no valid text (or holds a control character): left out
    "failed",  # something went wrong in nexbooks while the file was handled (the log says what)
)

# EPUB (a ZIP)
EPUB_MAX_ENTRIES = 20_000
#: The central directory of the ZIP (one record of 46 bytes plus the name for each entry): a book's is a few hundred
#: kilobytes. ``zipfile`` reads as many records as the directory's size holds, whatever count the end record names, so
#: the size is what has to be bounded: this is room for the most entries a book may have with names of 50 bytes.
EPUB_MAX_DIRECTORY = 2 * 1024 * 1024
#: The XML parts of a book (container, package, navigation) are small; a megabyte is already odd.
EPUB_MAX_XML = 4 * 1024 * 1024
EPUB_MAX_NAV = 2 * 1024 * 1024
#: What a ZIP entry may unpack to, relative to what it weighs packed (text compresses 10 to 20 times; 300 is a bomb).
EPUB_MAX_RATIO = 300
#: Packed size below which the ratio is not looked at (a tiny entry of zeros has a huge ratio and harms nobody).
EPUB_RATIO_FREE = 64 * 1024
COVER_MAX_BYTES = 12 * 1024 * 1024
#: Pixels of a cover before it is opened (a PNG of 1 bit can be tiny on disk and huge in memory).
COVER_MAX_PIXELS = 30_000_000
EPUB_MAX_FILE = 4 * 1024 * 1024 * 1024
TOC_MAX_ENTRIES = 5_000
TOC_MAX_DEPTH = 6
TITLE_MAX = 300
DESCRIPTION_MAX = 4_000
#: What is read of the package file's text when it comes as HTML inside a field.
HTML_MAX = 200_000

# Estimating the pages of a book that has none (EPUB, MOBI): one printed page holds about 1,800 characters (the
# "Normseite" of the book trade); the markup of an e-book and the bytes of umlauts add around a fifth; so a page is
# about 2,200 bytes of unpacked markup. An estimate and nothing more: it gives the spine its width, and a book keeps no
# other number than this (an EPUB has no pages). Counted on the headers of the ZIP, not by unpacking anything.
BYTES_PER_PAGE = 2_200
PAGES_MAX = 20_000

# MOBI / AZW3
MOBI_MAX_RECORDS = 100_000
MOBI_MAX_HEADER = 512 * 1024
MOBI_MAX_IMAGE = 8 * 1024 * 1024
MOBI_MAX_FILE = 2 * 1024 * 1024 * 1024

# PDF
PDF_MAX_BYTES = 256 * 1024 * 1024
PDF_MAX_PAGES = 100_000
#: The cover of a PDF is a picture on its first page, taken only when it is this size at most.
PDF_COVER_MAX_BYTES = 8 * 1024 * 1024

# Audio
TAG_TEXT_MAX = 2_000
AUDIO_MAX_CHAPTERS = 5_000
AUDIO_MAX_DURATION = 60 * 60 * 24 * 14

# Companion files
SIDECAR_MAX_BYTES = 1024 * 1024
SIDECAR_COVER_MAX = 12 * 1024 * 1024
