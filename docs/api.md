# The nexbooks API

Everything the web app does goes through `/api/v1`; the web app has no routes of its own. What is under `/api/v1`
stays as it is: new fields may come, nothing is renamed or taken away. A change that would break a program goes to
`/api/v2` beside it.

- **The description**: `/api/v1/docs` lists every route on one page (no script, nothing loaded from elsewhere). The
  machine-readable description is `/api/v1/openapi.json` (OpenAPI 3).
- **The snapshot**: the description is kept in `backend/tests/schema/openapi.json`, and a test fails when the interface
  changes without the snapshot being renewed on purpose.

This is the interface: the frame (accounts, sign-in, settings), the libraries and the books in them, the search and
the download of a file, and the matching of the books with the sources.

## Ways to sign in

| Who | How | Used for |
|---|---|---|
| A browser | The session cookie, plus the header `X-Nexbooks-Client` on every changing request | The web app |
| A device (phone, later app) | `Authorization: Bearer nbd_…` | Everything the account may do, except changing the way into the account |
| A program | `Authorization: Bearer nbk_…` (an API key) | Reading (scope `read`) |
| A reading app | `Authorization: Basic` with the name of the account and the secret of a connection (`nbo_…`) | The catalogue under `/opds`, and nothing else |
| KOReader | The headers `x-auth-user` (the name of the account) and `x-auth-key` (the MD5 of the secret `nbs_…` of a connection) | The progress sync under `/kosync`, and nothing else |
| A Kobo | The secret `nbe_…` of a connection, as the second part of the address, `/kobo/<secret>/…` (a Kobo can send no header of its own) | The sync of a Kobo under `/kobo`, and nothing else |

A request that carries `Authorization: Bearer` is judged by that token alone; the cookie is not looked at, and a wrong
token never falls back to it. A changing request with a token needs no `X-Nexbooks-Client`: a page on another site can
set neither header.

### Sign-in providers (a browser)

Sign-in through an OpenID Connect provider is a browser's way, under `/api/v1/oidc`, the same in every nex app:
`GET /providers` (the buttons of the sign-in page, open), `GET /<short name>/start[?invite=…][&next=<page>]` and
`GET /<short name>/callback` (`/callback` for the entry `oidc`). After the provider the browser holds the session
cookie, or lands on `/login?step=code` when the code of nexbooks's own second factor is due. Linking, unlinking and the
operator's list (`/me`, `/<short name>/link`, `/me/address`, `/admin/…`, `/authentik/…`) answer `403 browser_only` to a
token.

### Devices

Made in the web app (**My account, Devices and apps**, or `POST /api/v1/devices` from a signed-in browser):

- The token is shown once; nexbooks keeps only a checksum. It acts with the rights of its account.
- A device brings another in one case only: the iPhone app confirms the code of a television with its own token (below,
  "The iPhone confirms the television"). That television is connected **through** the phone: the list says so
  (`via: {"id", "name"}` in `GET /devices`, empty for a device made in the browser), revoking the phone revokes the
  television with it (and a confirmation the television has not fetched yet), and the television connects no device
  itself (`403 device_chain`).
- Only a signed-in browser may make, list or revoke devices, make API keys, change the password, the second factor or
  the passkeys, change or take away the mail address, or link or unlink a sign-in provider: these routes answer `403
  browser_only` to a token. The same goes for **every route of the operator** (settings, accounts, invitations, links to
  set a new password, backups, the log, the mail and push setup): the operator's side is the browser's, even for the
  operator's own token, so a lost phone is no way into the server.
- A device stops working at once when it is revoked, when the device that connected it is revoked, when its account is
  blocked or deleted, when "sign out everywhere" is used, and while the operator requires a second factor the
  account has not set up yet.

### Connecting the iPhone app

In the web app (**My account, Devices and apps, Connect the iPhone app**, for every person, not only the operator) a
signed-in browser makes a code, and the app takes it and gets a device token of its own, without anybody typing a long
token. The code lives ten minutes; an account holds at most five open ones; the token comes into being when the app
fetches it, so a code nobody used leaves no device behind. The fetched device stands in the list of devices and is
revoked like any other. "Sign out everywhere" and a new password end the codes made before. On the phone itself the window
has the buttons "Open in the app" (it opens the link of the code, which carries the secret, from the page's memory) and
"Copy code"; where the address is `localhost` or `127.0.0.1` it says the phone cannot reach it.

| Route | Who | What |
|---|---|---|
| `POST /devices/pairing` | a browser | Body `{"address"}`, the address the browser is at (the public address of the settings goes first). Answers `201` with `id`, `secret` (once), `code` (`ABCD-2345`, to type), `expires_at`, `address`, `link` and `qr_svg`. The link is `nexbooks://connect?address=…&pairing=…&secret=…`. `422 pairing_address` when there is no plain `http(s)://host[:port]` address, or the browser sent another `Origin`; `429 pairing_limit` at five open codes. |
| `GET /devices/pairing/{id}` | a browser | `{"state", "device"}`: `state` is `waiting`, `connected` (then `device` is `{"id", "name"}`) or `expired`. Another person's code is `404`. |
| `DELETE /devices/pairing/{id}` | a browser | Withdraws a code nobody used (`204`). One that was used stays, its device is revoked in the list. |

The app's side is under `/api/app/1` (below). A device token cannot make, ask for or withdraw a code (`403 browser_only`).

### Connecting a device that has no camera (an Apple TV)

The other way round: the device shows a code, a signed-in person confirms it in the browser (**My account, Devices and
apps, Connect a device with a code**, for every person). The person types the code, sees how the device is named and is
asked "Connect it to your account?". **Only that answer** lets the device fetch a token, and for the account that answered;
the code alone gives nothing, because the token goes to whoever shows the device's secret. The token is made at the moment
the device fetches it and is never kept, so a request nobody confirmed leaves no device behind. The window says
before the code is typed to enter only a code seen on one's own television; it shows the name as plain text in quotation
marks on one line under "The device calls itself" (it was typed by whoever made the request and proves nothing) and warns again
at the question that a code somebody else reads out may connect a stranger's device to the account. A device name holds at
most three stacked marks and at least one character that shows (`422 device_name_invalid` otherwise, on every way a device
gets a name). Whoever has to set up a second factor cannot confirm (`403 second_factor_setup_required`).

| Route | Who | What |
|---|---|---|
| `POST /devices/pair-requests/lookup` | a browser | Body `{"code"}` (any case, with or without the dash or spaces). Answers `{"id", "device_name", "requested_at", "expires_at"}` and changes nothing. `404 pairing_unknown` (counts against the typist), `410 pairing_expired` / `pairing_used`, `422 pairing_code` (not a code, no attempt counted), `429 too_many_attempts`, `429 too_many_codes`. |
| `POST /devices/pair-requests/{id}/confirm` | a browser | Body `{"code"}`: the id from the lookup **and** the code are needed. Answers `{"state": "confirmed", "device": null}`. The same person twice is one confirmation; another person is `410 pairing_used`. `409 too_many_devices` while the account holds 30 devices (the request stays). |
| `GET /devices/pair-requests/{id}` | a browser | `{"state", "device"}`: `confirmed` (the device has not fetched yet), `connected` (then `device` is `{"id", "name"}`) or `expired`. A request somebody else confirmed, or nobody did, is `404`. |
| `DELETE /devices/pair-requests/{id}` | a browser | Takes the confirmation back as long as the device has not fetched its token (`204`): the request runs out at once. One the device fetched stays; its device is revoked in the list. |

A wrong code typed here counts like a wrong code of the iPhone app (the same count of the sender, not the television's, and one of the person, so
changing addresses does not help), and over all typists together only 30 wrong codes pass in ten minutes (a watch of its own,
so that typing here never rests the typing of the codes of the iPhone app, nor the other way round). "Sign out everywhere"
and a new password end the confirmations that were not fetched yet.

### API keys

Off until the operator switches them on (**Settings, API**). A key reads as its account, never more (`scope: read`). It
runs out after 30, 90 or 365 days, or never, and can be blocked for good by the operator. A request that carries an
`Origin` header is refused: a key is for programs, not web pages.

### E-readers

A person makes connections for their reading apps under **My account, Devices and apps, E-readers**, and sets the address of
their Kindle there. **On from the start**; the operator switches it off for everybody in **Settings, API** (`PUT
/settings` with `ereaders_allowed`, `false` answers `403 ereaders_off` on everything below and ends every connection).
**Only a signed-in browser** makes, lists and deletes connections and sets the address: these routes answer `403
browser_only` to a device token, an API key and a connection. "Sign out everywhere", a new password, and the operator signing the
account out or blocking it delete every connection of the account that is not blocked (the list of books for the Kobo stays).

| Route | What it does |
|---|---|
| `GET /ereaders` | `{"allowed", "links": [...], "kindle": {"address", "mail_ready", "sender", "max_megabytes"}, "kobo": [edition ids]}`: the own connections (`id`, `kind`, `name`, `prefix`, `created_at`, `last_used_at`, `blocked`), the Kindle and the editions on the own list for the Kobo that the caller still sees. `mail_ready`: the operator set up a mail server; `sender`: the address the mails come from, which the person has to approve at Amazon. `kobo_base`: the address a Kobo is handed for everything else it asks (the public address, else the one this request came to); a page seen over `https` that gets `http` here sits behind a proxy nexbooks was not told about (`NEXBOOKS_PUBLIC_URL`, `NEXBOOKS_TRUSTED_PROXIES`). |
| `POST /ereaders/links` | Body `{"name", "kind": "opds", "kosync" or "kobo"}`. Answers `201` with `{"link", "username", "secret"}`. The secret (`nbo_…` for the catalogue, `nbs_…` for the sync of KOReader, `nbe_…` for a Kobo, whose address it is part of) is shown **once**; nexbooks keeps only its SHA-256 (for `kosync`: the SHA-256 of the MD5 KOReader sends, see below) and the first characters. At most 20 connections an account (`409 too_many_links`). The person is told of a new connection like a new sign-in (push and mail, never with the name). |
| `DELETE /ereaders/links/{id}` | Deletes an own connection (`204`); another person's is `404`. One the operator blocked is `409 link_blocked`: only the operator lifts a block. |
| `PUT /ereaders/kindle` | Body `{"address"}`: an address at `kindle.com` (case does not count), else `422 invalid_kindle_address`. Empty removes it. |
| `POST /ereaders/kindle/test` | Mails a small test book to the address now. `409 kindle_address_missing` / `mail_off`, `502 mail_failed`, `429 kindle_too_often` (3 a minute, 10 an hour). |
| `POST /files/{id}/kindle` | Mails the EPUB `{id}` to the Kindle address of the caller, **in the background** (`202`, `{"queued": true}`). Open to a browser and a device token, not to a key. The rights are those of `GET /files/{id}/download` (a file of a library the caller is not in is `404`); an EPUB only (`422 not_epub`), without copy protection (`422 protected_book`), at most 50 MB (`413 file_too_large`, `max_mb`). Needs the address (`409 kindle_address_missing`) and a mail server (`409 mail_off`). The same book twice while it waits is one mail. At most 6 a minute and 30 an hour per person (`429 kindle_too_often`). The job reads the rights and the address again when it runs. |
| `PUT /editions/{id}/kobo` | Puts an e-book on the own list for the Kobo (`{"listed": true}`). Browser only. The rights are those of the download (an edition of a library the caller is not in is `404`, like a number nobody has); an e-book with an EPUB only (`404` for a recording, `422 not_epub`), without copy protection (`422 protected_book`). `403 ereaders_off`, `409 kobo_list_full` (at most 1000 books; kept by the statement that writes the entry), `429 kobo_too_often` (120 a minute). Twice is one entry; a book the person took off a Kobo by hand is sent again. |
| `DELETE /editions/{id}/kobo` | Takes it off the list (`{"listed": false}`; the same for an edition that was not on it). The Kobos are told at their next sync. Works while e-readers are switched off. |
| `GET /admin/ereaders/links` | The operator: every connection with the name of its account, never a secret. |
| `POST /admin/ereaders/links/{id}/block` | The operator blocks a connection for good; its account sees it marked, cannot delete it, and cannot make another of the same kind (`409 kind_blocked`) while it is there. "Sign out everywhere" and a new password leave it too. |
| `DELETE /admin/ereaders/links/{id}` | The operator deletes a connection of any account (`204`); the way to lift a block. |

## The catalogue for reading apps

A catalogue of the e-books in OPDS 1.2 (Atom) for apps such as KOReader, Librera and Moon+ Reader, under `/opds`. It is **not
part of `/api/v1`** and not in the machine-readable description; it is only for programs, **only `GET`**, and reads no
cookie.

**Signing in.** `Authorization: Basic`, the name of the account and the secret of a connection of the kind `opds` (made as
above). The password of the account is no way in. Without a header: `401` with `WWW-Authenticate: Basic realm="nexbooks"`.
A wrong, blocked or deleted secret, a name that is not the account's, an account that is blocked or has not set up
the second factor the operator requires: `401`. Wrong secrets count against the sender like wrong passwords, in a count of
their own (five, then a quarter of an hour of `429` with `Retry-After`; the same wrong secret again counts once, a
connection that exists is never turned away by it). Switched off by the operator: `403 ereaders_off`. One account may ask
600 times a minute (`429 opds_too_often`); files count as for the download.

**What is in it.** Only what the account sees: the same rights as the interface, a book of a library the account is not in
is not listed and its address is `404`. **E-books only**: a work with no e-book is left out, and recordings are not offered.
Only files that are on the disk. Nothing of the disk is written into a feed: books, authors, series and libraries are named
by their number, a file is `/opds/files/{id}/{id}.{format}` (the ending is only a hint; the number names the file).

| Address | What it is |
|---|---|
| `/opds` | Navigation: New, Authors, Series, and one entry for each library with e-books the account is in. Carries the search links. |
| `/opds/new?page=` | Acquisition feed: the newest e-books. |
| `/opds/libraries/{id}?page=` | The e-books of one library, by title (`404` for a library the account is not in). |
| `/opds/authors?page=` | Navigation: authors with at least one e-book, by last name, with the number of their e-books. |
| `/opds/authors/{id}?page=` | The e-books of an author. |
| `/opds/series?page=` | Navigation: the series, by name. |
| `/opds/series/{id}?page=` | The volumes of a series, in order. |
| `/opds/search?q=` | The e-books the words find (the words are the start of a word of the title, the people, the series, the description or an ISBN; at most 30, one page). |
| `/opds/search.xml` | The OpenSearch description (`/opds/search?q={searchTerms}`). |
| `/opds/works/{id}/cover` | The cover as a WebP picture (also `/thumbnail`), with `ETag`. |
| `/opds/files/{id}/{name}` | The file, `Range` and `If-None-Match` as in the interface; the checks of `GET /files/{id}/download`. |

A page holds 50 entries and links `first`, `previous`, `next` and `last`; `page` is 1 to 100000. A book entry carries title (with
series and volume), authors, description, language, year, genre, the cover as `image` and `image/thumbnail`, one
`http://opds-spec.org/acquisition` link for each format (EPUB first, then AZW3, MOBI, PDF) with its size, and a `related`
link to its series.

## The progress sync of KOReader

The sync server KOReader's progress plugin talks to, under `/kosync`: KOReader reports where a person is in a book and asks
where else they were. It is **not part of `/api/v1`**, not in the machine-readable description, and reads no cookie. The
address to enter in KOReader (Progress sync, Custom sync server) is `https://<your nexbooks>/kosync`, **without a slash at the
end**.

**Signing in.** The plugin sends two headers on every request: `x-auth-user`, the name of the account (case does not count),
and `x-auth-key`, the **MD5 of the password** as 32 lower-case hexadecimal digits. The password is the secret (`nbs_…`) of a
connection of the kind `kosync`, made under My account as above; the password of the account is no way in. nexbooks keeps the
SHA-256 of that MD5, never the secret and never a key that works. A wrong, blocked or deleted key, a name that is not the
account's, an account that is blocked or has not set up the second factor the operator requires, and an operator who
switched e-readers off are all `401 {"code": 2001, "message": …}` (the plugin shows `message`; switched off says so). Wrong
keys count against the sender like wrong passwords, in a count of their own (five, then a quarter of an hour of `429`; the
same wrong key again counts once, and a connection that exists is never turned away by it). One account may ask 120 times a
minute (`429 kosync_too_often`). A body is at most 16 KiB (`413`).

| Route | What it does |
|---|---|
| `GET /kosync/users/auth` | `200 {"authorized": "OK"}` for a good key. |
| `POST /kosync/users/create` | Always `402 {"code": 2005, "message": …}`: accounts are made in nexbooks, the message says where. Nothing is read or made. |
| `PUT /kosync/syncs/progress` | Body `{"document", "progress", "percentage", "device", "device_id"}` (and `metadata`, taken and not kept). Answers `200 {"document", "timestamp"}`. `document` is the partial MD5 of the file (below), `progress` KOReader's own word for the place (an XPointer for a book it reflows), `percentage` 0 to 1, `device` a name, `device_id` the id the plugin made up. A document nobody knows, or a book of a library the account is not in, is answered the same and **nothing is kept**. A malformed body is `403` with a `code` (2003, 2004, 2007) as the official server answers. |
| `GET /kosync/syncs/progress/{document}` | `200 {"document", "percentage", "progress", "device", "device_id", "timestamp"}`, or `200 {}` when the book is unknown to the account or no place can be told. |

**The document.** KOReader names a book by the *partial MD5* of its file (the default, "Binary"): the MD5 of the bytes read at
the offsets 0, 1 KiB, 4 KiB, 16 KiB, 64 KiB, 256 KiB, 1 MiB, 4 MiB, 16 MiB, 64 MiB, 256 MiB and 1 GiB, up to 1024 bytes at
each, stopping at the first offset past the end of the file. nexbooks makes it for every EPUB when the file comes or changes,
and for the EPUBs that were there before in the background, a few at a time. The other way KOReader can name a book, by the
MD5 of the file's name, is not understood. The book has to be **the same file** on the device and in the library (fetching it
through the catalogue is the sure way). Other formats are not matched.

