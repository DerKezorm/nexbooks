# foliate-js (vendored)

Upstream: <https://github.com/johnfactotum/foliate-js>, commit `78914ae` (2026-05-01), MIT
(see `LICENSE`). Only the files nexbooks uses are here; `vendor/zip.js` and `vendor/fflate.js` are the builds foliate-js
carries (zip.js BSD-3-Clause, fflate MIT; see `public/licenses/reader.txt`).

What nexbooks changed (the whole list; `THIRD-PARTY.md` says why):

- `view.js`: the file type detection and loaders for ZIP, folders, comics, FB2 and PDF are cut out; the speech output is cut
  out; `relocate` carries the `reason` of the move.
- `paginator.js`, `fixed-layout.js`: the `sandbox` attribute of the frame of a page comes from `globalThis.nexbooksSandbox`
  (set in `src/readview/main.ts`).
- `mobi.js`: `clean` and `guard` hooks (MOBI6 and KF8 pages, MOBI6 resources) before an address is made.

Checked against a fresh clone of upstream at `78914ae` (13.10.2026, `git diff --no-index` file by file): `epub.js`, `epubcfi.js`,
`overlayer.js`, `progress.js`, `search.js`, `text-walker.js`, `vendor/zip.js`, `vendor/fflate.js` and `LICENSE` are identical; the
only files that differ are `view.js`, `paginator.js`, `fixed-layout.js` and `mobi.js`, and only by the changes listed above.
Everything else nexbooks does to a book (media types, cleaning, limits) is done around the library, in `src/readview/`, not in it.

`foliate.d.ts` types what nexbooks uses. To update: take the new commit, apply the changes above again, run the tests
(`npm test`, `npm run e2e`).
