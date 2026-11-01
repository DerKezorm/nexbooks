# Third-party licences

nexbooks itself is licensed under the **GNU Affero General Public License v3.0** (see [LICENSE](LICENSE)). This file
lists what it ships or depends on.

## Bundled with the app

These files travel inside the container image, so their notices travel with them.

| What | Copyright | Licence | Notice in the app |
|---|---|---|---|
| Fonts Fraunces, Inter and Oswald (via Fontsource) | The Fraunces, Inter and Oswald Project Authors | OFL-1.1 | `/licenses/fonts.txt` |
| Lucide icons (partly from Feather) | Lucide Contributors; Cole Bemis | ISC, MIT | `/licenses/lucide.txt` |
| foliate-js (turns the pages of an EPUB, MOBI or AZW3 in the browser), with the ZIP reader zip.js and the decompressor fflate it carries | John Factotum; Gildas Lormeau (zip.js); Arjun Barrett (fflate) | MIT; BSD-3-Clause (zip.js); MIT (fflate) | `/licenses/reader.txt` |
| pdf.js (the pages of a PDF; the npm package `pdfjs-dist`, its character maps, standard fonts and decoders copied at build time) | Mozilla Foundation and contributors | Apache-2.0 | `/pdfjs/LICENSE` |

nexbooks loads no font, icon or script from another host.

### The reader of e-books: foliate-js