**What a report becomes.** The place is kept as the person's place in the edition, by the rule of `PUT /editions/{id}/progress`
(the newest report stays; a report counts from the moment it arrived): `locator` empty, `fraction` the percentage, `device`
and `device_key` (`kr-` and the `device_id`) from the report. The reader opens at `fraction` when `locator` is empty. KOReader's
own word for the place is kept beside it as long as nothing else moves the place.

**What KOReader is told.** KOReader goes to `progress`, not to `percentage`. A place that came from KOReader is told back as it
was reported. A place from the browser or the app has no such word: the answer is `/body/DocFragment[N]`, the start of the
`N`-th item of the book's spine in which the share of the book falls (items weighed by their unpacked size, as the reader does).
That is the start of a chapter or file, not the word; a book whose parts cannot be read gets `{}`.

## The sync of a Kobo

The sync a Kobo e-reader makes with its store, answered by nexbooks under `/kobo/<secret>`: a person puts e-books on a list,
the Kobo fetches the list at its sync and keeps it, and the place read on the Kobo meets the place of the browser. It is
**not part of `/api/v1`**, not in the machine-readable description, and reads no cookie. Only the e-books the person put on
their list (below) are reachable, only as the EPUB that lies in the library (nothing is converted), and only while the person
still sees them.

**Setting it up.** The person makes a connection of the kind `kobo` under My account as above and gets, once, an address
`https://<your nexbooks>/kobo/nbe_…`. The Kobo is connected to a computer, and in `.kobo/Kobo/Kobo eReader.conf` (a hidden
folder on the device), in the section `[OneStoreServices]`, the line `api_endpoint=` is set to that address. A Kobo can be
given an address and nothing else (it sends no header of its own), which is why **the secret is part of the address**: it is
kept as a hash, masked in every line of the log, and repeated in no answer but the ones that hand the device its addresses
(`/v1/initialization`, the sync and the metadata). Put nexbooks behind HTTPS, and keep in mind that a proxy in front of it logs
the address unless told not to. The password of the account is no way in.

**Beta.** This has been checked against the protocol and against programs that work with real devices, not yet on a real Kobo.

**nexbooks passes nothing on to Kobo.** The device is handed the table Kobo's store hands out, with every address of the
store's server and of its pictures changed to nexbooks; the rest (web pages, the sign-in of the shop) stays as it is and never
holds the secret. A route nexbooks has no use of answers `200 {}` for a good connection (and `401` for a bad one), so the **Kobo
store and Kobo's own services do not work** while the device points here; taking the line out of the file brings them back. No
answer is ever a redirect, and a slash too many in the address is taken out.

**Signing in.** A wrong, blocked or deleted secret, an account that is blocked or has not set up the second factor
the operator requires, and a secret of another kind are `401 {"detail": {"code": "unauthorized"}}`. The operator's switch off
is `403 ereaders_off`. Wrong secrets count against the sender like wrong passwords, in a count of their own (five, then a quarter
of an hour of `429`; the same wrong secret again counts once; a connection that exists is never turned away by it). One account
may make 600 requests a minute (`429 kobo_too_often`); files count as for the download. A body is at most 32 KiB, under `/v1/analytics/` 1 MiB, which nothing reads (`413`).

| Route | What it does |
|---|---|
| `GET /kobo/<secret>/v1/initialization` | `{"Resources": {...}}`: the whole table of Kobo's store, the addresses of its server and its pictures (`library_sync`, `library_metadata`, `reading_state`, `image_url_template`, `image_url_quality_template`, `device_auth`, ...) under `/kobo/<secret>`, everything else as it is. Header `x-kobo-apitoken: e30=`. |
| `POST /v1/auth/device`, `POST /v1/auth/refresh` | The device's sign-in. Answers tokens made up on the spot (`AccessToken`, `RefreshToken`, `UserKey`); nothing reads them again, the address is the only proof. |
| `GET /v1/library/sync` | The change of the list since the answer whose token the device sends in `x-kobo-synctoken` (none: everything). A JSON list of `NewEntitlement` (the book with its `BookMetadata`, the file address in `DownloadUrls` and its `ReadingState`), `ChangedEntitlement` (the metadata changed, or `IsRemoved: true` for a book that left the list or that the person no longer sees; it carries no title), and `ChangedReadingState` (the place moved elsewhere). At most 30 entries; with more to tell the answer carries `x-kobo-sync: continue` and the device asks again. The header `x-kobo-synctoken` of the answer is what the device sends next. An answer counts as taken when the next request brings its token: a device that comes back with an older token is told again what it missed. |
| `GET /v1/library/{name}/metadata` | `[BookMetadata]` of a book of the list. |
| `GET /v1/library/{name}/state` | `[ReadingState]`: `CurrentBookmark` (`ProgressPercent`, and the `Location` the Kobo reported when nothing has moved the place since), `StatusInfo` (`ReadyToRead`, `Reading`, `Finished` from 99 %), `Statistics`. |
| `PUT /v1/library/{name}/state` | Body `{"ReadingStates": [...]}`. The state with `EntitlementId` = `{name}` is kept as the person's place in the edition by the rule of `PUT /editions/{id}/progress` (the newest report stays): `fraction` from `ProgressPercent` (0 to 100, clamped; `Finished` is 100), `locator` empty, `device` the name of the connection, `device_key` `kb-` and its number. A `Location` with a control character, a text that is too long or a text that holds half of a character (a lone surrogate) is not kept; the share is. A body that is not JSON, holds no such state or a percentage that is not a finite number changes nothing and is answered like one that did: `{"RequestResult": "Success", "UpdateResults": [...]}`. |
| `DELETE /v1/library/{name}` | The person took the book off the device: `204`. It stays on the list and is not sent to **this** connection again until the person puts it on the list anew (the other connections of the person still get it). |
| `GET /download/{name}.epub` | The EPUB as it lies in the library, with `Range` and `If-None-Match` as in the interface and the checks of `GET /files/{id}/download`. |
| `GET /image/{name}/{width}/{height}/{quality}/image.jpg` (also `.../{quality}/{greyscale}/image.jpg`) | The cover as a JPEG no bigger than the device asks for (at most 2000 by 2000, never enlarged), with `ETag`. `404` for a book without one. |
| `GET /v1/library/tags` | `[]`. Every other route, any method: `200 {}`. |

A `{name}` is the UUID the Kobo knows a book by. nexbooks makes it from the person and the edition, so it is the same on every
Kobo of one person and means nothing to another. A name that is not on the list of the person, a book of a library they are
not in (any more) and a name nobody has are all the same `404`, for the metadata, the place, the file and the cover alike.

**What a report becomes, and what the Kobo is told.** The share of the book comes from the Kobo as a percentage; the place it
words itself (a KoboSpan and the file it is in) is kept beside the share for as long as nothing else moves the place, so that
the Kobo gets back exactly what it reported. A place from the browser or the app has no such location: the Kobo is given its
share. The Kobo that reported a place is not told it again; the other Kobos of the person are.

## Libraries

A library is a folder of books that the operator made known. nexbooks only reads it. It lies at or below one of the
folders in `NEXBOOKS_LIBRARY_ROOTS` (`/books` in the container); the path is resolved (`..` and links followed) before it
is compared, may not be the data folder, hold it or lie in it, and may not overlap another library. A person sees the
books of the libraries they are a member of and of no other, **the operator included**: the operator manages every
library and sees the books of those they are a member of. The rights, each one including the one before it: `read` sees;
`acquire` also asks for books to be fetched (see *Fetching books*); `manage` also starts a scan and reads the list of problems.

| Route | Who | |
|---|---|---|
| `GET /libraries` | a browser, a device, a key | The libraries of the caller: `id`, `name`, `kind` (`ebook`, `audio`, `both`), `my_right`, `state` (`idle`, `queued`, `running`), `counts` (`ebooks`, `audiobooks`, `files`, `bytes`, `problems`, `review`, `entries`, `pending`; the problems, the books to look at and the numbers of the matching only for `manage`: `entries` are the books that have a place in the matching, `pending` those not looked at yet), `matching` (`idle`, `queued`, `running`, or `off`: books wait and no source is on) and `resting` (the sources the matching waits for: `source`, `until`), the last two only for `manage`. No path. |
| `GET /libraries/{id}` | the same | One of them. A library the caller is not a member of answers `404`, like one that does not exist. |
| `GET /libraries/admin` | the operator, in a browser | Every library with `path`, `scan_enabled`, `scanned_at`, `progress` (`done`, `total`), `report` of the last run and the `members`. |
| `GET /libraries/roots` | the operator | The roots and the folders directly in them, for a picker. |
| `POST /libraries/check` | the operator | `{"path": …}`: `{"ok": true, "path": …}` or `422` with the reason as `code` (`path_relative`, `path_missing`, `path_not_folder`, `path_outside_roots`, `path_is_data`, `path_unreadable`, `path_overlap`, `no_roots`) and `field: "path"`. |
| `POST /libraries` | the operator | `{"name", "path", "kind", "scan_enabled"}`. Makes it, makes the operator a member with `manage`, queues the first scan. `201`. |
| `PATCH /libraries/{id}` | the operator | `name`, `kind`, `scan_enabled`. The path cannot be changed: make a new library. |
| `DELETE /libraries/{id}` | the operator | Takes the library out of nexbooks. **The files stay where they are.** `204`. |
| `PUT /libraries/{id}/members` | the operator | `{"members": [{"user_id", "right"}]}`, all at once; who is left out leaves. |
| `PATCH /libraries/{id}/members` | the operator | `{"changes": [{"user_id", "was", "right"}]}`: only these persons change, each from the right the caller saw (`was`, null for not a member) to `right` (null to take them out). `409 members_changed` and nothing changed when somebody else changed one of them meanwhile. |
| `POST /libraries/{id}/scan` | a browser: the operator, or a member with `manage` | Reads the folder now. `202`. A scan that already waits is the same scan. With the body `{"everything": true}` every file is read again, not only the new and changed ones (the checksums of files that did not change are kept; what a person decided stays). |
| `GET /libraries/{id}/problems` | a browser: the operator, or a member with `manage` | The files the scan could not read: `path` (inside the library), `reason` (`unreadable`, `unsupported`, `too_large`, `too_many_entries`, `zip_bomb`, `encrypted`, `timeout`, `crashed`, `link`, `changed`, `bad_name`, `failed`) and an English `message`. Paged with `limit` and `offset`. |

### The scan

In the background, one library at a time, and it survives a restart. Only files that are new or whose size or time
changed are read again; a file changed in the last minute waits for the next run. It never follows a link out of the
folder (links are listed as problems), and a folder that is gone, or holds no book file while nexbooks knows books in it,
stops the run (`report.aborted`: `offline` or `empty`) instead of marking everything missing; a folder too big for one
walk (more than 500,000 entries) is read as far as the walk went, nothing is marked missing (`truncated`). A folder or
file whose name is no valid text is left out and listed (reason `bad_name`); a fault while one file or one book is handled
is listed (reason `failed`) and does not end the run. Files that are gone are marked missing, never deleted. The instance setting `scan_minutes` (0, 15, 60, 360 or 1440; `PUT /settings`) is how often the libraries
are read by themselves; "Scan now" always works.

**A book shows as soon as its files are read**, not at the end of the run: a unit (the files of one e-book, or the parts
of one recording) is put together when its last file is read, and the matching with the sources begins for it in a lane
of the job queue of its own, beside the scan (`report.early` counts the books put together on the way). What cannot be
certain waits for the end, as it always did: a file beside the books (`metadata.json`) speaks for the books below it only
when they are one, which is known when every file below is read.

**Every file carries the generation of the rules it was read by** (`files.rev`). When the rules change in a way the books
that are in should get (the year, the author of a PDF, the genre, the title), the next run reads every file again once
(`report.reread`, not counted as a change); a person's corrections and decisions are never touched. **That run begins soon after the start** (not at the next turn of the interval, which may be an hour away) for a library the operator lets be read by itself, and reads the files again **at a pace**: after each file it rests as long as the reading took, at most a second, so a share is kept busy for half of the time. A new or changed file is read at full speed.

**"Read everything again" asks for it by hand**: `POST /libraries/{id}/scan` with `{"everything": true}` (a real boolean;
any other value is a `422`) reads every file again, once, and is open to the operator and to a member of the library with
`manage`, like the plain scan. A second request while one waits is the same request.

## Books