nexbooks reads EPUB, MOBI and AZW3 in the browser with **foliate-js** (MIT, <https://github.com/johnfactotum/foliate-js>), taken
at commit `78914ae` (1 May 2026) into `frontend/src/vendor/foliate-js/` and kept apart from
the rest of the code (the linter leaves it alone). Only the parts nexbooks uses are there: the reader (`view.js`,
`paginator.js`, `fixed-layout.js`), the formats (`epub.js`, `epubcfi.js`, `mobi.js`), `progress.js`, `overlayer.js`,
`search.js`, `text-walker.js`, and the two libraries foliate-js bundles in its `vendor/` folder: **zip.js** (BSD-3-Clause;
the version foliate-js builds, 2.7.x) and **fflate** (MIT; 0.8.x). Nothing is loaded from a CDN. What nexbooks changed, and
why, is the whole list:

1. `view.js`: the detection of file types and the loaders for ZIP, folders, comics, FB2 and PDF are cut out (nexbooks makes
   the book itself, `src/readview/book.ts`, with limits on what a ZIP may claim and unpack, and hands it to `View.open`), the
   speech output is cut out, and the reason a place changed (a turned page, a scroll, a jump) is passed on in `relocate`.
2. `paginator.js`, `fixed-layout.js`: the frame of each page of a book takes its `sandbox` attribute from the reader
   (without `allow-scripts` where the browser lets it work that way; Safari needs the flag, and there every page carries a
   policy that forbids all scripts).
3. `mobi.js`: a page and a picture pass through two hooks (`clean`, `guard`) of the reader before they get an address, as
   those of an EPUB do through the events of the loader.

foliate-js was chosen over epub.js (BSD-2-Clause) because it is maintained (epub.js's last release is from 2023), reads
EPUB 2 and 3, fixed layouts, right-to-left and vertical text, and **MOBI and AZW3**, which epub.js does not, finds places
with CFIs, searches in the book and has no dependency of its own.

## Backend

What `backend/requirements.txt` names, and what those packages bring along (checked against the installed set, which is
what the container image installs; on Linux `uvicorn[standard]` adds uvloop).

| Package | Licence |
|---|---|
| FastAPI, Starlette (the web framework), SQLAlchemy, pydantic, pydantic-core, pydantic-settings, annotated-types, annotated-doc, typing-inspection | MIT, Starlette BSD-3-Clause |
| anyio, h11, httptools, watchfiles, PyYAML, PyJWT, http-ece | MIT |
| argon2-cffi, argon2-cffi-bindings, cffi | MIT (cffi: MIT-0 for its own part) |
| uvicorn, httpx, httpcore, click, idna, segno, python-dotenv, websockets, pycparser | BSD-3-Clause |
| uvloop (Linux only) | MIT **or** Apache-2.0 |
| Pillow | MIT-CMU |
| cryptography | Apache-2.0 **or** BSD-3-Clause |
| tzdata (the IANA time zone database for Python) | Apache-2.0; the data itself is in the public domain |
| typing-extensions | PSF-2.0 |
| SQLite (the library inside Python) | Public domain |
| certifi (the CA bundle) | MPL-2.0; the file is unchanged and its source is public |
| pillow-heif | BSD-3-Clause for its own code; the binary wheels are **GPLv2** as a whole, see below |
| webauthn (py_webauthn, passkeys) | BSD-3-Clause |
| cbor2 | MIT |
| pyOpenSSL | Apache-2.0 |
| pyasn1, pyasn1-modules | BSD-2-Clause, BSD-3-Clause |
| packaging | Apache-2.0 or BSD-2-Clause |
| pypdf (the pages and title of a PDF) | BSD-3-Clause |
| defusedxml (the XML inside an EPUB: no entities, no external files) | PSF-2.0 |
| mutagen (the tags and running time of audio files) | GPL-2.0-or-later; may be combined with an AGPL-3.0 work, and its source is public |

### HEIC photos: pillow-heif and the libraries it brings

To read iPhone photos (HEIC) as a profile picture, nexbooks uses pillow-heif. Its binary wheels, and so the container
image, contain:

| Library | Licence |
|---|---|
| libheif | LGPL-3.0 |
| libde265 | LGPL-3.0 |
| x265 | GPL-2.0-or-later |

The wheel ships these notices in `pillow_heif-*.dist-info/licenses/`. GPL-2.0-or-later and LGPL-3.0 may be combined
with an AGPL-3.0 work, and the source of every part is public.

### WebP and AVIF pictures: Pillow and the libraries it brings

To take WebP and AVIF pictures (what Android phones, newer cameras and browsers save), nexbooks uses Pillow, whose binary
wheels carry the decoders. Their notices are in `pillow-*.dist-info/licenses/LICENSE` of the wheel:

| Library | Licence |
|---|---|
| libwebp | BSD-3-Clause |
| libavif (with the AV1 decoder dav1d and, in the wheels that carry it, aom) | BSD-2-Clause |

## Frontend

| Package | Licence |
|---|---|
| React, React DOM, React Router, i18next, react-i18next, scheduler, cookie, set-cookie-parser, html-parse-stringify, void-elements, use-sync-external-store, @babel/runtime | MIT |
| lucide-react | ISC |
| pdfjs-dist (pdf.js) | Apache-2.0 |
| Fontsource packages | MIT (the fonts themselves OFL-1.1, see above) |

Build and test tools (Vite, TypeScript, Tailwind CSS, Vitest, ruff, pytest) are not part of the image. Their code is not
in the finished interface either, except what Vite and Tailwind CSS (both MIT) compile into the files.

## The base image

The container starts from `python:3.13-slim` (Debian GNU/Linux; the Python Software Foundation licence for Python, and the
licences of the Debian packages, listed in `/usr/share/doc/` of the image). On top of it: `curl` (the curl licence, an MIT
style licence; for the health check) and `gosu` (Apache-2.0; drops the rights at the start). Nothing is downloaded when
the container runs.

## Data sources and their terms

nexbooks can ask sources on the internet about books and authors (Settings, Sources). No source is required, none belongs
to the project, nexbooks ships none of their data, and each one can be switched off by the operator. Only a title, an
author and an ISBN go out. The terms of a source apply to whoever switches it on; the operator checks that they fit their
use. This table says what nexbooks takes from each source and how long it keeps it, and promises nothing about those terms.

| Source | What nexbooks takes | Terms and how it is named |
|---|---|---|
| Hardcover (`api.hardcover.app`) | Works, editions, series, trends, with the operator's own read-only token | The API terms of Hardcover apply, **also for commercial use**: an operator who uses nexbooks for a business reads them first. The API is a beta. Not connected until the operator pastes a token. |
| Open Library (`openlibrary.org`) | Works, languages, descriptions and covers of editions, and the books of a subject with the number of readers who want them (the shelves of Browse) | Open data of the Internet Archive's project; see <https://openlibrary.org/developers/licensing>. Named as the source in Settings, Sources and in About, wherever its data is described. |
| Apple Books Charts (`rss.marketingtools.apple.com`, `itunes.apple.com`) | The public chart of best-selling e-books of a country, and Apple's older feed of the chart of one genre (with the day each book came out and the description Apple gives), with the covers they name | The terms of Apple's feeds apply. Used by Discover and the shelves of Browse only; covers and texts are kept for a day at most. |
| German National Library (`services.dnb.de`) | Catalogue data of German titles: author, year, ISBN, publisher, series, subject group; for the catalogue of an author's works, every record of the author (translations included) | CC0. |
| French National Library (`catalogue.bnf.fr`) | For the catalogue of an author's works only: the French titles, years, extent, readers and literary form | Open data of the BnF (Licence Ouverte / Etalab); named in Settings, Sources and in About. Asked by the author's name only, kept for a day. |
| LIBRIS, National Library of Sweden (`libris.kb.se`) | For the catalogue of an author's works only: the Swedish titles and years | Open data (CC0) of the National Library of Sweden. Asked by the author's name only, kept for a day. |
| National Library of the Netherlands (`jsru.kb.nl`) | For the catalogue of an author's works only: the Dutch titles, years, extent and trade subject code | The terms of the KB's open interfaces apply. Asked by the author's name only, kept for a day. |
| National Library of Norway (`api.nb.no`) | For the catalogue of an author's works only: the Norwegian titles, years and pages | The terms of the National Library of Norway's open API apply. Asked by the author's name only, kept for a day. |
| Audible (`api.audible.<market>`) | Narrators, running time, abridged or not, series, ASIN, the lists of Discover | No documented public interface. **Off from the start**: the operator chooses once whether to use it. nexbooks only reads, slowly; what it says is kept for a day, covers and descriptions of books not on a shelf for a day at most. |
| Wikidata (`www.wikidata.org`) and Wikimedia Commons (`commons.wikimedia.org`, `upload.wikimedia.org`) | Who an author is, a short description, one photo | Wikidata is CC0. Each photo has its own licence: only CC BY, CC BY-SA, CC0 or the public domain are kept, shown with the photographer and the licence at the photo. A photo from any other source is not kept. |

## Compatibility

All of the above may be combined into an AGPL-3.0 work. The obligation runs one way: nexbooks as a whole is
AGPL-3.0, and anyone who runs a modified version as a network service must offer its source.