Everything here only reads and is open to a browser, a device and a key. A work, series, person or cover that lies only
in libraries the caller is not a member of answers `404`; lists and counts leave it out (the database is asked for exactly
the caller's libraries, nothing is filtered afterwards). A work shows the editions of the caller's libraries **and what
those editions say**: its title, description, genre, series and volume, original title, year, pages, cover, colour,
authors and `added_at` are worked out from the editions the caller sees, never from a copy in a library they are not a
member of (so the same book can read differently to two people, and `genre`, the numbers per genre, `sort`, a series and
a person follow what the caller sees). A series or person the caller sees no edition of answers `404`.

A **work** is a book; its **editions** are its forms: an e-book per language (its files in several formats are one
edition: *the edition is the content, the format is the file*) and each recording.

| Route | |
|---|---|
| `GET /works` | A page of books. Filters: `library`, `genre` (`roman`, `krimi`, `fantasy`, `scifi`, `klassiker`, `sachbuch`, `kinder`, `lyrik`, `none`), `kind` (`ebook`, `audio`), `language`. `sort`: `shelf` (genre, author, series, volume), `added`, `title`. `page`, `per_page` (at most 200). |
| `GET /works/stats` | The numbers above the shelf for the same filters: `works`, `editions`, `ebook_editions`, `audio_editions`, `works_with_ebook`, `works_with_audio`, `genres`, `languages`. |
| `GET /works/{id}` | One book with its editions: files (`id`, `path` inside the library, `format`, `size`, `part`, `duration`, `sha256`, `protected`: copy-protected, which nexbooks does not open), chapters, narrators, translators, and of each edition what the page of a book needs to correct it: `authors`, `series_name`, `series_pos`, `genre`, `genre_guess`, `corrected` (the fields a person corrected by hand) and `matched_by` (the sources that know the edition). `delivery` (`release`, `indexer`, `delivered_at`) of an edition that was fetched from a release, for a member with `manage` only (null for everybody else and for a book that was not fetched). `duplicates`: groups of recordings of the work whose running times are within 3 percent of each other (probably one recording twice; a hint, nothing is merged). A description comes as plain text: paragraphs are apart by an empty line (`\n\n`), and a single `\n` inside a paragraph is the wrap of the source (set it as a space). |
| `GET /files/{id}/download` | One file of a book as it lies in the library (`Content-Disposition: attachment`, `nosniff`, `Range` works). The request names the file by its number only: the place on the disk comes from the database and is checked against the library folder again (no `..`, no link, no file outside it). A file of a library the caller is not a member of, a file beside a book (`cover.jpg`, `metadata.json`) and a file that is gone are `404`, the same as a number nobody ever had. |
| `GET /files/{id}/content` | The bytes of a file for the reader in the browser and for players of apps: the same file as the download, with `Range` (a part, `206`) and an `ETag` (ask again with `If-None-Match`, `304`), `Cache-Control: private, no-cache`, `nosniff` and a policy that makes the answer show and run nothing if somebody opens the address as a page. Named by its number only, like the download; a file the caller may not see is `404`. A device token and an API key read it. One person (all their browsers and devices together) has at most 12 files being sent at one moment (`NEXBOOKS_MAX_STREAMS`; a player holds one or two) and may ask 600 times a minute: beyond either `429` with `Retry-After` (`too_many_streams`, `stream_too_often`); the download counts the same. |
| `GET /search?q=…` | The books of the caller's libraries that the words find, best first (`limit`, at most 30; `items` are books as in a list). Every word is the start of a word of the title, the people (authors, narrators, translators), the series, the description, or an ISBN or ASIN; case and accents do not count, `ß` is `ss`; all words must be found. **Only what the caller may see**: the index holds every word with the library of its book in front and a search asks for the words of the caller's libraries, so what other people have is neither found nor counted, and costs the search no time. A person may search 240 times a minute (`429 search_too_often`); a search that takes more than 5 seconds is stopped (`503 search_too_slow`). |
| `POST /search/catalog` | `{"q": …}`: asks the catalogues of the sources the operator switched on for the words, side by side (a browser only: it costs the sources a request; 20 a minute and person). **One hit for each book** (`items`, at most 24): its e-books, recordings, translations and editions are one hit, named the way it is called in the first of the languages the operator fetches in that it has (`title`, `language`; `allowed` false and `languages` for a book in none of them), with `authors`, `year` (first edition), `series`, `series_pos`, `ebook`, `audio`, `cover_url`, `sources`, `editions` (the single records behind it: `source`, `form`, `language`, `title`, `year`, `isbn`, `publisher`, `narrators`, `minutes`, `variant`, `pages`), `label` (`work`, `box`, `guide`, `companion`, `drama`, `part`, `comic`), `procurable` and `why_not` (only a book can be acquired from its hit: `not_a_book` for what is labelled, `volume_unknown` for a book named like its series whose volume nobody knows) and `group` in the order of a library (`match` the book asked for, `series` the other volumes of its series, `more`, `related` what is labelled, `elsewhere`). `available` is false while no source is switched on, `failed` when every source that was asked failed; `resting` names the sources that gave nothing for this search (`source`, and `until` when it rests after errors, null when it did not answer this search): the hits are incomplete then. One answer that runs out of time does not make a source rest; a second within five minutes does, and a `429` rests it for as long as its `Retry-After` says (a minute when it says nothing). The answer comes after a moment with what the sources gave by then: `pending` true and a `ticket` while some are still asked. Only the typed words go out. A person has one search that asks at a time: a new one calls the last one off, and so does `DELETE`. `cover_url` is the cover of the hit, fetched from its source when the page shows it; `token` is null until the hit is opened. |
| `GET /search/catalog/{ticket}` | The same search again with what the sources added meanwhile (it waits up to two seconds for them); `404` for a ticket that is not the caller's or is older than ten minutes. |
| `DELETE /search/catalog/{ticket}` | The page gave up on the search: nothing more is asked for it (a question waiting for its turn at a source sends nothing), and the answer stays as it was. `204`; `404` for a ticket that is not the caller's. |
| `POST /search/catalog/{ticket}/books` | `{"key"}` of a hit of the search: the page of the book, made now (the person opens the hit or acquires it), `{"token"}`; it carries the series and the volume of the hit, so that a wish for it looks for that volume. `404 search_gone` when the search or the hit is gone. |
| `GET /search/catalog/{ticket}/covers/{key}` | The cover of a hit (WebP), fetched from its source's own host the first time a page shows it and kept for a day (150 a minute and person). `404` when it has none. |
| `GET /works/{id}/cover` | The cover as WebP. `cover_url` of a work carries `?v=…`, which changes with the picture; with it the answer may be kept for good (`Cache-Control: private, … immutable`), without it the picture is asked about each time (`ETag`, `304`). |
| `GET /series/{id}` | A series with the volumes the caller sees, in order. |
| `GET /people/{id}` | An author or narrator with `wrote` and `narrated`. |

A book in a list: `id`, `title`, `authors`, `series` (`id`, `name`, `pos`), `genre`, `genre_guess` (the genre is a guess: no file and no source said it, it is the genre most of the author's other books in the same library have; made from the books of that library only), `color` (the main colour of the
cover, `#rrggbb`; a colour of the genre's palette without a cover), `cover_url` (null without a cover), `pages` (counted
for a PDF, **estimated** for other e-books from the amount of text, and from the running time for a recording alone),
`year`, the look of the spine (`pattern` 0 to 4, `tall` 0 to 1, `gold`), `audio_only`, `added_at` and `editions` (`id`,
`kind`, `variant`: `unabridged`, `abridged`, `dramatized`; `variant_uncertain` (nothing proves which reading it is); `language` and `language_guess` (the language was worked out from the e-book of the work, or from the library, not told by a file, a source or a person); `formats` such as
`["epub", "azw3", "mobi"]`; `pages`; `duration` in seconds; `narrators`). The files are listed with a work and can be
downloaded, read and played (`/files/{id}/content`, with `Range`: a player asks for the part it needs, and for the start
of the next file of a recording before the last one ends).

## Reading and listening

Where a person is in an edition, and their bookmarks. **Both are the caller's own**: no route names another person, and the
operator reads nobody's. An edition the caller does not see (not a member of its library, no file on the disk) is `404`,
exactly like a number nobody had. Reading them is open to a browser, a device and an API key; **setting** them wants a browser
or a device (a key only reads).

| Route | |
|---|---|
| `GET /editions/{id}` | An edition with its work (`work`, as in a list) and the edition itself (`edition`: files, chapters, as in `GET /works/{id}`), for a reader or a player. The files of a recording come in the order of their `part`, each with its `duration`; laid end to end they are one line of time, and the `start` of every chapter is a time on that line (seconds from the beginning of the first file). Files without chapters inside are one chapter each, except that consecutive files whose titles show that one chapter runs over them (`Chapter 1 (1/3)`, `Chapter 1 (2/3)`, `Chapter 1 - Part 3`) are one chapter that starts in the first of them. |
| `GET /editions/{id}/progress` | Where the caller is: `locator` (a CFI for EPUB, MOBI and AZW3, `page:12` for a PDF, `time:8040.500` for a recording: seconds on its line of time; **empty** for a place KOReader's sync reported, or one carried over to a new format, which has only `fraction`: open the book at that share), `position` (those seconds for a recording, `null` for an e-book), `fraction` (0 to 1), `unit` (`chapter`, `page`, or `time` for a recording), `chapter`, `chapter_count`, `chapter_title`, `device_key`, `device` (a name for people), `moved_at` (when the reader was at this place), `received_at`, and **`age`**: how many seconds ago the reader was at this place by the server's clock when it answered (compare ages on your own clock, never the server's time of day with yours). `null` when they have not begun. |
| `PUT /editions/{id}/progress` | Reports a place: `locator` (for a recording `time:` with at most three places after the point, no further than its length; anything else is `422 bad_place`, and so is a time for an e-book), `fraction`, `unit`, `chapter`, `chapter_count`, `chapter_title`, `device_key` (a key the device made up for itself, letters, digits, `_` and `-`), `device`, and **`age`**: how many seconds ago the reader was at this place. The server counts back from the moment the report arrives (`moved_at` = arrival - `age`, an age of more than a week counts as a week), so a clock that is wrong keeps no other device out. **The newest report of all devices stays**; at a tie the later arrival wins. Checking and writing is one statement. The answer is `{"applied": …, "progress": …}`: `applied` is false when a newer place from another device was there, and `progress` is the place that stands, to ask the person whether to jump there. A browser reports every few seconds while the person reads and when the page is left (`fetch` with `keepalive`). |
| `GET /progress` | The caller's places in all their books, the newest first; `work` narrows it to one book. A page at a time: `limit` (1 to 200, default 200) and `offset`; the header `X-Total-Count` says how many places there are in all, so a program knows whether to ask for the next page. |
| `GET /editions/{id}/bookmarks` | The caller's bookmarks in an edition, in the order of the book: `id`, `locator`, `position` (seconds, for a recording), `fraction`, `chapter_title`, `note`, `created_at`. |
| `POST /editions/{id}/bookmarks` | `{"locator": …, "fraction": …, "chapter_title": …, "note": …}`: sets one (`201`); the same place again gives the one bookmark (`200`, its note unchanged). A note has at most 500 characters. At most 500 in an edition (`409 bookmark_limit`), kept by the statement that writes the bookmark, so a crowd of requests at the same moment cannot write past it. |
| `PATCH /bookmarks/{id}` | `{"note": …}`: changes the note of one of the caller's own bookmarks; another person's, or one in a book the caller no longer sees, is `404`. |
| `DELETE /bookmarks/{id}` | Removes one of the caller's own bookmarks (`204`); another person's is `404`. |

**The e-book and the recording of a book keep a place each.** They are two editions, and nothing moves the one when the
other moves: a recording may be shortened, a radio play or another translation, and a place in one says nothing about the
other.

The look of the reader belongs to the profile (`PUT /me/preferences`): `reader_font` (`fraunces`, `inter`, `publisher`),
`reader_size` (percent: 80 to 200), `reader_line` (hundredths: 120 to 200), `reader_margin` (`narrow`, `normal`, `wide`),
`reader_theme` (`auto`, `light`, `sepia`, `dark`), `reader_justify`, `reader_flow` (`paged`, `scroll`). The player's choices
too: `player_speed` (hundredths, 50 to 300 in steps of 5: `125` is 1.25 times), `player_back` (seconds: 5, 10, 15, 20, 30,
45, 60), `player_ahead` (10, 15, 20, 30, 45, 60, 90, 120), `player_rewind` (ten seconds back after a pause of five minutes
or more).

## Sources and matching

nexbooks can ask **sources on the internet** about the books it found, to learn the series, the place in it, the
description, the genre, the year, the cover, the narrators and, for a recording, which recording it is. **No source is
required and none belongs to the project.** What goes out is a title, an author and an ISBN, never a file, a folder name
or the name of a library. Everything that comes back is kept (a month for a book found, a day for a miss; what Audible and
Apple say, a day), so a book is asked about once. The main switch and one switch per source are the operator's.

| Source | From the start | Needs | Asked about |
|---|---|---|---|
| Open Library | on | nothing | e-books and recordings; works, languages, cover, description |
| Audible (one market at a time: `de`, `us`, `uk`, `fr`, `ca`, `au`, `it`, `es`, `jp`, `in`, `br`) | **off**: the operator chooses once (`audible_decided`) | nothing | recordings; narrators, running time, abridged or not, series, ASIN; the lists "New releases" and "Coming soon" of Discover |
| Hardcover | on, but **not connected** | a read-only token of the instance (the operator pastes it) | works in many languages, series with the place in them |
| German National Library | on | nothing | German titles only; the author, the year of the first edition, ISBN, publisher, series, subject group and headings |
| Apple Books Charts (a country at a time) | on | nothing | Discover only: the chart of the best-selling e-books; never a book by title |

Each source is asked slowly (Hardcover: 60 a minute, a burst of 10, 5,000 a day; the others about one a second), rests after
errors (30 seconds, doubled each time, up to an hour; as long as a source asks with `429`), and a refused Hardcover token
stops that source until the token is replaced. The way out is the one of `services/outbound.py`: https only, hosts out of a
fixed table, every address of a name checked to lie in the public internet, no redirect except to the source's own hosts.

The operator's routes (a signed-in browser):

| Route | |
|---|---|
| `GET /sources` | The main switch (`enabled`), `markets` and `available_markets`, `hardcover_connected`, `audible_decided` (whether the operator has chosen about Audible, in either way: its switch has been saved), `sources` (`id`, `enabled`, `state`: `ready`, `main_off`, `off`, `not_connected`, `token_invalid`, `resting`; `requests_today`, `requests_per_day`, `last_ok_at`, `last_error`, `last_error_at`, `resting_until` and `waiting` (the books that wait for this source: the last look at them found it away)), `waiting` (books not matched yet) and `to_look_at`. |
| `PUT /sources` | `{"enabled", "markets": ["de", "us"], "sources": {"dnb": true}}`: any of them. Markets are chosen from the list (`422` otherwise), one to six. |
| `PUT /sources/hardcover/token` | `{"token": …}`. Sealed with the server's secret and **never read back**. Hardcover is asked once with it: a token it refuses is `422 source_token_invalid` and not kept; if Hardcover cannot be reached the token is kept. A leading `Bearer ` is dropped. |
| `DELETE /sources/hardcover/token` | Disconnects Hardcover. |
| `POST /sources/rematch` | Match every book again that no person decided on, **the unsure first, the books that were sure last** (after the books that are new). `202`, `{"books": n}`. |

### The three answers

Every edition (one form of a book in one library) gets an answer, by the points a candidate earns against what the files
say (`services/match/score.py`): **sure** (applied by itself), **maybe** (a person looks, with the best proposals) or
**open** (nothing fits). An ISBN or ASIN settles it, unless everything else says it is another book (a companion or a collection, or another title together with another author or another language: the ISBN in a file is sometimes a stranger's): then it is at most a **maybe**, with the reason `isbn_conflict`. Otherwise the title (the whole title, not a prefix), **the author with
first name or initial, never the last name alone**, the volume (also a bare number: Band 2 is not Band 3), the language
of the file, the year, and for a recording its **running time within 5 percent** of a product's (from 0.95 to 1.05 of it), which also says whether it is
the unabridged reading, an abridged one or a play. A running time up to a fifth shorter is *plausible*: no proof, no
objection. The running time alone never makes a book sure: the title, the author and the volume must fit as well.
**A recording is sure for the book** when its running time proves it, when it is plausible, or when a second, independent
source tells the same book; which recording it is (abridged or not) then stays *not proven* (`variant_uncertain` on the
edition) until the running time proves it or a person with `manage` sets it (`PATCH /editions/{id}` with `variant`). A
recording one source alone tells, with no running time to prove it, is a **maybe** (`runtime_unproven`). A record that is
the name of the series (`Auris 2`) is evidence for the series and the volume, never another book; the imprint of a
publisher (`Fischer 1983`) and the name of an author are no series. Another title with the same author and the same series
and volume, or the same year, is `possible_translation`, never sure. Companions ("Summary of …") and collections are never the book. A book
the sources did not answer for stays *pending*: it is not called open because a source was away. The same when a source that gave the book its values last time is only resting or failing now: nothing is decided on the word of the rest. **The same when a source the book is asked at, by the language of its file (German: the National Library and Audible; English: Open Library, Audible and Hardcover), failed, rests, is at its daily limit or was switched off just now, while the others found nothing or only part of the book:** it stays pending until that source has answered (a book that is sure needs no more), and a source at its daily limit rests until midnight like one that said when to come back. Books that wait for a resting source are not asked about again until it is back. A source the operator never switched on is no one a book waits for. A source the operator has switched off keeps what it gave, except where a new answer brings something in its place, and only while another source still knows the book by the same name.

What a source gives is laid under what the files say: **a person, then the file, then a source**. A source fills only what
the files leave empty, and a value a person set stays through every scan and every new match. It is kept per edition, so a
member of one library never reads what an edition of another library says.

The list of books to look at, for **a member with `manage`** (the operator only when they are one):

| Route | |
|---|---|
| `GET /libraries/{id}/review` | `total` and `items`: `id`, `edition_id`, `work_id`, `state` (`maybe`, `open`), `reason` and `reason_text`, `kind`, `title`, `authors`, `language`, `year`, `series`, `file`, `cover_url` and the `proposals` (`id`, `source`, `title`, `authors`, `narrators`, `year`, `languages`, `series`, `runtime_min`, `variant`, `score`, `why`: `[{code, text}]`, `missing` and `against`: `[{code, text}]` (what is missing to be sure of the proposal, and what speaks against it though it could be the book), `detail` (the numbers of `year_off`), `lacks` (only what would really make it sure, worked out in points: `isbn`, `second_source`, `language`, `author`, `runtime`, `volume`, `year`; each is enough alone when `lacks_join` is `or`, all are needed together when it is `and`), `cap` (what holds the proposal back whatever its points: `author_weak`, `author_unknown`, `language_missing`; empty for nothing), `fatal`, `found`, `cover_url`) and `gap` (`sure_at`, `points`, `missing` with `join` and `cap` as for the best proposal, and `tie`: when two proposals are about as good, what tells them apart: `volume`, `year`, `language`, `variant`, `title` or `nothing`). Paged with `limit` and `offset`. A member who may only read: `403`; not a member: `404`. |
| `GET /review` | The same list over every library the caller looks after (`manage`), each item with its `library_id`, paged with `limit` and `offset`. A library one only reads or is no member of adds nothing (the operator's neither, unless he looks after it); nobody gets an error, only fewer books. |
| `POST /review/{id}/accept` | `{"proposal_id": …}`: takes a proposal. Only the id is read from the request; the data is what the server kept. `{"to_look_at": n}`. `409 entry_changed` when the book is not on the list any more (another person or the matching got there first). |
| `POST /review/{id}/keep` | "Leave it as it is": the book drops off the list. Only a book that is on the list, and only once: `409 entry_changed` otherwise. |
| `POST /review/{id}/search` | `{"title", "author"}`: asks the sources with those words (a person's request waits a few seconds at most, then `429 source_busy`) and adds what it finds to the proposals. `409 sources_off`, `502 source_failed` (also an answer nobody can read), `409 entry_changed`; a person may ask 30 times a minute (`429 review_search_too_often`). |
| `GET /review/{id}/cover?id=…` | The picture of a proposal as WebP, fetched from the source's own host the first time (a browser only). |
| `GET /review/{id}/file` | What the files of the book say about themselves, for a member with `manage` (the operator no exception): `files` (`id`, `name`, `folder` inside the library, `format`, `size`, `part`, `duration`, `pages`, `chapters`, `protected`; never a path on the disk), `folder`, `says` (title, subtitle, `authors`, `narrators`, series, ISBN, ASIN, publisher, year, `language`, `text_language`, genre, `subjects`, `pages` with `pages_counted`, `duration` in seconds, `chapters`; an empty value is "the file says nothing") and `cover_url`. For every proposal the comparison, field by field (`title`, `authors`, `narrators`, `series`, `volume`, `isbn`, `language`, `text_language`, `year`, `runtime`, `publisher`): `state` is `agrees`, `similar` (close, a person looks), `differs` (the file contradicts the proposal), `file_silent` (the file says nothing about it) or `source_silent` (the source does not know it); a field both sides leave empty is not listed. Read from what the scan kept, no file is opened. To read in the book or listen, fetch the file the usual way (`/files/{id}/content`, with `Range`): nothing of that saves a place. |
| `GET /review/{id}/file-cover` | The picture inside the file of the book as WebP, for a member with `manage`; `404` for a file without one. |
| `GET /editions/{id}/cover` | The cover of an edition as WebP, for a member of its library. |
| `POST /editions/{id}/unlink` | Takes the match with the sources back; the book is as the files describe it and is not matched again by itself. |
| `PATCH /editions/{id}` | Corrects by hand: `title`, `authors`, `series`, `series_pos`, `genre`, `language`, and for a recording `variant` (`unabridged`, `abridged`, `dramatized`: which reading it is; `422` for an e-book). A field left out stays, `null` takes a correction back, an empty text corrects to nothing (the title excepted: `422 invalid_input` naming the field, a book is never without one; `null` takes a corrected title back like any other field). |
| `POST /editions/{id}/join` | `{"work_id"}`: puts the edition under another work the caller sees (`404` for a work they do not see, the same as for one that is not there). |
| `POST /editions/{id}/split` | Makes the edition a work of its own. A join or split by hand is never undone by the matching. |
| `POST /editions/{id}/wrong-book` | A book nexbooks fetched is not the one wished (a member with `manage`, in a browser; a device or a key gets `403`). The files of the edition that are on the disk go into the trash of the library (they come back from there, `POST /procure/trash/{id}/restore`; files that were gone from the disk are named in `missing` and do not hold the book back), the release that delivered it is blocked for the book wished (`by_hand`, lifted on the blocklist), a line `removed` with the note `wrong_book` stands in the history, the edition leaves the shelf (and the work with it when it was the only edition), and a new wish looks for another release at once (a wish that waited for a better format or for its next try searches now). **All or nothing:** when the files cannot be moved nothing is blocked, written or removed. **What the people had in the book** (the place, the bookmarks and the finished passes of everybody who read or heard it) is kept with the trash entry and given back, unless a person has something of their own in it by then, when the files are taken out of the trash and read again; it goes when the entry goes (deleted, emptied, or its days over). `GET /works/{id}` tells a member with `manage` the days the trash keeps it (`delivery.trash_days`). Answer: `trash_id` (null when no file was there to move), `replacement` (`searching`: a wish looks now, `idle`: the wish is made but fetching by itself is off, so it waits for "Jetzt suchen", `have`: the book has another edition of the kind, `none`: no book left to wish for, `failed` with `code`), `work_id` (null when the work is gone), `missing` (the names of the files that were gone) and `people` (how many people have something that is kept). `409 not_fetched` for an edition that came from no release (it lay in the library, or a person uploaded it), `409 trash_failed` when the files could not be moved (nothing was changed), `409 hidden_folder_link`; `403` for a member who may only read or only acquire, `404` for a non-member. |

When two editions are one work for a source (Hardcover's book, Open Library's work), the matching puts them under one work
(translations, an e-book and its recording). Joining and splitting undo each other.

## Discover

The lists of the sources set against what the caller has, books that are not on a shelf, and the wish list. **The lists, the
volumes of a series and the pages of books are public data, the same for everybody; whether a book stands on the caller's
shelf, the gaps in their series and the wish list are worked out from the caller's own libraries and rows.** Nothing asks a
source while the caller waits: what was never fetched is queued (the answer says `fetching`), with the operator's main switch
off nothing is queued and what was kept is shown. A book that is not on a shelf is named by a `token`, 18 random characters
(never a number), in the address of its page and its cover. **Its cover and its description are there to be shown:** they are kept
for a day and then gone (and in no backup); a list or a page that needs them fetches them again.

| Route | |
|---|---|
| `GET /discover/lists/{best,new,soon,trending}` | Query: `market` (two letters, empty: the profile's, else the language's), `format` (`all`, `ebook`, `audio`), `language`, `genre`, `hide_owned` (empty: the profile's `discover_hide_owned`, on from the start). Answers `state` (`ready`, `fetching`, `empty`, `failed`, `main_off`, `no_source`), `asking`, `availability`, `sources` (the sources behind the cards shown), `languages` (the ones in the list, for the filter), `total` and `owned` (the cards that fit and how many of them the caller has) and `items`. A card: `token`, `title`, `authors`, `rank` (best sellers and trends only), `kinds`, `forms`, `released`, `upcoming`, `genre`, `color`, `cover_url`, `sources`, `owned_work_id` (the book on **the caller's** shelf, or null), `wished`, `series`. While the sources of a list are off (Audible for `new` and `soon`, Hardcover not connected for `trending`), the list is made from the themed shelves of Browse: their new books (the newest first), the ones a source gave a day after today, and what they rank highest (the first of every shelf, then the second); `sources` and `availability` are then those of the shelves. |
| `GET /discover/for-you` | `gaps`: a card for each series the caller reads that lacks a volume (`have`, `out`, `missing`, `soon`, `volumes`: `own` with the book, `missing` or `soon` with the card), `authors`: more of the authors on their shelves, `pending` while the sources are looked up in the background. |
| `GET /discover/browse` | **Browse**: the wall of the thirteen themed shelves. Query: `market`, `hide_owned` as for the lists. Answers the state of the sources (`state`, `availability`, `asking`, `pending` while parts of the shelves are being fetched, `sources`, `fetched_at`), `markets`, `departments` (`bel`, `sach`, `kj`, each with its shelves: `key`, `books`, `new` and `fan`, three cards for the tile) and `strips` (a shelf with at least three books in a section: `shelf`, `section`, `sections` with their counts, `items`). A strip card is a list card with `award`, `topics`, `pages`, `minutes` (the longest recording, where a source says), `year` and `blurb` (the words on the back, kept for a day). The shelves are public data; the caller's own things (`owned_work_id`, `wished`) are worked out from their libraries. |
| `GET /discover/shelves/{key}` | One shelf opened. Query: `section` (`new`, `popular`, `award`, `classic`), `language`, `format`, `years` (`new`: this year and the one before, `mid`: back to sixteen years, `old`), `topic`, `sort` (`pop`, `new`, `short`), `hide_owned`, `limit` (60, at most 120) and `offset`. Answers `total` and `owned` under the filters, the facets (`sections`, `languages`, `topics`, each with its count under the other filters), `year_bounds` and `items`. `404` for a shelf that is none. |
| `GET /discover/browse/personal` | The shelves made for the caller from their own history, on the server only: `because` (the book finished last: more by the same people, then its genre), `like` (a favourite is a book finished within five days of the first report), `short` (under 250 pages), `long` (recordings of 20 hours or more; only where a source says the running time). Rows with no book are left out. Nothing is asked of a source. |
| `GET /discover/series/{id}` | The volumes of one series for the page of a book (404 for a series the caller sees no volume of). |
| `GET /discover/books/{token}` | The page of a book of a source: `titles` (every title it is known by, with its language), description, `forms_known` (kind, language, variant, minutes, narrators, pages, source), `volumes` of its series, `more` books of the author on the caller's shelves, `pending` while other forms are looked for. |
| `GET /discover/books/{token}/cover` | The cover as WebP, `ETag`, immutable when `?v=` is the start of the picture's name. A card names this address whenever the source has a cover; the server fetches it when it is asked for (once for concurrent requests) and keeps it for a day. `404` when there is none, `429` with `Retry-After` while the source is busy or used up for the day. A book the caller has in a library shows `/works/{id}/cover` instead. |
| `GET /later`, `PUT /later/{work_id}`, `DELETE /later/{work_id}` | The caller's own list for later: books on their own shelves (the wish list is for books that are not). `GET` gives `{"items": [work]}`, the newest first, only works the caller sees. The same list as under `/api/app/1/later`. `PUT` is `404` for a work the caller does not see and `409 later_full` past 500 entries; both changes answer `204`, and asking twice does no harm. A book that is finished leaves the list. `GET /works/{id}` says `later`. |
| `GET /wishlist`, `PUT /wishlist/{token}`, `DELETE /wishlist/{token}` | The caller's own list, the newest first. Both changes are safe to repeat; the list holds 500 at most (`409 wishlist_full`); two entries of one book (e-book and recording) are one wish. A device may change it, a key only reads. |
| `GET /watching` | The authors the caller watches, the newest watch first: `items` (see `PUT` below, with `announced` empty and `next` the soonest book announced in the watch's language, its `date` the day or, when no day is known, the year) and `limit` (100). A key may read it. |
| `PUT /watching/authors/{token}` | Switches a watch of the author on or changes it (`token`: the token of the author's catalogue, `GET /catalog/authors/{token}`). Body: `kind` (`ebook`, `audio`, `both`), `language` (the one language books are wished in: one of the languages books are fetched in, `languages` of the answer; left out or empty: the first of them in the operator's order; any other: `422 language_not_accepted`; the language of the account plays no part), `more` (also non-fiction, children's books, collections, biographies and poems; by default novels and stories only). Books the sources could not place for sure are never wished. **The first look**, when the catalogue is built: the books *announced* in that language are wished at once (or put on the wish list without the right to acquire) and wait for their day; what is out already never is. A book announced for a year without a day is wished when its day is known (`state` `year` until then). A book announced only in another of the languages books are fetched in goes onto the wish list (`not_yet`) and is wished when it is announced in the watch's language. A book that may be no new book of its own (an adaptation, a study guide, an edition with additions, a play, a part, a box, a new edition, a new translation of a work out in the language already) goes onto the wish list instead of being wished; new is new in the watch's language (the first edition in it; how long ago the original came out plays no part), and a paperback or a new edition of a work out in that language is left alone; the whole title, the subtitle and the part number each source gave are read for it, and a doubt only the recordings carry (a play) holds the audio book alone. A book the caller has in another language (the original of the translation announced) goes onto the wish list with `why` `own:<code>`. A wish the watch made follows the day its book is announced for, while it waits (or found nothing after the old day). One wish per book and kind, whatever its language: a book the caller wished already in another language is not wished again. `state` is `waiting` while the catalogue is not built (the first look follows when it is). Answers the watch: `language`, `languages` (the languages books are fetched in for this author, first first), `target` (the language it wishes in), `language_off` (the language chosen is no longer fetched; it wishes in the first), `sources_few` (neither Audible nor Hardcover may be asked: few books to come are known), `paused` (the main switch of the automatic work is off: nothing is wished until it is on), `announced` (the books announced in its language with `state` `wished`, `listed`, `year`, `held`, `covered` (somebody else wished it for the library it would go into for the caller; wished for the caller when that wish goes) or `open` and `why`: `adaptation`, `companion`, `edition`, `play`, `part`, `box`, `reissue`, `translation`, `library`, `language:<code>`, `own:<code>`, or the code of a refused wish), `next`, `not_yet` (`title` in the language it is announced in, `language`, `announced_in`, `listed`), `wished` and `listed` (all that the watch made), `can_acquire`, and `made` (`wished`, `listed`, and `stopped`: why the look broke off, `no_indexers`, `share` (the part of the day's wishes watching may use), `list_full` or `many`; the look is made again later; `off`: the main switch of the automatic work is off, the watch is saved and nothing was wished, its first look comes when the switch is on). A wish refused for a reason that does not pass by itself is held with its code and not tried again at every look. `409 too_many_watches` past 100 authors. A device may switch a watch on and off, a key only reads. |
| `DELETE /watching/authors/{token}` | Switches it off (`204`, also when there was none). `?wishes=keep` (the default): the wishes and wish list entries it made stay; `?wishes=remove`: the wishes nothing has started on and the entries it put on the list go with it (a download that runs stays). The interface asks first. |

A watch is the caller's own (nobody else reads or changes it, the operator included). Its wishes are ordinary wishes: `GET /procure/wishes` names the author in `via` on the caller's own wishes a watch made (on the wishes of other people `via` is empty), and `GET /wishlist` names the author of an entry a watch made in `via` and why it is not wished in `via_why` (maps of the token of the book; `language:en` announced only in English, or the doubt). The page of the author (`GET /catalog/authors/{token}`, `GET /people/{id}/catalog`) carries the caller's watch as `watch`. After that, nexbooks looks again whenever the catalogue of a watched author is built again (at most once a day, and only while the sources have room), and wishes what was announced since and what came out since the last look. Two people who may ask for books in one library: the second gets no wish of their own while the first one's is there, and gets one at a later look when the first one's is taken away.

The catalogue search (`POST /search/catalog`) names `token` (the page of the book, which knows every title and form of it; null until the hit is opened with `POST /search/catalog/{ticket}/books`) and `owned_work_id` (the book on the caller's shelves, from the libraries the caller may see) for each hit, so that a hit can be opened or acquired.
The list of the libraries (`GET /libraries`) names `progress` (`done`, `total` files) for a library being read.

## Reading stack, authors and tidying

**The history is the person's own**: no route reads it for anybody else, the operator included. An entry names its edition;
its `work_id` is the edition's work as it is now (an edition put to another work, by hand or when the scan reads a recording
anew, takes its entries with it). An entry of an edition the caller no longer sees is left out and comes back with it.

| Route | |
|---|---|
| `GET /history` | The caller's finished books, the newest first (`work`: one book only; `limit`, `offset`; the header `X-Total-Count`). An entry: `id`, `work_id`, `edition_id`, `kind` (`read`, `listened`), `finished_on` (`YYYY-MM-DD` in the caller's time zone), `source` (`auto`: at 98 percent of an e-book or at the end of a recording, once a pass; `hand`), `created_at`. |
| `POST /editions/{id}/finished` | "Finished": today or `{"finished_on": "YYYY-MM-DD"}`. The same edition on the same day is the one entry (`200` then, `201` for a new one). |
| `DELETE /history/{id}` | "Not finished": takes one of the caller's own entries away. |
| `GET /stack` | The reading stack: `years` (oldest first) with `books`, `read`, `listened`, `pages`, `seconds`, `genre`, `thickest` and the `entries` (an entry with its `work`, `pages`, `seconds`), and `today`. |
| `GET /works?extras=list` | The list view: every book also carries `mine` (the caller's place in the e-book and the recording, apart, whether each is finished, `moved_at`; for a member with `manage` of a library the book lies in also `match`, the least sure matching state of its editions there, and `libraries`). |
| `GET /authors` | The authors of the books the caller sees, a page at a time: `sort` (`name`, `count`, `recent`), `genre`, `q` (a part of the name or of another name the person writes under), `page`, `per_page`. A card: `id`, `name`, `sort_name`, `works`, `audio`, `genre`, `added_at`, `photo_url`; `genres` counts the authors per genre. |
| `GET /people/{id}` | Also `aliases` and `pen_names` (other names the person writes under: proved by a source or the same ISBN or ASIN, or the same name written differently, such as the two words turned round, a first name in another language, a shorter name that fits this one name only; or joined by hand), `can_split`, `can_merge`, `photo_url`, `credit` (`source`, `author`, `license`, `license_url`, `page_url`) and `bio` (`texts` with `text`, `language`, `source`, `url`; `born`, `died`) in `language` (the caller's when left out). |
| `POST /people/{id}/split` | Body `alias_id` (an id of `pen_names`). Says that the other name is somebody else: the two are two people again, for good (nothing joins them by itself again; a join by hand is taken back). The operator, or whoever manages every library the books of the person lie in. The answer is the person. |
| `POST /people/{id}/merge` | A browser only. Body `other_id` (an author from `GET /authors`) and `keep` (`this` or `other`: the name that stays). The two are one author: the books of both are the books of the name that stays, the other name leads to it, works of one title are one work. Kept until somebody splits the name off again. The operator, or whoever manages every library of the books of both; `404` for an author the caller sees no book of, `422` for the same author twice (`same_person`), `409` when the library's evidence does not allow it (`cannot_join`). The answer is the author that stays. |
| `GET /people/{id}/photo` | The photo as WebP: from the author's folder of a library the caller is a member of, else from Wikimedia Commons, under a free licence and with its photographer. `404` alike for no photo and an author the caller does not see. |

**Tidying** (the operator only, a browser only, off until switched on) renames and moves the files of one library after a
scheme such as `{Author}/{Series}/{Volume} - {Title} ({Year})`. A preview lists every move and is exactly what the run does;
nothing is ever overwritten; a book moves whole (its formats, the parts of a recording with their `CD 1`, `Audio`, `Cover`
folders, the files beside it) or not at all; a recording is never sent into a folder that holds another book, two never into
one folder; the run can be undone. Problems are named per file (`exists`, `collision`, `folder_taken`, `part_folder`,
`shares_beside`, `link`, `too_long`, `no_title`, `book_blocked`, ...).

| Route | |
|---|---|
| `GET /organize/settings`, `PUT /organize/settings` | Whether tidying is on (`{"enabled": …}`). |
| `GET /organize/status?library=…` | `enabled`, `writable` and `reason` (`read_only`, `no_permission`, `missing`), the `scheme`, the `placeholders` and the latest `runs`. |
| `POST /organize/preview` | `{"library_id", "scheme"}`: makes the plan and keeps it as a preview (`422` with the reason for a scheme that cannot be used). |
| `GET /organize/runs/{id}`, `DELETE /organize/runs/{id}` | One run (`state`, `total`, `done`, `summary`); a preview can be thrown away. |
| `GET /organize/runs/{id}/moves` | The files of a run with `source`, `target`, `state` and `reason` (`state`, `offset`, `limit`). |
| `POST /organize/runs/{id}/start`, `POST /organize/runs/{id}/undo` | `{"confirm": true}`: starts the previewed run (`409 preview_stale` when the library changed since), or puts back what it moved. |

**Moving an edition into another library** (a browser; a member with `manage` in the library it lies in and in the one it
goes to, the operator included) takes every file of the edition (all formats of an e-book, all parts of a recording with
the files beside it) into the folder of the other library, named by the scheme of filing. **The edition stays the same**:
its id, and with it progress, bookmarks, history, corrections and the matching. Nothing is overwritten (a goal that is taken
gives another name, `Title (2)`); on the same disk a file gets a second name, else it is copied and its size compared, and
the source goes only after every file lies at its goal. A move that fails before that leaves the edition whole where it
lay; one that fails after it leaves the edition in the target and names the files still at the source. Either can be tried
again. A scan in between leaves the files of a move alone.

| Route | |
|---|---|
| `GET /editions/{id}/move` | `source` (with `writable`), `files`, `bytes`, the `targets` (the libraries the caller manages that take the kind: `writable` and `write_problem`, the `preview` of where the book files would lie, `more`, `folder`, `copies` when it takes a copy, `problem` when there is no place), `busy`, and the `latest` move. On the page of a book, `can_move` of an edition says whether to offer it. |
| `POST /editions/{id}/move` | `{"library_id", "confirm": true}`: puts the move in the queue (`202`, the move). `422 confirm_needed`, `move_same`, `move_kind`; `409 move_busy` (a move of the edition, or a tidying of either library, runs), `move_no_goal`, `move_source_not_writable`, `move_target_not_writable`. |
| `GET /moves/{id}` | How far it is: `state` (`queued`, `running`, `done`, `failed`), `step` (`place`, `clear`, `done`), `error` (`no_space`, `taken`, `target_taken`, `source_changed`, `source_left`, `no_write`, `failed` ...), `files`, `placed`, `removed`, `where` (`source`, `target`, `both`) and `left` (files at the other end). |
| `POST /moves/{id}/retry` | `{"confirm": true}`: a failed move goes on from its step. |

## Fetching books

The operator connects **indexers** (Newznab and Torznab), a **Prowlarr** and **download programs** (SABnzbd, NZBGet, qBittorrent,
Transmission, Deluge), chooses the formats and the places, and sets the automatic work. All of it is the operator's, in a signed-in
browser, and **the keys and passwords are sealed with the server's secret when they are saved and never read back**: an answer says
only whether one is set (`key_set`, `secret_set`). The third right of a library, `acquire` (between `read` and `manage`), is the
right to ask for books to be fetched in it: searching, loading, wishes and following the downloads are described below.

**The way out** to all of them is `services/procure/gate.py`, apart from the one to the sources: the address is the one the
operator typed (http or https, no user, no password, no query), only the host and the port of that address are asked, **private and loopback
addresses are allowed on purpose** (a homelab lives there), link-local space and the metadata services of cloud machines never,
a redirect is followed only to the same host and port, with http raised to https at most (twice at most), an answer is read up to 10 MB (an NZB file 20 MB, a torrent file
10 MB) within 30 seconds (90 behind Prowlarr), XML only with defusedxml, and the log names no key, no title and no path.

An **indexer** is asked at most once in two seconds, rests after a request limit until the time it names (`Retry-After`, five
minutes at least, an hour without one, a day at most; the rest doubles each time the limit is hit again straight after), and with a
limit of the day of the operator's own it is not asked after that many requests until midnight (every request takes its place in the
count before it is made, in one statement, so requests that run at the same moment never pass the limit). A refused key (401, 403)
stops the requests but one a day until the key is changed or the indexer is tested by hand; the planner and the reading of the newest
entries also leave an indexer alone for a time that doubles with every server error or missing answer (two minutes to two hours). Its book categories come from its caps (7000 series for books, 3030 and its neighbours for audiobooks, sub-categories
included), each with a language, and the ones that fit are chosen beforehand; `supported_params` keep what its searches support.

| Route | |
|---|---|
| `GET /procure/indexers` | Every indexer: `id`, `name`, `kind`, `url`, `key_set`, `enabled`, `priority` (1 to 50, the smallest is asked first), `daily_limit` (0: none), `state` (`ready`, `resting`, `key_rejected`, `off`), `used_today`, `via_prowlarr`, `categories` (`id`, `name`, `type`, `lang`, `on`, `suggested`, `asked`: false for a category of a language books are not fetched in, which a search never asks, `proposed`: the language the caps state), `proposals` (chosen categories whose language differs from `proposed`), `languages` (the languages books are fetched in), `resting_until` and `resting_cause` (`request_limit`, `api_limit`, `daily_limit`), `last_error`, `api_current`, `api_max`, `grab_current`, `grab_max`, `supported_params` (`search`, `book`, `audio`, `book_search`, `audio_search`). |
| `POST /procure/indexers` | `{"name", "kind", "url", "api_key", "priority", "daily_limit", "categories", "category_langs", "language"}`: the caps are asked (a key the indexer refuses is `502 indexer_key_rejected`, something that is no indexer `502 indexer_not_newznab`, nothing saved), the categories default to the suggestion: the book categories of the languages books are fetched in (settings, "Sprachen") and the mixed ones; `language` (the interface language) counts only when none is ticked. A category whose name or the name of the one it stands under carries a language (`Books - DE` > `Ebook`, `Audiobook - FR`) gets it. `201`. |
| `PATCH /procure/indexers/{id}` | Any of the fields. A new address, kind or key is asked first. An indexer from Prowlarr keeps name, kind, address and key (`409 indexer_from_prowlarr`). |
| `DELETE /procure/indexers/{id}` | `204`; `409 indexer_from_prowlarr` for one that came from Prowlarr. |
| `POST /procure/indexers/test` | `{"kind", "url", "api_key", "indexer_id", "language"}`: caps, then one request in the suggested categories (`t=search`, never `t=book`, which some indexers answer with a hundred books of any author). Nothing is saved. A key left out is the saved one of `indexer_id`. The answer marks each category `asked` and gives `languages`. The test of a saved indexer (`POST /procure/indexers/{id}/test`) keeps the caps it found and gives a chosen category saved as `mixed` the language its name carries (a language set by hand stays). |
| `POST /procure/indexers/{id}/test` | The same under the indexer's own rules (not while it rests or is off); what it finds is kept. |
| `POST /procure/indexers/{id}/proposals` | "Vorschläge übernehmen": every chosen category takes the language the caps state for it (`proposed`); only a language set apart from the caps is kept in the indexer at all. The indexer as `GET /procure/indexers` gives it, with `proposals` (how many chosen categories have another language than the caps state, here 0). The operator only. |
| `GET /procure/prowlarr`, `PUT /procure/prowlarr`, `DELETE /procure/prowlarr` | The connection (`connected`, `url`, `key_set`, `auto_add`, `version`, `last_sync_at`, `last_error`, `counts`, `taken`, `sync_state`). `PUT` `{"url", "api_key", "auto_add"}` tests first and queues a sync; a key left out stays. `DELETE` takes the indexers of Prowlarr with it. |
| `POST /procure/prowlarr/test`, `POST /procure/prowlarr/sync` | The test (`version`, `indexers`, `with_books`); a sync as a job of the queue (`202`), which also runs every 15 minutes. An indexer of Prowlarr with book categories becomes an indexer here; one Prowlarr no longer lists goes; a Prowlarr that cannot be read changes nothing. |
| `GET /procure/clients`, `POST /procure/clients`, `PATCH /procure/clients/{id}`, `DELETE /procure/clients/{id}` | The download programs: `kind`, `protocol` (`usenet`, `torrent`), `url`, `username`, `secret_set`, `category` (`nexbooks`), `priority`, `enabled`, `version`, `state` (`ready`, `unreachable`, `login`, `other`). Saving and every change of the connection tests first (version, login, the category made and read back, whether the program would move its files); a failure is `502` with `client_unreachable`, `client_auth_failed` (`reason`: `wrong_key`, `nzb_key`, `wrong_password`), `client_host_refused`, `client_address_refused`, `client_wrong_kind`, `client_category_failed`, `client_category_unusable`, `client_blocked` or `504 client_timeout`. |
| `POST /procure/clients/test`, `POST /procure/clients/{id}/test` | Version, whether the category existed, and the folder finished jobs go to (`folder`, empty when the program does not tell). |
| `GET /procure/path-mappings`, `DELETE /procure/path-mappings/{id}` | The mappings of paths the operator confirmed. |
| `GET /procure/formats`, `PUT /procure/formats` | Per kind a ranking of every known format with a tick (`ebook`: `epub`, `azw3`, `mobi`, `pdf`; `audio`: `flac`, `m4b_aac`, `mp3_320`, `mp3_192`, `mp3_128`, `mp3_64`; PDF and `mp3_64` off from the start), the `target` (a ticked format: nothing better is looked for), `languages`, `original`, `variant` (`unabridged`, `prefer`), `abridged`, `dramatized`, `size` (MB, from and to), `library` (per kind) and `schema` (the scheme of tidying). At least one format stays ticked; `422` names the part that is wrong. The answer also lists the `libraries` that can take the books, each with `writable` and `write_problem` (`missing`, `read_only`, `no_permission`). |
| `POST /procure/formats/preview` | `{"schema", "sample"}`: where a made-up book would be put (`ebook`, `audio`). |
| `POST /procure/formats/check-library` | `{"library_id"}`: a probe file is made, written and removed again in the folder; `writable`, `reason` (`missing`, `read_only`, `no_permission`, `write_failed`), `hardlink` and, for each download program that tells its folder, whether it lies on the same file system. |
| `GET /procure/automation`, `PUT /procure/automation` | The main switch `enabled` (on from the start), `rss_minutes` (5, 15, 30, 60), `retry_hours` (1, 3, 6, 12, 24), `replace`, `replace_max` (1 to 10) and `seed` (`ratio`, `7d`, `no`; `no` from the start: the torrent leaves the program once the book is filed). A new `retry_hours` moves the next try of every wish that waits to that long after its last search. |
| `GET /procure/automation/status?limit=` | What the automatic work did and when it comes back (the operator, a browser): `last_run_at`, `next_run_at` (null with the main switch off), `due` (wishes whose time has come), `waiting_for_a_day` (books not out yet), `wishes_open`, `indexers` (each: `checked_at` and `next_at` of the reading of the newest entries, `error`, `resting_until`), `day_ends_at`, `zone` (the name of the instance's time zone, empty for a fixed offset) and `runs`, the newest rounds first (`limit` 1 to 200, 30 by default): `kind` (`rss`, `search`, `handover`), `started_at`, `finished_at`, `error` and the numbers (`indexers` with `requests` and `entries` each, `wishes`, `matched`, `loaded`, `handovers`). Counts only: no title, no key, no address. |
| `GET /procure/targets` | For everybody signed in (a browser, a device, a key): the libraries the caller may ask for books in (`acquire` or `manage`), each with `writable` and `write_problem` (`missing`, `read_only`, `no_permission`; empty when nexbooks may write there: a library mounted read-only takes no book). |

### The automatic work

Every 15 seconds one tick of the planner runs, one at a time, and reads the database only (a restart loses nothing). With the main
switch off nothing runs by itself; "search now" is served as ever.

* **Wishes come back** after `retry_hours` (one wish per tick, the one that waited longest); a wish that has found nothing for a week
  is looked for once a day at most, after a month once a week. A wish for a book that is not out
  waits for its day (`release_date`, the day of the instance's time zone, `TZ` in a container) and is searched on that day.
  A wish held back by an indexer's limit (`capped`) is due again when the limit is over.
* **The newest entries** of every indexer are read every `rss_minutes` (one request of the book categories the operator ticked;
  a second and third page only while a page holds nothing but entries not seen before). An entry is judged once (two weeks).
  An entry that fits a wish that waits, by the rules of a search, is loaded: the best one, and the next one of another indexer
  when an indexer cannot give the file. An indexer with a daily limit gives at most half of it to this reading, and nothing is read
  while no wish waits for a release.
* **The day of an indexer** starts at midnight of the instance's time zone. Searches and the reading of the newest entries count
  against the operator's `daily_limit`; the files of releases fetched are counted apart (`grabs_today`) and held back only by what
  the indexer says about its own grabs (the number it names a day is also the most nexbooks fetches from it). An indexer at its limit
  keeps its wishes waiting (`capped`, `detail.until`), the others go on. What the automatic work does by itself (wishes that come back,
  the newest entries, a better format) uses at most 70 percent of a limit; the rest is for a new wish and for "search now".
* **After a failure** the next release of the search the failed one came from is taken at once (no request), else a new search.
  A wish has at most `replace_max` downloads in 24 hours, whatever started them; then it waits until the first of them is a day old
  (`replace_limit`, `next_try_at`).
* **A better format** is looked for, in the same time, for a wish whose book has the kind below the goal of the ranking; it goes on
  after a step that does not reach the goal and ends when the goal is reached or lowered. Never a format that is not ticked.
* **A program that did not answer** gets the release again after 3 minutes, then 6, 12, 24 and 48 (up to five more times,
  `handing_over_again`), without a new search; the program is asked before the indexer, and the file once fetched is kept in the data
  folder (at most two days, 100 MB) until a program has it, so a program that is away costs the indexer no release. A book is never
  loaded twice. With the main switch off none of this runs.

### Searching and loading releases

A person with the right `acquire` (or `manage`) in a library that takes the kind of book searches the indexers for one book: a
work of a library they see, or a book of the sources (the token of its page). The operator has no right of his own here. A search is
a job of a queue of its own that survives a restart; it asks every enabled indexer at once (each paced as above), in the categories
the operator chose of the kinds looked for and in the language looked for (or of mixed language), with `t=book` only where the caps
list `author` and only answers whose name holds the author, else a free text "Author Core title" in the spellings of an umlaut (`ö`,
`oe`, `o`; also without any accent), at most four queries (every title in its first spelling before any title in its second) and
two pages of 100 per indexer, within 150 seconds. `t=book` counts as an answer only when a release in it names the author and the
core of the title; otherwise the free text asks for the e-books too. Each release is then judged: kind, format
in the words of the ranking (said by the name, or for an audiobook worked out from its size and the running time), language (a mark
or words of a language in the name are sure, unless the word belongs to the title looked for; a title known in one language only is a
reason; the language of a category and the language looked for are only presumed, and a presumed language is never a reason), and
the reasons against it as codes: `blocklisted`, `not_a_book`, `sample`,
`soundtrack`, `companion`, `adaptation` (a graphic novel, a retelling for children, a simplified text), `partial` (`Teil 2 von 2`,
`CD 7-12` of a book that is no volume of a series), `bundle`, `author_mismatch`, `title_mismatch`, `other_volume`, `other_language`,
`abridged`, `dramatized`, `format_not_wanted`, `too_small`, `too_large`, `not_enough_seeders`, `older_than_retention`, `not_better`
(an upgrade must be better than the format there is of the same version of the book or a fuller one, and nothing is looked for from
the goal on; an unabridged reading always replaces an abridged one or a dramatisation). **Doubts** are reasons that turn nothing down
and load nothing by themselves: one thing speaks for the release and another against it (`language_unsure`, `bilingual`, `edition`:
a school edition or the book of the film, `collection`, `maybe_partial`: `Teil 1` without the number of parts, `title_short`,
`maybe_other_volume`: a number after the title that is not the volume wished, `maybe_other_book`: a field nothing explains, or an
author or a book of the author that the indexer states with `extended=1` beside a name that fits, `author_missing`, `size_odd`). A
person loads such a release after a look; a wish whose releases have doubts only says so (`detail.look`). Words of a language
inside the title, the author or the series are no sign of a language. Those that pass are ranked: the reading
first for an audiobook (unabridged, a dramatisation, abridged; an abridged one passes only when no unabridged one does), then the
place of the format, a sure language before a presumed one, the priority of the indexer, seeders or age, the size.

| Route | |
|---|---|
| `GET /procure/works/{work_id}?language=` | What the caller may look for of a work: `allowed`, and per kind (`kinds`) the `library` it would go into, the best format there is in that language (`have`), the `goal` and whether an `upgrade` is possible. `404` for a work the caller does not see. |
| `POST /procure/searches` | `{"work_id"}` or `{"book"}` (a token), `language` (a code the operator allows under Settings, Acquisition, Languages, else `422 language_not_accepted`; left out, every language he allows, with the book's original language when "Original language" is ticked; a language the indexer categories do not know means the same, `language` is then empty and `languages` lists them; the account's language plays no part) and `new`: `202` with the search, every indexer `waiting`. A second request for the same book and language while one waits or runs gets that one, and so does one within fifteen minutes of a search that finished (same kinds), unless `new` is true. When no indexer can be asked right now (all rest, or have no category for the book) or a person has made the searches of the day, and the person has an earlier finished search for the same book, language and kinds with releases, that search is returned instead of a new one (a request with `new` is not: it starts a search that ends at the limit, or is refused). At most three of a person wait or run (`429 too_many_searches`), and a person (not the operator) starts at most 60 a day (`NEXBOOKS_SEARCHES_PER_DAY`; a search shown again does not count): beyond it `429 too_many_searches_today` with `Retry-After`. An indexer with a limit of the day gives each person a fair share of it: the indexer's line says `indexer_share_used` with `until` when a person's share is used. `403 acquire_not_allowed` without the right, `409 no_indexers`, `404` for a book the caller does not see. Finished searches go after a day, except one that a wish which is not over still points at; a person keeps 20. |
| `GET /procure/searches/{id}` | The caller's own search (`404` otherwise): `state` (`queued`, `running`, `done`, `failed`; a search whose job was given up or lost is `failed` with `error` `search_failed`), `kinds`, `languages`, `title`, `author`, `indexers` (per indexer `state`: `waiting`, `searching`, `done`, `failed`, `timeout`, `skipped`, with `found`, `error`, `until` for one that rests and `limit`, the operator's limit of the day, where it or a person's share of it stopped the indexer), `resting` (read live: the indexers that cannot be asked now, each `id`, `name`, `cause`, `until`, `limit`), `can_search_again` (an indexer can be asked and the person has searches left today), `searches_used_until` (the moment a person who has made the searches of the day may search again; null otherwise), and once done `cards` (per kind `total`, `passing`, the three most frequent `reasons`, the `top` release, the `goal`, `below_goal`, `have`) and `releases`, those that pass first by `rank`: `title`, `indexer`, `protocol`, `kind`, `format`, `format_how` (`name`, `estimate`, `unknown`), `tier`, `language`, `language_sure`, `variant`, `bitrate`, `size`, `age_days`, `seeders`, `peers`, `grabs`, `score`, `rank`, `reasons` (`[{code, arg}]`), `blocked_by` (the blocklist entry). No link and no key is ever shown. |
| `POST /procure/searches/{id}/releases/{release_id}/load` | `{"force"}`: the file is fetched from the link the indexer gave, on whatever host (as the *arr programs do); behind an indexer on the internet a link into a private network or to a blocked address is `502 release_link_private`, a link that is no http or https link `502 release_file_invalid`; an NZB must be one, a torrent bencoded: `502 release_file_invalid`) and handed to the first enabled program of its protocol by priority, into nexbooks's category, the seed goal of the tracker with a torrent; `201` with the download (`state` `queued`, or `unsure` when the program did not answer in time). A release that was turned down needs `force` (`409 release_refused` with its `reasons`); a second request while the first is on its way, and any other release of the same book and kind while one is, is `409 already_loading` (one release at a time for a book and a kind, whoever asks); `409 no_client`, `409 search_not_done`, `409 indexer_gone`, `409 target_not_writable` (`library_id`, `reason`: nexbooks may not write into the library the release would go into; nothing is fetched). |
| `POST /procure/searches/{id}/releases/{release_id}/block` | `{"reason"}` (`by_hand` or a code of the judgement): the release goes on the blocklist of its book; every later search of that book turns it down, also under a work that is built again (the entry keeps the author and the core of the title). `201` with the entry. |
| `GET /procure/blocklist` | The entries of the libraries the caller may ask for books in (and of works they see), newest first: `title`, `kind`, `protocol`, `indexer`, `reason`, `by` (who blocked it; empty when nexbooks did), `work_id` or `book`, `book_title`, `book_author`. A release nexbooks turned down for its language (`other_language`) is blocked only for the wishes and searches that do not take that language. |
| `DELETE /procure/blocklist/{id}` | Takes an entry off again (`204`); `404` for one the caller does not see (the library it was blocked for decides). |

### Wishes, downloads and what needs somebody

A wish is one book in one kind and one language for a library the caller may ask for books in; there is one open wish per
book, kind, language and library, whoever asks (two libraries may each wish the same book and neither sees the other's wish).
Everything here needs the right `acquire` (or `manage`) in the library (`403 acquire_not_allowed` without it, also for the
lists); a wish, a download and a line of the history are seen by everybody who has it there, a work only when the caller
sees it. A key reads, a device token also changes. The jobs nobody asked for and the path mappings are the operator's, in a
browser.

| Route | What it does |
|---|---|
| `GET /procure/counts` | For the head of the page: `allowed` (the caller may ask for books anywhere; false answers zeros), `seeking`, `running`, `needs`, `blocklist`, `automatic` (the main switch), `indexers` and `clients_down` (programs that could not be reached the last time). |
| `GET /procure/book-options?work_id=` or `?book=` | Per kind (`ebook`, `audio`) the state for the caller (`free`, `have`, `upgrade`, `wished`, `loading`), the format there is and the goal, the libraries it may go into (each with `writable` and `write_problem`; `default_library` is the operator's choice, else the first library nexbooks may write in; with `choose` several libraries nexbooks may write in take the kind and the operator chose none: `default_library` is empty and a wish names one), plus `original_language`, the `languages` the book may be fetched in (the ones the operator ticked, and the book's original language when "Original language" is ticked) and `release_date` while the book is not out. |
| `POST /procure/wishes` | `{"items": [{"work_id"} or {"book"}], "kinds": ["ebook", "audio"], "language", "library_id"}` for up to 100 books: `201` with a result for each book and kind (`created`, `exists` because somebody wished it already in the same library, `have`, or `error` with a `code`) when at least one wish was made, `200` when none was; when every book failed, the failure of the first book (for example `404 not_found`). A new wish is searched at once and, with the main switch on, the best release that passes is loaded. A person (not the operator) makes at most 100 wishes a day (`NEXBOOKS_WISHES_PER_DAY`): beyond it `429 too_many_wishes_today` with `Retry-After`. `403 acquire_not_allowed`, `403 library_not_allowed`, `409 no_indexers`, `422 language_not_accepted` for a language the operator does not allow (left out: every language he allows), `422 library_required` when several libraries nexbooks may write in take a kind, the operator chose none and the request names none, `422 not_a_book` or `422 volume_unknown` for the page of a hit of the catalogue search that cannot be acquired. |
| `GET /procure/wishes?mine=` | What is wished: `kind`, `language`, `state` (`searching`, `idle`, `not_found`, `waiting`, `capped`, `upgrade`, `loading`), `detail` (the reasons of a search that found nothing and `look`, how many releases only doubts stand against, the limit that holds an indexer with `indexer`, `until` and `code`, the format and the goal of an upgrade, and `have_language` and `goal_language` when a wish for every language allowed has its book in a lower one), `searched_at`, `next_try_at`, `release_date`, `handing_over_again` (a release was found and the download program did not answer), `library`, `by`, `mine`, `book`. Only reads: a wish that waits for a place of its person is started by the planner. |
| `DELETE /procure/wishes/{id}` | Takes a wish away (`204`); a download it has goes on. |
| `POST /procure/wishes/{id}/search` | Searches now, also with the main switch off. `409 wish_loading`, `409 wish_waiting`, `422 language_not_accepted` (its language is no longer allowed), `429 too_many_searches`, `429 too_many_searches_today` (it counts as a search by hand). |
| `POST /procure/wishes/{id}/language` | `{"language"}` (a code the operator allows, or `null` for every language he allows): a wish that rests because its language was taken off the list (`state` `idle`, `detail.reason` `language_off`) searches again. When a wish of the book in that language is open already, that one is the answer and this one goes. `409 wish_loading`, `422 language_not_accepted`. |
| `GET /procure/running` | The downloads that run: `phase` (`sending`, `unsure`, `queued`, `downloading`, `paused`, `unpacking`, `waiting` to be filed, `checking` while its files are read and held against the wish, `filing`, `seeding`), `protocol` (`usenet`, `torrent`, `upload`), `progress`, `remaining_seconds`, `speed` (bytes per second, worked out from what the program says is left; null while it does not load), `stalled`, the seed ratio and goal, `client`, `indexer`, `library`, `by`. Tracking asks the programs every 15 seconds. |
| `GET /procure/problems` | `problems`: downloads that need somebody, `code` (`path_not_found`, `no_write`, `client_error`, `no_space`, `gone_from_client`, `place_failed`; `paused`: the program has held the job for hours, `values.why` lists the reasons it gives (SABnzbd's labels such as `DUPLICATE`, `ENCRYPTED`, `TOO LARGE`, `FILTERED`, `ALTERNATIVE`, `PROPAGATING` as it writes them, and `queue_paused` when its whole queue stands; empty when it names none), answered with `POST /procure/downloads/{id}/cancel` (`block` `true` to block the release too, which takes the job out of the program and searches on); the questions of the check after the download `runtime` (`loaded`, the unabridged and abridged times of the catalogue `unabridged` and `abridged`, `reason`, all in seconds; where no catalogue names an unabridged time, `pages` and `estimate`: a rough time at 1.7 minutes a page from the pages of the book, a guide that decides nothing), `bundle`, `files_unassigned`, `language_unclear` (the package of an e-book names another language than its text or the name of the release, or the text is in two languages (`text` `mixed`) or in one nexbooks has no words for (`other`); `values` `file`, `text`, `name`, `wanted`; nothing is blocked, `take` files it), `edition` and `partial` (the title of the files names a special edition or a part; `title`, `authors`), `pen_name` (the release named the author wished, the files another one with the title wished; `found`, `wanted`), each answered with `take` or `block`; `same_edition` (a better format of an e-book the library has, and nothing proves it the same edition; `have`, `found`), answered with `take` (it replaces the old one), `keep` (both stay) or `block`; and `language` for a release the check turned down, until it is read) with `values` (paths only for the operator; a bundle names its books as `units` with a short `unit` to answer with) and `can_map`; `foreign`: jobs in nexbooks's category nobody asked for (for the operator only, else empty); `notes`: the caller's own notes of the filing (`upgrade_position`: the place in an e-book was carried over to a new format by its share; `upgrade_replaced`: a better format replaced the old one, which lies in the trash; `folder_kept` with `client`: the program put the job into a folder it shares with others, and nexbooks took nothing away there; `works_joined` with `title` and `into`: the scan put an edition that was a book of its own into another book, `POST /editions/{id}/split` is the way back; `kindle_failed` with `reason` (`mail`, `no_address`, `off`, `not_sendable`, `unreadable`): a mail with this e-book to the person's Kindle did not go for good, `POST /procure/notes/{id}/acknowledge` clears it). |
| `GET /procure/history?result=` | What became of the downloads (`done`, `rejected`, `failed`, `removed`), and `joined` for an edition the scan put into another book (no download; `note.edition_id`, `note.title`, `can_split`); `done` or `bad` narrows it. |
| `POST /procure/downloads/{id}/cancel` | `{"block"}`: the job leaves the program with its files; with `block` the release is never taken for the book again and the wish searches on. `409 not_cancellable`. |
| `POST /procure/downloads/{id}/recheck`, `/library`, `/stop-seeding` | Look again; put a finished download into another library (`{"library_id"}`); end the seeding of a filed torrent. |
| `GET` and `POST /procure/downloads/{id}/mapping` | The operator only, for a download of a library he manages (`404` otherwise): what the search for the folder of a download found (`search`: `proposed`, `twice`, `cut`, `nowhere`, `no_path`) and confirming it. Only what nexbooks suggested is accepted (`409 mapping_not_offered`). |
| `POST /procure/foreign/{id}/ignore`, `DELETE /procure/foreign/{id}` | The operator only: ignore a job nobody asked for, or take it out of its program with its files. |
| `POST /procure/foreign/{id}/assign` | The operator only, with `acquire` in the wish's library: `{"wish_id"}` makes the job the download of an open wish (`201`); it is followed and filed like any other. `409 wish_loading`, `409 already_loading`. |
| `POST /procure/downloads/{id}/decide` | An answer to the check after the download: `{"action": "keep"}` (a recording goes in as the version its running time says), `{"action": "pick", "unit"}` (one book of a bundle), `{"action": "whole"}` (the recordings of a bundle are one book, where the question says `whole`), `{"action": "take"}` (files it did not recognise, as the book wished), `{"action": "block"}` (the release is blocked, its files leave the program, the wish searches on), `{"action": "acknowledge"}` (the note of a release turned down for its language is read). `409 not_a_question`, `422 invalid_input`. |
| `GET /procure/downloads/{id}/look` | What the files of a download that asks a question say about themselves, for the people who may ask for books in the library the book goes into: the files (name and folder inside the download, format, size, the order of the parts, `protected`), what is written in them (`says`: title, authors, narrators, series, ISBN, publisher, year, `language`, `text_language`, running time, chapters, pages) and the `chapters` of a recording. `404` for anybody else, `409 not_a_question` when the download asks nothing (its files are filed or gone). |
| `GET /procure/downloads/{id}/files/{file}/content` | The bytes of one file of that download, for the reader and the player: `Range`, `ETag` and `If-None-Match` as `GET /files/{id}/content`. The place comes from the journal of the filing and is checked against the folder of the download again (a link, a file that is gone or one of another download is `404`). |
| `POST /procure/notes/{id}/drop` | Of an edition with two files of one format, `{"folder": …}` (one of the two the note names) goes to the trash, and the note with it (`204`; a member with manage). |
| `POST /procure/notes/{id}/acknowledge`, `/restore` | The caller's own notes: read one (`204`), or bring the old format back from the trash and put the new one into it, with the caller's place as it was (needs `acquire` in the library). `409 trash_gone`, `409 trash_taken`. |
| `GET /procure/trash` | What upgrades replaced, in the libraries the caller manages: `entries` (`title`, `author`, `format`, `files`, `size`, `expires_at`, `library`), `days`, `size`. |
| `POST /procure/trash/{id}/restore`, `DELETE /procure/trash/{id}`, `DELETE /procure/trash` | Put an entry back where its files lay (`409 trash_taken` when something lies there now), delete one, or empty the trash of the libraries the caller manages (`{"deleted"}`). Deleting is for a browser only (`403 browser_only` for a device token or a key). A trash folder that is a link is left alone: `409 hidden_folder_link` (emptying skips it). |

### Checking and filing

A finished download is filed by a job of its own (it survives a restart and never runs at the same time as a scan or a
tidying): every book file is read by the readers of the scan (the EPUB's `dc:language`, title, author, ISBN, series; a
recording's tags and running time, its parts as one), held against the wish (the author as a set, never by the last name
alone; the core of the title; the volume; the language from the file; the kind; the format by the ranking; an upgrade
must be better; a recording's version by its running time against the catalogue, where one of under a tenth of the unabridged time is a piece of the book and turned down (`runtime_part`, blocked as `partial`) and not asked about; a sample is turned down, a book
about the book is asked about), and placed by the scheme of the
formats: a torrent is linked, a Usenet download and an upload are moved, and where that is not possible copied under a
hidden partial name first. Nothing is ever written over (a name that is taken gets ` (2)`). Archives (ZIP, TAR) are
unpacked in a process of its own with limits; RAR and 7z are not, and a release of them is turned down. A dangerous file
(a program, a script) turns the whole release down. What is turned down is blocked for the book, its files never go into a
library, and the wish searches on; what is unclear waits under "Braucht dich", and when the rules of the check have changed since the question was asked, the automatic work (main switch on) judges it again, a few at a time, from the files it has and by the same check as a fresh download: the book is filed, the release is turned down, or the question is asked again with today's numbers. A download whose files are gone, an upload, and a recording whose version a person set keep their card. An upgrade puts the old format into the
trash of its library (a hidden folder, `trash_days` of the formats, 30 from the start) and **betters only the same
version**: the same variant (unabridged, abridged, dramatized), the same language and, where both are known, the same
reader and a running time within 5 percent. In doubt the new recording stands beside the old one. A filing that stopped
between making the new name and taking the old one away goes on when it is looked at again (the file at the goal with the
same size and content counts as placed); a goal that was taken meanwhile is planned anew. Room is checked for the whole
release before the first file moves. A download a program reports inside a library is no release.

### Uploading books

An upload is filed the same way as a download. It needs the right `acquire` in the library and belongs to the person who
began it until it is filed (`404` for anybody else, the operator included).

| Route | What it does |
|---|---|
| `POST /uploads` | `{"library_id", "work_id", "kind"}` (`work_id` and `kind` optional: the form a book lacks; without `kind` the files tell it, and for a ZIP the filing, once it has unpacked it): `201` with the upload. `403`, `404`, `409 no_write` (with `reason`), `409 hidden_folder_link` (the hidden folder for uploads in the library is a link), `422 library_kind`, `429 too_many_uploads` (three open per person). |
| `PUT /uploads/{id}/files?path=` | The body is one file (`application/octet-stream`); `path` may hold folders (`CD 1/01.mp3`), never `..`, an absolute path, a drive or a hidden name. EPUB, PDF, AZW3, MOBI, M4B, MP3, M4A, FLAC, Ogg, Opus, a cover, `metadata.json`, `metadata.opf` and ZIP; 500 MB for an e-book, 4 GB for anything else, 20 GB and 1000 files for one upload. A long name keeps its ending (the stem is shortened). The right `acquire` is looked at again with every file (`403`). `415 format_not_accepted`, `413 too_large`, `413 too_many_files`, `409 file_exists` (also for two files of one name sent at once), `409 upload_closed`. |
| `POST /uploads/{id}/finish` | `202`: the upload is filed (`403`, `422 upload_empty`, `422 upload_mixed` for e-books and recordings together, `409 already_loading`). |
| `GET /uploads/{id}` | `phase` (`uploading`, `waiting`, `filing`, `problem`, `rejected`, `done`), `problem`, `files`, `work_id` once filed, `limits`. |
| `DELETE /uploads/{id}` | Drops an upload that is still coming, with its files (`204`). |

## The surface of the iPhone app: `/api/app/1`

The app speaks to its own surface, not to `/api/v1`: the app in the store and the servers people run are never updated at
the same moment. As long as `app/1` stands in the address, nothing here breaks (fields may come, none goes); if something
must, `/api/app/2` is made beside it and older apps keep working. `backend/tests/test_app_promise.py` and
`backend/tests/app1_fields.json` hold the addresses and the fields of every answer fast. The description is part of
`/api/v1/openapi.json`.

### Fetching the first token

Two addresses (and, below, three more for a device without a camera) need no token: this is how the app gets one. They are for programs: a request with an
`Origin` header is `403 origin_refused`, and it counts for nothing. Neither wants the header `X-Nexbooks-Client`. A body may
hold 4 KB at most (`413 too_large`): nobody has signed in there.

| Route | What |
|---|---|
| `POST /api/app/1/pairing/{id}/claim` | Body `{"secret", "device_name"}`: the secret of the QR code. |
| `POST /api/app/1/pairing/claim` | Body `{"code", "device_name"}`: the code a person typed (any case, with or without the dash). |

Both answer `200` with `{"token", "account": {"name"}, "server": {"version"}}`: the token (`nbd_…`, send it as
`Authorization: Bearer`) is in this answer and in no other, and the answer is never cached. `device_name` is how the
device stands in the list (empty means `iPhone`; 1 to 80 characters, no control characters, else `422
device_name_invalid`, and the code stays open, whatever the length: nobody gets a plain `invalid_input` for a name). A
device is only made when the account holds fewer than 30 (`409 too_many_devices`, the code stays open).

| Answer | Code | Meaning |
|---|---|---|
| 403 | `origin_refused` | The request has an `Origin` header: a web page's. The app sends none. It counts as nothing. |
| 404 | `pairing_unknown` | No such code, **or a wrong secret**: the same answer, so nobody can search for ids. Counts against the sender (five, then a quarter of an hour of rest, `429 too_many_attempts`). Also: the account is blocked or locked. |
| 409 | `too_many_devices` | The account holds 30 devices; the code stays open. |
| 410 | `pairing_expired` / `pairing_used` | Told only to whoever shows the right secret or code. These do not count against the sender. |
| 422 | `pairing_code` | Not a code at all (not eight letters and digits of the alphabet without `0 O 1 I L`). It names nothing, so it is no guess and costs no attempt. |
| 422 | `device_name_invalid` | The name cannot be kept (empty after the spaces are gathered, over 80 characters, control or invisible characters). The code stays open. |
| 422 | `invalid_input` | A field is missing or of the wrong kind (no secret, no code, a name that is no text). |
| 429 | `too_many_attempts` | Five wrong secrets or codes from this address (`Retry-After`). **A count of its own:** it does not touch the sign-in or the device tokens of the address, and a secret that is right is not turned away by it (it cannot be guessed), so a person who mistyped the code can still scan the QR code. |
| 429 | `too_many_codes` | Only by code: 30 wrong codes within ten minutes were tried on this server by anybody, from all addresses together (`Retry-After`, `retry_after` in seconds). Fetching by code rests until the window has room; the QR code's secret goes on. The app says "scan the QR code instead, or wait". |

The secret and the code are compared as hashes in constant time and kept only as hashes. The code has about forty bits
(31^8): the two counts are what keeps it from being tried out within the ten minutes it lives. Five wrong ones per address
rest that address; over all addresses together 30 in ten minutes rest the fetching by code. A person who types by hand
makes a few mistakes at most.

### A device without a camera asks for a code

Three more routes that need no token, for the Apple TV app (and any other device that has no camera): the device asks for a
code of its own, shows it, and asks how it stands until a person has confirmed it in the browser. The same rules as above: no
`Origin`, no tab header, a body of 4 KB at most. The server names the feature `pairing_request` in `GET /me`.

| Route | What |
|---|---|
| `POST /api/app/1/pairing/request` | Body `{"device_name"}` (empty or missing means `Apple TV`; 1 to 80 characters, no control characters, else `422 device_name_invalid`). Answers `201` with `{"id", "secret", "code", "expires_at", "poll_seconds"}`: show `code` (`ABCD-2345`) large, keep `secret` (only the device knows it) and ask every `poll_seconds` (3). It lives ten minutes. |
| `GET /api/app/1/pairing/request/{id}` | Header `X-Pairing-Secret`. `{"state": "waiting"}` until a person confirmed the code; then **once** `{"state": "confirmed", "token", "account": {"name"}, "server": {"version"}}` (never cached); `{"state": "expired"}` when it ran out, when the confirmation was taken back, when the account was signed out everywhere or is blocked. After the token was handed over: `410 pairing_used`. |
| `DELETE /api/app/1/pairing/request/{id}` | Header `X-Pairing-Secret`. The device lets its request go (its screen was left): it runs out at once and gives its place back. **Always `204`**, also for a wrong secret or an id the server forgot (then nothing happens), and it never counts against the sender. Right secret: again and again, whatever the state; a request that was fetched is left as it is. |

Errors: `403 origin_refused`; `404 pairing_unknown` (when asking how it stands: no such request **or a wrong secret**, the same
answer; counts against the sender: five, then a quarter of an hour of rest, `429 too_many_attempts` with `Retry-After`; it is a
count of the television's own, apart from the typing of codes in the browser and from the fetching with a code of the iPhone
app, so the mistakes of a television do not keep a person from typing; a right secret is not turned away by the rest); `409 too_many_devices` (at the fetch, the request stays confirmed);
`410 pairing_used` (only to the right secret); `422 invalid_input` (header missing); `429 too_many_requests` (asking: the
server holds as many codes that wait for a confirmation as may be, `Retry-After`).

**How many may wait.** Asking needs no sign-in, so the number of codes that wait for a confirmation is limited: 10 per sender
(a household has a few screens, and a screen that shows a new code each time it is opened leaves one behind) and 300 on the
whole server (300 open codes against 30 wrong codes in ten minutes is a chance below 3e-8 per window to hit one). **A sender
that asks for an eleventh code does not meet a wall: its oldest open code runs out** (that television hears `expired`) and the
new one takes its place, so a television that starts again never locks itself out. Only the 300 over all senders say `429`.
A confirmed, expired or let-go request does not count. **The sender is the address the connection came from.**
`X-Forwarded-For` is believed only from a proxy named in `NEXBOOKS_TRUSTED_PROXIES`; anybody else can write that header, and
a different value every time would buy a place for each. Behind a proxy that is not named, everybody behind it counts as one
sender (the log says so once, and "Ready for the internet?" warns). The sender is kept as a salted hash, only to count, and
goes with the request.

### The iPhone confirms the television

A person who has paired the iPhone app can connect the Apple TV app without typing an address and without a browser. The
television shows a code only for the iPhone app (a QR code that carries a key for the home network, nothing for nexbooks): the
app tells the television the address of nexbooks over the home network, the television asks for a code of its own as above
(`POST /pairing/request`) and hands `id` and `code` to the app, and the app confirms with **its own device token**. The
television fetches a token of its own, as above, with the secret that only it knows: the token is made at that moment, once,
and the television stands in the list of devices by itself, to be revoked alone. The server names the feature
`pairing_request_confirm` in `GET /me`; an older server has no such route (`404`), and the app sends the person to the browser.

**A device token makes a device here**, so the television is connected **through** the phone that confirmed: the list of
devices says "Connected through <phone>", **revoking the phone revokes the television too** (and a confirmation it gave that
the television has not fetched yet: the television hears `expired`, and fetches nothing while the phone is revoked), and a
device connected this way confirms nothing itself: `403 device_chain` at both routes below, before anything is counted (it
is connected in the browser instead). `GET /me` says so beforehand: `device: {"name", "may_confirm"}`. "Sign out everywhere",
a new password, an account that is blocked or signed out by the operator end the phone and the television alike. So a lost
phone may have brought a television along, but blocking the phone in the list ends both; nothing it brought outlives it.

| Route | What |
|---|---|
| `POST /api/app/1/pairing/request/preview` | Before confirming (feature `pairing_request_preview`): the same token, body and answers as the confirmation, and the same counts for a wrong pair, but **nothing is decided**. Answers `200` with `{"state": "waiting", "device_name", "requested_at", "expires_at"}`, never cached: the name as the server keeps it (and as it will stand in the list of devices). The app shows it next to what the television says over the home network and asks the person only then. |
| `POST /api/app/1/pairing/request/confirm` | A device token (no cookie, no API key). Body `{"id", "code"}` (the code in any case, with or without the dash). Answers `200` with `{"state": "confirmed", "device_name", "expires_at"}`, never cached. It is the same decision as typing the code in the browser (one service): the request is tied to the account of the token. |
| `DELETE /api/app/1/pairing/request/{id}/confirm` | The device token that confirmed takes it back as long as the television has not fetched (`204`, again and again, never cached): the request runs out at once, the television hears `expired`. A request this device did not confirm is `404 pairing_unknown` (not counted); one the television fetched is `410 pairing_used` (then it stands in the list and is removed there). |

**Both must fit.** `id` and `code` of the same request, else `404 pairing_unknown`: one answer, to the word, for an id nobody made,
a code that does not fit, and a request that another person confirmed already. Other answers: `410 pairing_expired` (it ran out
or the television let it go; it shows a new one), `410 pairing_used` (confirmed or fetched already, **also by the same account**:
the app sends once, and after a lost answer it asks the television), `403 second_factor_setup_required` (the account has to set up
the second factor in a browser first), `403 device_chain` (this device was itself connected through another one), `409
too_many_devices` (the account holds 30; the request stays as it was, and a request that is used or ran out says so first), `422
pairing_code` (the text is not a code at all; it names nothing, so it is no guess and costs nothing), `422 invalid_input`, `401`
as everywhere on this surface, `429` as below. A body may hold 4 KB at most (`413 too_large`).

**The brake** is the one of the codes typed in the browser: a wrong pair counts against the sender and against the account
(five, then a quarter of an hour of rest, `429 too_many_attempts` with `Retry-After`, the same count as *Connect a device with a code* in the browser), and over all senders only 30 wrong codes pass in ten minutes (`429 too_many_codes`, the same count as
the browser's, not the one of the QR code of the iPhone). **A right pair is not turned away by any of these**, as the right secret
of a television is not turned away when it asks how it stands: an id of 128 bits cannot be tried. One more count protects the
code (40 bits) from whoever happens to see an id (it stands in the address the television asks at): **five wrong codes against
one request rest that very request**, also for the right code (`429 too_many_attempts`); another request is not touched. That
count goes on also while the sender, the account or the whole server rest: whoever rests his own counts first still spends
the request's.

**What the app keeps to.** The counts are shared with the browser: five wrong pairs of the app rest the typing of codes in the
browser of the same person and from the same address, and the connecting with a code of the QR code (`/pairing/claim`) from the
same address, for a quarter of an hour (and the other way round). Whoever loses a race (another person confirmed the request a
moment before) hears `404 pairing_unknown`, which counts as a wrong pair; a request the server cleared away (twenty minutes
after it ran out) is `404` too. So the app sends a pair **once**, only for a request it just got from the television, and after
`404` or `410` it asks the television for a new code instead of trying again. Confirm only while the television is in front
of the person who sees the question.

The log says "A device of <account> was confirmed from the app <name of the phone>: <name of the television>" (names cleaned),
then, when the television fetches, the line of the other ways ("... was paired by code: ..."). Never a token, a secret, a code or
an id.

### The library, the books and the places (a device token)

Everything else under `/api/app/1` wants a device token and nothing else: a browser's cookie is `401
device_token_required`, an API key `401 token_wrong_kind`, a revoked token `401 token_invalid`. The same services answer as
in the browser, so the rights are the browser's: a book in a library the person is not a member of answers `404
not_found` like one that does not exist, at every address. **No answer names a place on the disk** (no path, no folder, no
file name; a file is sent as `<id>.<format>`).

| Route | What |
|---|---|
| `GET /api/app/1/me` | `{account: {id, name, language}, libraries: [{id, name, kind, right}], server: {version}, installation_id, features}`. `installation_id` is a random name of the server, the same for ever; `features` names what the server can do for the app (`pairing`, `library`, `works`, `covers`, `files`, `progress`, `bookmarks`, `history`, `pairing_request`, `pairing_request_confirm`, `pairing_request_preview`); `device: {name, may_confirm}` is the device that asks, and whether it may confirm the code of another (not when it was itself connected through a device). |
| `GET /api/app/1/library?after=<marker>&limit=1000` | The works the person sees, compact (`id`, `title`, `authors`, `series`, `genre`, `year`, `library_ids`, `color`, `cover_etag`, `editions` with `id`, `library_id`, `kind`, `variant`, `language`, `duration`, `pages`, `formats`, `size`, `narrators`), plus `removed`, `marker`, `latest`. |
| `GET /api/app/1/works/{id}` | One work: the same, and `description`, `original_title`, `original_language`, per edition `title`, `subtitle`, `isbn`, `publisher`, `published`, `translators`, the `files` (`id`, `part`, `duration`, `size`, `sha256`, `format`, `mime`) and, of a recording, its `chapters` (`number`, `title`, `start` in seconds). `sha256` is empty while the file on the disk is not the one nexbooks read (changed by size or time since; the next scan reads it again): check against it only when it is there. |
| `GET /api/app/1/works/{id}/cover` | The cover as WebP with its `ETag` (the `cover_etag` in quotes); `If-None-Match` gives `304`. One size (the scan makes it small). |
| `GET /api/app/1/files/{id}/content` | The bytes, with `Range`, `If-Range` and `ETag`. A download that broke off goes on with `Range` and `If-Range: <ETag>`: `206` with the rest while the file is the same, `200` with the whole file once it changed. `HEAD` is not answered (`405`): `Range: bytes=0-0` tells the size. Too many at once `429 too_many_streams`, more than 600 in a minute `429 stream_too_often`, both with `Retry-After`. |
| `GET /api/app/1/progress?since=<time>` | The person's places, with `device` (the name), `this_device`, `moved_at`, `age`, `percent` (0 to 100), and `until` to ask with next time. Places that arrived up to a minute before `since` come again. |
| `PUT /api/app/1/editions/{id}/progress` | Body `{locator, percent, age, device}` (and `chapter`, `chapter_count`, `chapter_title`). Answer `{kept, current}`: `kept` false means another device was there later, `current` says where. The rule of the browser: `age` counts back from the moment the report arrives, at most 7 days. |
| `GET`, `POST /api/app/1/editions/{id}/bookmarks`; `PATCH`, `DELETE /api/app/1/bookmarks/{id}` | As in the browser (`fraction` 0 to 1). |
| `POST /api/app/1/editions/{id}/finished`; `GET /api/app/1/history`; `DELETE /api/app/1/history/{id}` | As in the browser. |
| `GET /api/app/1/later` | The person's list for later, books on their own shelves (the wish list is for books that are not): `{"items": [{"work_id", "added_at"}]}`, the newest first, only works the person sees. An entry for a work they do not see at the moment stays and comes back with it; when works are joined it goes with the book. The list is the person's own, no operator sees it. A book that is finished, by the rule at the end of the book or by hand, on any device, leaves the list in the same transaction. The server has it when `features` holds `later`. |
| `PUT /api/app/1/later/{work_id}` | Puts the book on the list (`204`); a book that is on it stays as it is. `404` for a work the person does not see. `409 later_full` past 500 entries. |
| `DELETE /api/app/1/later/{work_id}` | Takes it off (`204`), the same for a book that is not on it. |

**The marker.** Without `after` (or with 0) the whole library comes, oldest change first, `limit` works at a time; while
`marker` is below `latest`, ask again at once with `after=<marker>`. Starting from nothing, `removed` is empty: keep the
works of all pages and forget the rest once `marker` reaches `latest`. Then ask with the last marker and get only what
changed: works whose book, editions, files, chapters, names, cover or library changed, and in `removed` the works that
went or that the person may no longer see (only those the asking device was given; apply `removed` before `items`: a work that went is named even when a work of the same number is in `items`, though numbers of works, editions and files are never given twice). Once the device asks with a marker past a removal, it has heard of it: the removal does not come again, also not when the work changes later in a library the person left. A person in no library gets `latest` 0 and nothing else.
Every change has a number of its own, taken in the writer's transaction; nothing is lost while others write. A marker the
server cannot answer for is `410 marker_too_old` (older than the removals it keeps, 90 days; newer than anything it gave
out; or from before a restored backup): start again from nothing.

## Errors and limits

Errors look like `{"detail": {"code": "token_invalid", "message": "No valid device token."}}`. The code is stable; the
message is English.

| Answer | Code | Meaning |
|---|---|---|
| 401 | `sign_in_required` | No session. |
| 401 | `token_invalid` | A device token that does not exist, was revoked, or whose account is blocked. An app stops at the first one (every request, not only that one) and offers to pair again; it never takes a `429` for this. |
| 401 | `key_invalid` / `api_off` | An API key that does not hold, or keys are switched off. |
| 403 | `ereaders_off` | The operator has switched e-readers off. |
| 401 | `token_wrong_kind` | An API key where a device token belongs, or the other way round. It does not count as a wrong guess, nor do `api_off` and `origin_refused`. |
| 403 | `browser_only` | The route needs a signed-in browser, not a token. |
| 403 | `second_factor_setup_required` | The operator requires a second factor this account has not set up. |
| 429 | `too_many_attempts` | Five wrong tokens, keys or passwords from one address: it rests a quarter of an hour (a wrong secret or code of the app's pairing has a count of its own, see above). A device token or an API key that exists is looked up first and never turned away by this rest, only one that does not; the same wrong token or key from the same address counts once (while it keeps coming within the hour a count lives), so a revoked token that an app sends with every request shuts nobody else out. The same code comes from the door before the sign-in: after 30 wrong sessions, tokens or keys within a minute from one address, a credential that nobody has is answered `429` with `Retry-After` (seconds) until the minute is over; the same credential again within the minute counts once. A session, device token or key that exists is never turned away by it. |
| 413 | `too_large` | The request is larger than the limit; `max_mb` says how large. |
| 429 | `slow_down` | More than 120 requests a minute with one key; `Retry-After` says when to go on. |
| 429 | `source_busy` | A search in the sources would have to wait for its turn longer than a few seconds; `Retry-After` says when to try again. |
| 429 | `too_many_streams` | More files are being sent to one person at this moment than the instance allows (12 unless the operator changed it); `Retry-After` says when to try again. A player never meets it. |
| 429 | `stream_too_often` | One person asked for the bytes of files more than 600 times in a minute; `Retry-After` says when to go on. |
| 429 | `review_search_too_often` | One person looked for others in the sources more than 30 times in a minute; `Retry-After` says when to go on. |
| 429 | `search_too_often` | One person searched more than 240 times in a minute (the catalogues: 20); `Retry-After` says when to go on. |
| 429 | `too_many_wishes_today` / `too_many_searches_today` | One person (not the operator) made the wishes or started the searches by hand one may in a day (100 and 60 unless the operator changed it); `Retry-After` says the seconds until midnight of the instance. |
| 429 | `discover_too_often` | One person asked more than 300 times in a minute for Discover (120 for changes to the wish list); `Retry-After` says when to go on. |
| 429 | `kindle_too_often` | More books mailed to a Kindle than a person may send (6 a minute, 30 an hour; 3 test mails a minute); `Retry-After` says when to go on. |
| 429 | `opds_too_often` | One person asked the catalogue for reading apps more than 600 times in a minute; `Retry-After` says when to go on. |
| 429 | `kosync_too_often` | One person asked the sync of KOReader more than 120 times in a minute (the answer is the one of the interface, with `detail`, not the shape of the plugin); `Retry-After` says when to go on. |
| 429 | `kobo_too_often` | One person made more than 600 requests of a Kobo in a minute, or put books on the list for the Kobo or took them off more than 120 times; `Retry-After` says when to go on. |
| 409 | `kobo_list_full` | The list for the Kobo holds as many books as it may (1000). |
| 401 | `unauthorized` | On `/kobo/<secret>/…`: no such connection, blocked, or its account is blocked or waits for its second factor. Names nothing. |
| 409 | `wishlist_full` | The wish list holds as many books as it may (500). |
| 409 | `later_full` | The list for later holds as many books as it may (500). |
| 409 | `too_many_watches` | The caller watches as many authors as one person may (100). |
| 503 | `search_too_slow` | A search took more than 5 seconds in the database and was stopped; use more words. |
| 409 | `entry_changed` | The book was decided or matched anew since the list was read: look at the list again. |
| 409 / 502 | `sources_off` / `source_failed` | No source is switched on, or none could be asked (`reason` says why). |
| 502 / 429 / 409 | `indexer_*` | An indexer: `indexer_key_rejected`, `indexer_not_newznab`, `indexer_wrong_kind`, `indexer_unreachable`, `indexer_blocked`, `indexer_limit_reached` and `indexer_daily_limit` (`429`, `retry_after` in seconds), `indexer_off` (`409`). |
| 502 / 504 | `prowlarr_*` | Prowlarr: `prowlarr_key_rejected`, `prowlarr_not_prowlarr`, `prowlarr_too_old`, `prowlarr_unreachable`, `prowlarr_blocked`, `prowlarr_timeout` (`504`). |

A wrong token counts against the sender like a wrong password, the same one once. A wrong secret or code of the app's pairing counts too, but
in a count of its own.

## Routes for programs

### `GET /api/v1/me`

With an API key: who the key speaks for: `name`, `display_name`, `scope` and the nexbooks `version`.

### `GET /api/v1/auth/me`

With a session or a device token: the account, its profile and what this server offers it. The profile holds `theme`
(`system`, `light`, `dark`), `view` (`shelf`, `grid`), `market` and `language` (two lower-case letters or empty, for
Discover), `timezone` and `timezone_source`, and `notify_login`.

### `GET /api/v1/health`

`{"status": "ok", "version": "…"}`, open to everybody (the container's health check uses it).
