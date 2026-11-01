# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through GitHub instead:
**Security, Report a vulnerability** on this repository (private vulnerability reporting). Say what you found, how to
reproduce it, and which version you ran.

You get an answer within a week. A fix goes out as a new release; the release notes name the problem once the fix is
available, and you are thanked there unless you would rather not be.

## Supported versions

Only the newest release gets security fixes. Updating is `docker compose pull && docker compose up -d`; see
[Updating](README.md#updating).

## Reaching nexbooks from outside

nexbooks is best used in your own network, and from on the way through a VPN. If you open it to the internet anyway,
the README has a checklist: [Reaching nexbooks from outside](README.md#reaching-nexbooks-from-outside).

**A request that cannot be anybody's costs next to nothing (under `/api/`).** One that brings neither a session cookie nor a token to a
route that needs a sign-in is answered `401` before the framework, a thread or the database is asked. A sender that
guessed wrong 30 times within a minute (a session cookie, device token or API key the routes turned away as wrong, the
same one once; a key at a route for devices, keys that are switched off or a key sent by a web page are mistakes, not
guesses, and do not count) is answered `429` until the minute is over, but **only for credentials the database does not
have**: the door looks the credential up (one question about three hashes, on a connection of its own) and never turns
away a session, a device token or an API key that exists, not after a restart and not behind a shared address (a
household, an office, a proxy). Where the database cannot answer, the request goes on to the route. The sender is told
apart as for the sign-in brake: without `NEXBOOKS_TRUSTED_PROXIES` a made-up `X-Forwarded-For` opens no new count, and
the proxy as a whole is the sender; `X-Forwarded-For` counts as the sender's address only from a trusted proxy. Measured
on one machine at a steady 400 requests a second from outside: a person's shelf took 60 to 64 ms (54 ms alone) instead
of 74 to 245 ms.

The doors for reading apps (`/opds`, `/kosync`, `/kobo`) are not behind this door: each looks its secret up in the database
(one or two small questions) and counts wrong ones against the sender on its own, five and then a quarter of an hour of `429`
(a secret that exists is never turned away by that). Measured in one process, a request of that kind that belongs to nobody
took about 11 to 13 ms against 2.5 ms for the door. A flood of eight senders on those doors slowed a person's shelf no more
than a flood on the door did (206 to 214 ms against 277 ms, in one process, which measures the interpreter lock more than the
doors). They stay out of the door because they have
three more ways to bring a secret (a Basic header, two headers of KOReader, a path) and answer `403` while the operator has
e-readers switched off, which needs the database too.

**The container.** It runs with a read-only root, a small `/tmp` in memory, no new privileges, every capability dropped
but the four the start needs to set the owner of `/data` and to drop from root, and as the user PUID:PGID (never root).

## What the operator can reach

The operator manages accounts, the settings, the libraries and the backups. A backup holds the server's secret
(`secret.key`) together with the database: with both, the mail credentials, the API keys of the indexers and the passwords
of the download programs can be read. Keep backups as protected as the data folder. A device token never reaches the operator's side, not even the operator's own.

## Books are only read (unless the operator turns tidying on, books are fetched or uploaded, or an edition is moved)

nexbooks reads the folders of its libraries and never writes into them: it does not delete a book, and it does not
change the metadata inside a file. The exceptions are filing what was fetched or uploaded (below, "Checking and filing")
and "Tidying" (Settings, Tidying), **off from the start** and the
operator's alone, and only in a library the operator is a member of with "manage" (a preview names the books and their
files, and those are for the members of a library, the operator included): it renames and moves files by a scheme,
after a preview and after the operator's confirmation, in a folder nexbooks may write in (the libraries are mounted `:ro` from the start, and then nothing can happen). It never
overwrites a file, never follows a link, never leaves the library folder (a name from a book's metadata can neither climb
with `..` nor add a `/`), moves a book whole or not at all, writes every move into a journal, and can be undone. The
third is **moving an edition into another library**, for a member with "manage" in both (the operator included only as
such a member), after a question: every file of the edition goes to its goal by the scheme of filing, never over anything
(a goal that is taken gives another name), never through a link; on the same disk as a second name, else as a copy whose
size is compared; the files at the source go only after every file lies at its goal and the edition belongs to the
target, each only while its twin there is whole. A move that fails before that takes back what it put in the target; one
that fails after it leaves the rest at the source, which no scan takes for a new book. A path in
the database is relative to its library and is checked before every use: nothing outside the library folder is ever read,
whatever the database says (links and `..` included).

**One more thing is done by itself, and only where nexbooks may write.** After a scan, an edition that holds one file twice,
the same to the byte, keeps one copy and puts the others into the trash of the library (they come back from there). That
happens only in a library nexbooks has been given to write in: the operator switched tidying on, or the library is where
fetched books go (Settings, Formats and places). In every other library the copies stay where they are and are only named
under "Braucht dich", even when the folder is mounted so that nexbooks could write.

## Searching and downloading

- **The search finds only what the caller may see, and takes no time for what they may not.** Every word in the search index
  carries the library of its book in front, and a search asks for the words of the caller's libraries only, so the index
  never reads what lies in another library. The statement that finds the books carries the rights a second time (a library
  of the caller, a file on the disk), and nothing is filtered afterwards. What is typed never becomes syntax of the index
  or of SQL: it is cut into words of letters and digits, each quoted. A search that runs longer than five seconds is
  stopped, and a person may search 240 times a minute.
- **A download names the file by its number.** The place on the disk comes from the database and is checked against the
  library folder again at every request (no `..`, no absolute path, no link, a plain file inside the folder); a file of a
  library the caller is not in, a file beside a book and a file that is gone answer `404` exactly like a number nobody
  had.
- **The catalogue search is for a signed-in browser**, never for an API key: it costs the sources a request. It sends the
  typed words and nothing else, only to the sources the operator switched on, at their pace, and a person may ask it 20
  times a minute.

## Reading in the browser

A book is HTML, CSS and pictures made by somebody else, opened in the browser of a person who is signed in to nexbooks. That is
the place where a book could do harm, and so the reader is built around one rule: **no script runs in a page of a book**, and
what a book does not need to reach is out of its reach.

- **The frame of the book** (`/readview.html`) is served with a policy of its own: `connect-src 'none'` (it cannot call
  nexbooks, nor anybody else), scripts only from nexbooks's own files, pictures, styles and fonts only out of the book,
  no windows, no forms, no navigation of the page that holds it. It fetches nothing itself: the page fetches the file with
  the person's session and hands the bytes over (a PDF has a frame of its own, with no origin at all).
  Messages count only from the frame the page holds. The frame has the origin of nexbooks, because the reader has to reach
  into the pages it turns (a browser allows that only inside one origin; a frame without an origin cannot be reached into,
  which was tried and measured); the protection is therefore that no script runs.
- **The walls are tried before every book.** The two walls that belong to the browser (a page's frame has no `allow-scripts`,
  and the page inherits the policy of the frame) are settings, and a setting can fail without a sound. So the reader makes a
  page the way it makes a page of a book (a `blob:` address, in a frame with the same `sandbox`), puts a script of its own in
  it and looks whether it ran. If it did, no book is opened in that browser and the person is told so in plain words; the file
  can still be downloaded. (The PDF frame has no origin and is not tried this way.)
- **Every page of a book is cleaned before it is shown**, whatever the manifest calls its media type (parameters and capitals
  are ignored): `application/xhtml+xml`, `text/html`, `image/svg+xml` and also `application/xml` and `text/xml`, which a
  browser would draw as a page. A page of the spine of any other type is replaced by an empty page, and a part that is nothing
  the reader knows (`text/plain`, `application/octet-stream`, other `+xml` types, a missing type) gets the type
  `application/octet-stream`, so that no browser draws it or guesses a page from its bytes. An XML document that is no XHTML
  page is an empty page. An SVG page cannot carry a policy of its own (SVG has no place for one): it is cleaned the same way,
  and the policy and the sandbox of the frame are its walls. What is taken out: `<script>`, frames, objects, embeds, forms, sound and video,
  `<base>` and `<meta http-equiv>` (a refresh) are taken out, so is every attribute that runs code (`on...`, `javascript:`
  and `data:text` addresses), and every address that does not lead into the book (`blob:` parts the reader made, `data:`
  pictures and fonts, places in the page): a picture, style or font from another host loses its address. CSS is read by a
  tokenizer that follows the rules of CSS Syntax (names are decoded, so `u:l(`, `@9mport` and comments between
  the parts are read as a browser reads them) and loses `url()`s and `@import`s that lead out, `image-set()`, `image()`,
  `src()`, `cross-fade()`, `expression` and `-moz-binding`; an SVG attribute such as `fill` or `filter` is read as CSS too.
  A page nested deeper than 64 levels is flattened (the wrappers go, the text and pictures stay) and a page of more than
  100,000 nodes is cut; the reader says so. Without that a page of a few bytes nested 200 deep brings a browser tab down. Beyond 2,000
  inline elements (`span`, `a`, `em` and the like) in one place the rest give up their tags and keep their text, without a notice: one
  unbroken run of 20,000 of them takes WebKit a minute to lay out. Each page carries a policy of its own as a `<meta>`
  (`default-src 'none'; script-src 'none'; connect-src 'none'; ...`), put in front of everything else. The loader loads no
  script file and no sound or video. A page that is no valid XHTML is read as HTML and cleaned the same way.
- **The frame of each page has no `allow-scripts`** wherever the browser lets the reader work that way; Safari (and every
  browser on an iPhone or iPad) needs the flag (a WebKit bug stops the reader's events otherwise), and there the policy of
  the page, its cleaning and the loader are what forbid scripts. Links to the web open only after a click and after a question that names the
  address the way the browser reads it, in a tab of their own with `noopener`, and only `http`, `https` and `mailto`.
- **Limits, in the browser as on the server:** at most 20,000 entries in a ZIP and a directory of at most 2 MB; all parts
  together may not claim more than 1 GiB unpacked, and more than 64 MiB only at a ratio a book could have (300); one text
  part at most 24 MiB, any part 128 MiB; a part is unpacked only as far as it said it is (plus a little): one that says it
  holds 2 KB and holds 120 MB is stopped at once, and the reader says so. A picture whose header claims more than 36
  million pixels is not handed to the page. The whole file may be 100 MB: a tab needs about twice that on top of what it needs without a book (measured), and a phone gives a tab far less than a desktop does. A larger book is named as too large for the browser, is not even fetched, and can be downloaded.
- **Copy-protected books are named, not opened.** A rights file or an encrypted part that is not a font scrambling makes an
  EPUB protected, Kindle's encryption flag does the same for MOBI and AZW3, a PDF with a password the same; nexbooks does
  nothing to get around any of it.
- **The bytes of a file** (`/api/v1/files/{id}/content`) are served by the number of the file only, the place on the disk
  comes from the database and is checked again; they answer `Range` and an `ETag`; a file of a library the caller is not in
  answers `404` like a number nobody had; the answer carries a policy that makes it show and run nothing if somebody opens
  the address as a page. A device token and an API key read them (the key only where the operator switched keys on, and
  never from a web page). One person has at most 12 files being sent at one moment (all their devices together, `NEXBOOKS_MAX_STREAMS`) and may ask 600 times a minute; beyond that `429` with `Retry-After`, so that one account, or a stolen session, cannot make the server slow for everybody else.
- **Where a person is in a book and their bookmarks are theirs alone.** Every route names the person who asks and nobody
  else, and a place or a bookmark of an edition the person no longer sees is hidden with it. The operator reads no one's
  place. Of two reports from two devices the newest stays; a device says how long ago the reader was at a place, not what
  time its clock shows, so a clock that is wrong keeps no other device out, and checking and writing is one statement.
  Setting a place or a bookmark needs a browser or a device (a key only reads).
- **Connecting the iPhone app** (`/api/v1/devices/pairing`, `/api/app/1/pairing`). A signed-in browser makes a code: an id, a
  secret (32 random bytes) and a code of eight characters (about forty bits). They are shown once and kept only as
  SHA-256 hashes, so neither a database nor a backup tells them. A code lives ten minutes, an account holds five open ones
  (counted and made in one statement), and "sign out everywhere" or a new password deletes them. The device token is made
  only when the app fetches it, in the one transaction that takes the code (a conditional `UPDATE`): of two apps at the same
  moment exactly one gets a token, and a device that cannot be made takes nothing. The secret and the code are compared as
  hashes with `hmac.compare_digest`, also when there is no such code. An unknown id and a wrong secret answer the same
  `404`; only whoever shows the right secret or code learns that a code ran out or was used. A wrong secret or code counts
  against the sender (five, then a quarter of an hour; a count of its own, so a mistyped code shuts out neither a device
  that exists nor the sign-in, and a right secret is not turned away by it). Over all senders together only 30 wrong codes
  pass in ten minutes, then fetching by code rests (the QR code's secret has 256 bits and goes on): a net with many
  addresses cannot try the forty bits out five at a time. A text that cannot be a code costs nothing. A device token that
  exists is never turned away by the rest of an address; one that does not meets it. A request with an `Origin` (a web page)
  is refused before anything is looked at, so no page can use a code or use up a visitor's attempts; the two addresses take no
  more than 4 KB of body (nobody has signed in there). The address in the QR
  code is the operator's public address, else the browser's, and then only a plain `http(s)://host[:port]` that is the very
  `Origin` the browser sent. The log says "A device of <name> was paired: <device name>" and never a token, a secret or a code.
- **Connecting a device that has no camera** (`/api/app/1/pairing/request`, `/api/v1/devices/pair-requests`). The other way
  round: the device asks, without any sign-in, for an id, a secret and a code of eight characters; they are kept as SHA-256
  hashes only. A signed-in person types the code, sees how the device calls itself and confirms: only that ties the request to
  their account, and **the code alone gives nothing**, because the token goes to whoever shows the secret. The token is made in
  the conditional `UPDATE` that takes the request when the device fetches it, once, and is **never stored** (not even for the
  seconds between the confirmation and the fetch; what waits in the database is the decision, not the key). Asking is open to
  anybody, so what waits for a confirmation is limited: 10 per sender and 300 in all. The sender is the address of the connection;
  `X-Forwarded-For` counts only from a proxy named in `NEXBOOKS_TRUSTED_PROXIES` (anybody can write that header: from any other peer
  it would buy a place for every value). A sender at its limit does not meet a wall, its
  oldest open code runs out for the new one, so a restarting television never locks itself out; only the limit over all
  senders says `429`. The sender is a salted hash that changes with every start and is kept only to count. The secret and the code are compared as
  hashes with `hmac.compare_digest`, also when there is nothing to find; an unknown id and a wrong secret answer the same
  `404`, and only the right secret learns that a request was used. A wrong secret when the device asks how it stands counts
  against the sender in a count of the television's own (five, then a quarter of an hour), a wrong code typed in the browser
  in the count of the pairing with a QR code and, in the browser, against the person (changing addresses does not help), so the
  mistakes of a television never keep a person from typing; letting go is always answered `204` and never counts (the secret
  has 256 bits); over all typists together 30 wrong codes pass in ten minutes (a watch of its
  own, so a signed-in person cannot rest the typing of the codes of the iPhone app). A right secret is not turned away by the
  rest. The window shows the name as plain text in quotation marks, in the size of the text around it and on one line, under "the
  device calls itself" (never as a heading), warns before the code is typed ("only type a code that you see on your own
  television") and again at the question that a code somebody else read out may connect a stranger's device to the account.
  A device name holds at most three stacked marks and at least one character that shows (a name of blanks or of a streak of
  marks is refused, for every way a device gets a name). "Sign out everywhere" and a new password end the
  confirmations not yet fetched; a blocked account is given nothing. The log says "A device of <name> was paired by
  code: <device name>" and never a token, a secret or a code.
- **The iPhone confirms the television** (`/api/app/1/pairing/request/confirm`). A person who paired the iPhone app can
  connect an Apple TV without a browser: the television asks for a code as above and hands its id and code to the app over the
  home network (never through nexbooks), and the app confirms **with its own device token**: no cookie, no API key. It is
  the same decision as in the browser (one function ties a request to an account), for the account of the token; the
  television's token is its own, made when the television fetches it with its secret (never stored, never the phone's),
  and stands in the list of devices to be revoked alone. **Id and code must both fit**: an id nobody made, a code that
  does not fit and a request that another person confirmed answer the same `404`. A wrong pair counts like a wrong code typed
  in the browser (per sender, per account, over all typists together, one count with the browser's), and **a right pair is
  not turned away by any of those counts**: an id has 128 bits and cannot be tried, so the right one needs no protection
  from others' mistakes (as the right secret of a television is not turned away). Because the id is no secret from the
  home network (it stands in the address the television asks at), five wrong codes against one request rest that very
  request, for the right code too; without that, whoever sees an id could try the 40 bits of the code at will. That count
  goes on while the sender, the account or the whole server already rest, so resting one's own counts first buys nothing.
  A request confirmed already is `410` also for the same account. The app may look first (`/preview`: the name the server
  keeps, nothing decided, the same counts) and take its confirmation back until the television fetched. The body holds 4 KB
  at most, read before the token is looked at. The log says "A device of <name> was confirmed from the app <phone>:
  <television>" and never a token, a secret, a code or an id (a request that rests is named in the log by its kind only).
- **A device token that brings a device.** This is the one way a device token makes another device, so it is fenced in.
  The television is connected **through** the phone (kept on the device and on the request): the list of devices says
  "Connected through <phone>", **revoking the phone revokes the television with it** in the same transaction, and so a
  confirmation of the phone the television has not fetched yet (it hears `expired`); the fetch itself also asks whether the
  phone still holds. A device connected this way confirms nothing (`403 device_chain`, before anything is counted), so there
  is no line of devices. Whoever holds a stolen phone token can bring one television, but blocking the phone in the list,
  "sign out everywhere", a new password, or the operator signing the account out or blocking it ends both; the second
  factor or a password is not asked for by the phone (it was met when the phone was connected), which is why its reach ends
  with the phone.
- **The surface of the iPhone app** (`/api/app/1`). Every address but the ones that give a token wants a device token and
  nothing else (no cookie, no API key), and answers with the same services as the browser: a book in a library the
  person is not in is `404` there too. No answer names a path, a folder or a file name; a file goes out as
  `<id>.<format>`. The list of changes (`removed`) names only works the asking device was given, so nobody learns the
  numbers of books in libraries they were never in, and only once: who left a library does not hear of later changes in
  it. A person in no library is told no marker at all (it would say how much others write). Numbers of works, editions
  and files are never given twice (`AUTOINCREMENT` since schema 20), so an app never takes one book for another. A
  revoked device token or API key that keeps coming counts as one wrong guess, and a token or key that exists is looked
  up before the brake: one forgotten iPad behind a shared address shuts nobody out. The order of changes is kept by
  triggers in the database, so no way of writing escapes it; a restored backup sends every device back to the start.
- **Listening uses the same walls.** A recording is played from `/api/v1/files/{id}/content` like a book is read, part by
  part, with `Range`; nothing is converted or cached on the server. A place in a recording is a time that must lie inside
  it, and an e-book takes no time as its place. The cover on the lock screen is nexbooks' own address. Many requests of one
  player at once are served by the same rules (more than 100 ranges in one request get the file once, as it is).
- **No offline copies yet.** Keeping a recording in the browser for later (Cache API, a service worker that answers for
  the server) would hand the file out without asking the server whether the person may still have it, and would keep it
  on a shared device for the next person; nexbooks does not do that until it can tie such a copy to the person and remove
  it at sign-out.

## What the files of a library can and cannot do

The files in a library come from outside, so nothing in them is trusted. The scan never opens one itself: a helper
process does, one file at a time, with a time limit (a file that takes too long ends the helper, not the scan), and its
answer comes back as JSON, never as an object that could run code. Every reader keeps limits: an EPUB (a ZIP) may hold
only so many entries and may not unpack to hundreds of times its size; its XML is read with `defusedxml`, which refuses
entity declarations ("billion laughs") and external files; a MOBI header is checked against the record it lies in before a
length is used; `metadata.json`, tags, chapter lists, descriptions and pictures are read up to a size and a count, and a
field of an unexpected type is a field that is not there. What the scan does with the names of the authors after it
settled (putting spellings of one name together, looking for editions held twice) does not grow with the square of what a
library holds: a last name that more than 64 names share is left alone (the log says so once per scan), and the walk for
editions held twice ends when the library has its pairs. A cover is drawn anew from the pixels as a small WebP, refused
before it is opened when it is too large in bytes or pixels.

A library is a folder at or below `NEXBOOKS_LIBRARY_ROOTS` (the path is resolved first, so `..` and links cannot lead
out), never the data folder, never a folder that overlaps another library. Links in a library are not followed, files are
opened below the library's folder only (a part of the path that is a link, or a file swapped for a link after the walk,
is refused), and a folder that is gone, or holds no book while books are known in it, stops the scan instead of marking
them missing (as does a walk that met more entries than it looks at: nothing it did not see is marked). A name that is no
valid text, or a fault inside nexbooks while one file or book is handled, is listed as a problem of that file; it does not
end the scan. Every list, count, cover, series and person is asked for in the SQL from the libraries the caller is a
member of; what the caller may not see answers exactly like what does not exist, the operator included. **A book that
lies in several libraries shows only what the editions the caller may see say about it**: its description, series and
volume, genre, pages, year, original title, authors, cover, colour and the date it came are worked out from those
editions, in the SQL as well (the filter by genre, the numbers per genre, the order of the shelf, the members of a series
and the books of an author), never from the summary the server keeps for all of them. Mount the books
**read-only** (`:ro`); nexbooks does not need to write there.

## Backups, and where the data lies

**A backup holds the database and `secret.key`, not the books.** Beside them it holds the pictures nexbooks made or
fetched (the covers, the photos of the authors) and the languages the operator
added: nothing makes those again by itself once the database names them, so without them a restore on a new machine
would show empty covers. Each picture is named by the start of its own sha256 and is checked against it in the trial
run; a restore adds pictures that are missing and never overwrites one. It is made with SQLite's backup API (a file copy of a
database in WAL mode would miss what is not yet in the main file). Downloading, deleting, uploading and restoring one
ask for the operator's password again, from a browser only: a stolen session or a device token can neither carry a
backup away nor lay one out. An archive that is uploaded or restored is read with its limits: it may hold nothing but
its three parts, pictures under `covers/`, `people/` and `foreign-covers/` named by their checksum, and languages under
`locales/` (a path, a link or another name makes it unusable; there must be as many pictures and languages as the
manifest counts, each picture at most 4 MB and each language at most 512 KB); the manifest is read up to 64 KB and no further; the
bytes are counted while they are unpacked and never taken from what the archive says about itself; the database must
be the one the manifest names by size and sha256 (a flipped byte is "damaged", not an error).

**How the trial run opens the database of an archive.** It never opens it in place and never with the server's own
connection. The database is unpacked into a hidden scratch file in the backups folder (`.check-<process>-<time>.db`,
removed when the run ends, also after an error) and looked at in this order. (1) `PRAGMA integrity_check` and
`user_version` through a plain `sqlite3` connection; no extension can be loaded and nothing is attached. (2) A read only
connection (`mode=ro`) lists the triggers, views and virtual tables in `sqlite_master`; any that nexbooks does not make
itself ends the run, **before anything is written**, because a trigger would fire at every write of the app after a
restore, a view may name anything and a virtual table has a module of SQLite behind it. Plain tables of data nexbooks
does not know are left alone: nothing reads them. (3) Only then is the scratch copy written to, as the start does it:
the steps that bring it to today's schema, the end of the stored sign-ins, and a look (read only again) that every table
and column nexbooks needs is there. Any error in any of it, or a database without tables, makes the archive "not
usable", logs the kind of the error (never its text, which could quote the database) and changes nothing. The scratch
copy is the only file that is changed. Because the real start does the same steps, an archive that passes cannot stop
the next start.

**A restore that fails.** The swap at the start is made on temporary files beside the live ones and ends in `os.replace`;
the live database and `secret.key` are not touched before that. If anything fails, the staged files move to
`backups/restore-failed/` (with a note of when and what kind of error), the log and the operators (push, and a mail with
a mail server) say so, and the server starts with the database it had. The way back is also kept: a restore makes a copy
of the state before it first.

**What a restore brings back.** It ends every session, but a backup does not know what was ended after it was made:
devices and API keys that were revoked, blocked or removed since are valid again after it. The trial run counts them
from the live database (`revived_devices`, `revived_keys`, `revived_links`) and says so in the dialog; after a restore, check
the lists of devices, keys and connections for e-readers and withdraw what should stay gone. A password changed since is the old one again, too.

**SQLite on a network share.** NFS and SMB do not keep SQLite's promises, and a database there can lose data without a
word. nexbooks looks where the data folder lies (the mount table on Linux, the drive type on Windows), says so on the
storage card and in the log at the start, and does not stop: the decision stays with the operator. The books may lie on
a share.

## Addresses the server reaches out to

Web Push targets are checked like every way out: resolved once and connected to exactly as checked, never link-local or
a cloud metadata address, no redirect followed, an answer read up to a few kilobytes, and one deadline for the whole
request, the answer read included. The sources of book data (Hardcover, Open Library, Audible, the national libraries of
Germany, France, Sweden, the Netherlands and Norway) use the same rules, and more (Audible is off until the operator turns
it on):

- **Hosts out of a fixed table.** The host of a request is never made from what a person typed or a book says: it is one of
  a few hosts per source (checked again before every request), https only, no credentials, no other port. A redirect is
  followed at most twice and only to a host the source's own table names (Open Library's cover addresses lead to the
  Internet Archive); a token never goes to such a host.
- **Slowly and switchable.** The operator's main switch means no request at all; each source has its own switch, a pace
  (a bucket kept in the database, so a restart gives no free burst) and a count for the day, and rests after errors.
  A person's request waits a few seconds for its turn at most.
- **Only the words of a book go out**: title, author, ISBN. Not a file, a path, the name of a library or a person.
- **Hardcover's token** is the operator's, sealed with the server's secret, never read back, never in a log or an address,
  and sent to one host only, Hardcover's API (not to its picture host, not after a redirect to any other host). A token
  Hardcover refuses, by its status or by an error that says so, stops that source until it is replaced.
- **What a browser sends is not data about a book.** A proposal is taken by its id out of what the server kept; the address
  of a cover is never taken from a request. The XML of the national libraries is read with `defusedxml` (no DTD, no
  entities) after a size limit.
- **The catalogue of an author** is public data, built in the background from the author's name only. What a person has
  of it comes from the libraries they are a member of and nothing else: a book of another library never shows as theirs,
  never counts, and the search names only the authors of their own books or those a source named for the words. The page
  of an author nobody has a book of is reached by a random token.
- **Watching an author is the person's own and has brakes.** Whom a person watches is read and changed by that person alone
  (the operator included); the wishes it makes are seen by the others of the library, the name of the author watched
  only by the person. A watch makes ordinary wishes through the same code and the same rights as a wish by hand
  (the right `acquire` in a library that takes the kind; without it a book goes onto the person's wish list), and each
  look is claimed work by work in the database (a unique row), so two requests at the same moment, a double press or the
  beat running beside a person never wish a book twice. At most 100 authors per person, at most half of a person's wishes of
  the day, at most 40 wishes per look, and the wishes for a book that is out are searched as background searches. The beat
  asks for the catalogue of a watched author only when it is a day old, the lane of the catalogues is free, and every source
  has used less than half of its requests of the day; with the main switch of the automatic work off it does nothing.
- **Rights.** The list of books to look at and every decision on a book are for a member with `manage` in its library (the
  operator only as a member). What a source gave, and what a person corrected, belongs to one edition and is read only by
  those who may see that edition: joining translations under one work shows nothing of the other library.
- **A decision and the matching do not overwrite each other.** A person's decision is taken, and the matching writes its
  answer, each in one step with the check that the book is still as it was (on the list and undecided, or waiting): the
  loser gets `409` or drops its answer. Looking for others is limited to 30 a minute for each person, on top of the pace of
  each source.

## Indexers, Prowlarr and download programs

The operator connects these, and the way out to them is a gate of its own (`services/procure/gate.py`), apart from the one to
the sources, because they usually lie in the operator's own network:

- **Only the host and the port the operator typed.** A connection talks to the host and the port of its address and to no
  other; a link in an answer is never followed to a host, or to another port of the same host, that the operator did not
  enter. A redirect is followed twice at most and only to the same host and the same port; the scheme stays, or goes from
  http to https with the same port number or from port 80 to port 443 (a program behind a proxy that sends http to https).
  Never from https back to http.
- **The one exception: the link of a release.** An indexer gives the link to an NZB or a torrent file on whatever host it
  likes (a file host, a subdomain, a CDN); a real indexer does, and the *arr programs follow such a link to any host over
  http or https, so nexbooks does too. That means: the link, and any redirect from it (twice at most), may lead to a host the
  operator never typed, and whoever runs the indexer decides where the file is fetched from. The limit is the network, checked
  for every hop after its name is resolved: **behind an indexer on the internet no hop may reach a private, loopback, shared
  (100.64.0.0/10), unique-local or link-local address** (`release_link_private`), also not through a name that resolves to
  one; behind an indexer that itself lies in a private network (Prowlarr or an indexer in the home network, which hand out
  downloads from there) private targets are allowed. The metadata services of cloud machines are never reached, behind any
  indexer. Only http and https links are asked, the size and time limits hold as before, and nexbooks adds nothing of its
  own to the request: none of the session's headers go to a host that is not the connection's own, and the key it holds for
  the indexer is never put on a link (the link carries whatever the indexer put in it). What this costs: an indexer on the
  internet that turns hostile can make the server fetch one file from any public host, and its link tells that host the
  indexer's own key or passkey, which the indexer put in the link itself. A link that leads somewhere private is stopped. Whether an indexer lies in a private network is read from its name when the file is fetched: an indexer whose owner turns hostile
  and whose name answers privately for a moment can open the private networks to its links for that request, the same limit as
  the one below for private addresses; connect indexers on the internet over https.
- **A saved key goes only where it was saved for.** A test or a change that points to another host or another port (or
  back from https to http) is refused with `key_required` until the key is typed again, so a saved key can never be sent
  to an address somebody typed. The page asks for it before it sends anything.
- **Private and loopback addresses are allowed on purpose** (a homelab lives there). What is never reached, whatever anybody
  types: link-local space and the metadata services of cloud machines, also inside an IPv6 address. The name is resolved once
  and the connection goes to exactly the addresses checked.
- **A known limit:** because private addresses are allowed, an indexer on the internet that the operator connects over
  plain http, and whose owner turns hostile, can let its name point into the operator's network: nexbooks then asks that
  address in his network (a GET with the path and the query of the indexer's address, the answer read as a feed and never
  sent anywhere). Only the operator adds indexers; connect indexers on the internet over https, where the certificate
  holds the name.
- **Only the operator, in a browser.** No member, device token or API key can make the server ask an address; the routes check
  that before anything is sent.
- **Keys and passwords are sealed and never read back.** The answers say only that one is set; none is in a log, an address
  that is logged, or an error. The log names the host, the path and the names of the query, never a value, a title or a path
  of a file; the file an error of the system names (`[Errno 2] ...: '<path>'`) is taken out of a line and of a traceback
  before it is written.
- **Small and in time.** An answer is read up to 10 MB (an NZB file 20 MB, a torrent file 10 MB) within 30 seconds (90 behind
  Prowlarr); a packed answer is unpacked in bounded steps; XML is read with `defusedxml` (no DTD, no entities).
- **Slowly.** An indexer is asked once in two seconds, rests after a request limit until the time it names, and stops at the
  operator's limit of the day. A Prowlarr that cannot be read changes no indexer.
- **The right to acquire** sits between reading and managing; the operator has it in a library only as a member.

## Wishes and following the downloads

- **A wish and a download belong to a library.** They are seen and changed by who has the right `acquire` there, and a work
  only by who sees it; the right is checked in the SQL of every list and on every action, for a key, a device token and a
  browser alike. The operator has no right of his own here; he alone confirms a path mapping, and only for a download of a
  library he looks after (`manage`): an operator who does not is told nothing of it, not even its name.
- **A wish, a download, a history line and a block belong to their library, and nothing crosses its border.** A work shows
  in a list only to who sees it, so the history keeps no title for a person who does not see the book. The wish of another
  library is not seen: asking for a book answers with the wish of the caller's own library (two libraries may each wish the
  same book, and each gets its own release), and the options of a book name only what is wished or on its way there. The
  blocklist is seen and lifted by the people of the library the block was made in, with the name of who made it (a block
  still turns the release down for the book in every library). The numbers for the head count what the caller sees.
- **One release at a time for a book, a kind and a library, one wish for a book, a kind, a language and a library.** Two
  people, a double click and a race all end in one; the database decides (unique indexes), not a check before the write. A
  download in another language, or for another library, is never taken for the wish's. A torrent that is in its program for
  one library is not handed to it a second time for another (the program would refuse it): that wish takes the next release
  that passes, or waits.
- **A wish of a person who lost the right rests** (it neither searches nor loads as them) until they have it again; a
  colleague who searches it takes it over.
- **nexbooks reads only below the folders it was told to.** The path a program reports is read only at or below a library
  root or a download root (`NEXBOOKS_DOWNLOAD_ROOTS`), after every link is followed, and never in the data folder
  (the database, the keys and the backups): a download root that is the data folder, lies in it or holds it stops the start with
  a message, and a path a program reports that leads there, by a link or not, is a problem (`path_not_found`), never a finished
  download. A path mapping is never made up from
  what a program says: it is one of the places nexbooks found itself by the name of the job, confirmed by the operator.
- **Jobs nobody asked for are never touched.** They are listed to the operator alone (the programs are shared by every
  library, and the jobs may be what he put there himself), who may ignore them or have them removed; nexbooks never takes one
  over, and removes a job only when it is its own (a failure, a cancel) or the operator says so. Before every removal the
  job is looked for in nexbooks's category (by the category the program names for the job itself, not only by its filter):
  a program removes by name, and a torrent's name is its info hash, which the operator's own torrent of the same release
  shares. What is not in the category is never removed, with or without its files; an unsure hand-over that is cancelled
  only ends the download then.
- **One program that breaks holds no other up.** Each program's part of a round, and each other step of it, runs on its
  own; an answer nexbooks cannot read (also one nested too deep to parse) is that program's error.
- **A hand-over a stop of the server left half done** is found at the next start (and after ten minutes in a round): it
  ends as failed, without a block, and its wish searches again.
- **A torrent is taken away with its files only when they lie below a download folder and in no library.** A book that lies
  in a library is never deleted because its torrent reached its goal.
- **A program that does not answer changes nothing.** Its downloads stay as they were; a removal it missed is tried again
  every round. Log lines carry numbers and codes, never a title or a path.

## Checking and filing, uploads

- **What comes from outside is read like a book of a library**: by the readers of the scan in their helper process, with
  their limits (defusedxml, sizes, a zip bomb), never in the server itself. A program or a script anywhere in a release
  (`.exe`, `.bat`, `.sh`, `.ps1`, `.lnk` ...) turns the whole release down.
- **Archives are unpacked in a process of their own** (`python -I`, only the standard library, a memory limit and a limit on
  the size of a file it writes on Linux, a time limit): ZIP and TAR only, at most 2000 files and 20 GB, never more than the
  free space less a reserve, nothing packed tighter than a book can be (a bomb: one file at a ratio of 200, all files of an
  archive together at 30 of the size of the archive), no name that climbs out of the folder, no link and no device, nothing
  written over; a mark some file systems do not take in a name (`? : * " < > |`) is written as `_`, not a danger. An archive with a password, a broken one or one over a limit turns the
  release down. What came out lies in a hidden folder of the library and is gone after the filing.
- **After a Usenet job is filed, nexbooks takes away what it knows, never more.** In the job's own folder (named like the
  release, strictly inside a download root, neither the category folder of the program nor a root) go the files the filing
  read and the rests of a release (`.nfo`, `.par2`, `.sfv`, `.nzb`, `.srr`, pictures, the parts of its archives); anything
  else stays (a film, a text, anything unknown, every link), and the folder goes only when it is empty then. A program that
  reports a folder it shares with others (the category folder itself, a folder with another name) gets nothing taken away
  there, and the person a note under "Braucht dich". The category is checked at every hand-over, not only when the program
  is saved: SABnzbd with job folders off or a sorter for the category gets no job.
- **The hidden folders of nexbooks in a library** (`.nexbooks-trash`, `.nexbooks-incoming`, `.nexbooks-unpack`) are looked
  at themselves before every use: a link, a junction, no folder, or one that leads out of the library, and nothing goes in,
  nothing comes out and nothing is deleted there; the filing stops with a problem (`place_failed`, a link on the way), an
  upload or a trash entry answers `hidden_folder_link`.
- **Nothing is ever written over**, and every path is checked after links are resolved: the file placed lies inside the
  download's folder (or the unpacked one), its goal inside the library, no folder on the way is a link, and a name from a
  book's metadata can neither climb with `..` nor add a `/` (the scheme of tidying makes the names). A file is first written
  under a hidden partial name, then given its name by a hard link (which never replaces anything), then the partial name goes.
- **What does not pass never goes into a library**: the release is blocked for the book, its files leave the download program,
  the wish searches on. A file of the release that is not wanted (a format not ticked, another volume of a bundle) stays out.
- **An upgrade never deletes at once**: what it replaces goes into a hidden trash folder of its library, kept for the days the
  operator set; only the people who manage the library see the trash (with titles), bring back or delete an entry, or
  empty it (the interface asks first). Deleting for good is for a signed-in browser only, never a device token or a key.
- **A note about a reading position is the person's own** (the place was carried over to another format by its share): no
  query reads it for anybody else, the operator included.
- **An upload needs the right `acquire`** in the library and belongs to the person who began it until it is filed: nobody
  else, the operator included, sees it, sends to it or drops it. A name is a path below the upload, checked part by part
  (no `..`, no absolute path, no drive, no hidden name); only book formats, the files beside a book and ZIP are taken; the size
  is checked before a byte is read (500 MB for an e-book, 4 GB for anything else, 20 GB and 1000 files for one upload, the
  free space less a reserve and the room the files on their way may still take) and counted while it comes; the right is looked
  at again with every file; three uploads at a time per person; an unfinished upload goes with its files after a day. The files wait in a hidden folder of the library and are filed like a download.
- The questions of the check (a running time, a bundle, files it does not recognise) are answered by who may ask for books in
  the library, like the download itself; a job nobody asked for is given to a wish by the operator alone.

## Searching and loading releases

- **Only who may acquire, and only for a book they see.** A search names a work of a library the person sees or a book of the
  sources; the person needs the right `acquire` (or `manage`) in a library that takes the kind of book. A search, its
  releases and what is loaded from it are the person's own: another person's search answers like one that is not there.
- **The link of a release is sealed.** A feed gives the link to an NZB or a torrent with the indexer's key or a passkey in it:
  it is kept sealed with the server's secret, never in an answer or a log line, and opened only to load the release. It goes
  to the host the indexer names, under the rules for the link of a release above (a link into a private network behind an
  indexer on the internet is refused with `release_link_private`); a torrent indexer may answer with a magnet link instead.
- **What comes back is checked.** An NZB is XML read with `defusedxml` (no DTD, no entities) and must hold a file; a torrent
  is read by hand, bencoded and strict, and a path in it that could leave its folder (`..`, a slash, a drive, NUL) makes it
  invalid. At most 20 MB (NZB) or 10 MB (torrent).
- **Rationed, per person.** A person has at most three searches waiting or running; a search asks at most four queries of
  two pages per indexer within 150 seconds, paced like every request to an indexer, and goes after a day. One person never
  uses the indexers up for the others:
  - an indexer with a limit of the day gives each person at most a fair share of it (the limit divided by the people who
    asked it today plus one, so that a share is kept for whoever comes next; never less than `NEXBOOKS_INDEXER_SHARE_MIN`,
    10 requests), and the searches of a person's wishes count as theirs; a person whose share is used waits for the next
    day at that indexer (`indexer_share_used`), the others go on;
  - a person makes at most `NEXBOOKS_WISHES_PER_DAY` (100) new wishes and starts at most `NEXBOOKS_SEARCHES_PER_DAY` (60)
    searches by hand a day ("Neu suchen", "Jetzt suchen"); above that `429` (`too_many_wishes_today`,
    `too_many_searches_today`) with `Retry-After` until midnight;
  - the planner and the wishes that wait for a place are served person by person in turn, so a person with hundreds of
    wishes pushes nobody else's back.

  The operator is held to none of these (the limits are his). The counts of a day are kept in memory: a restart forgets
  them, except the wishes, which are counted in the database too; the limit of each indexer holds all the same.
- **Reading starts nothing.** Listing the wishes (also with a key) only reads; the waiting wishes are started by the
  planner and when a search is done. A request that makes no wish says so (`200`, or the failure of the first book).
- **A second click is no second download, and a book loads one release of a kind at a time.** Marking a release as on its way
  and the check for one already on its way are one step, and the database holds it too (a unique index on the book and the
  kind and library for the downloads on their way), so two people or two requests at once cannot hand two releases of one book
  over to one library.
- **The blocklist** is shown and changed only by who may acquire in the library the block was made in (and sees the book);
  blocking and unblocking ask first in the interface. A block is unique per release and book (a unique index), and it outlives the work it was made for: when the
  scanner builds a work anew, the entries that wait under the author and the core title attach to it, and no one else sees
  them meanwhile. A search whose job was given up or lost is marked failed (and holds no place of the person's three).
- **The automatic work reads the same untrusted words with the same rules.** The newest entries of an indexer are names
  from outside: they go through the same parser and judgement as a search (author, title, volume, language, format,
  blocklist), are asked for over the same gate with the same pacing, and one request per reading (three pages at most) is
  all that goes out. An indexer with a limit of the day gives at most half of it to this reading. A wish is taken by one
  statement (the planner and a person who presses "search now" do not search twice), a book loads one release of a kind at
  a time whoever finds it first (the search, the newest entries, a new hand-over after a program did not answer), and a
  wish whose person has lost the right to acquire loads nothing by itself. With the main switch off no way out is used by itself
  (no reading, no search of the planner, no replacement, no hand-over again); only a person's "search now" goes on, and it loads
  nothing. Every request of an indexer takes its place in the count of the day before it is made (in one statement), so searches
  that run at the same moment never pass an indexer's limit; the planner, the newest entries and the search for a better format
  use at most 70 percent of it. A program that is away is asked before the indexer, the file of a release is kept in the data
  folder only until a program has it (two days at most, 100 MB), and an indexer that refused the key is left alone until the key is
  changed. What the operator reads of the automatic work
  (`GET /procure/automation/status`) is counts per indexer: no title, no key, no address.

## The reading stack, the authors and tidying

- **The history is the caller's own.** Every function names the caller; there is none that reads the reading stack of
  somebody else, the operator included. An entry is shown only while the caller still sees its edition. One pass of a book
  makes one entry (two devices reporting the end at once, or a double press, make one; the pass is claimed in a single
  statement).
- **An author is seen through their books.** The grid, the counts, the photo and the page of an author are made of the
  editions the caller may see; an author none of whose books they see is a page that does not exist. A photo from an author's
  folder (`author.jpg`, `folder.jpg`) is kept per library and shown only to its members; it is read below the library's
  folder, never through a link, held to a size and drawn anew.
- **Joining two authors by hand needs the operator, or `manage` in every library of the author's books.** What one person
  decides reaches no library they do not look after, also one they are not a member of. `can_merge` therefore says no for an
  author who has books in such a library as well: that one bit about the other library is taken knowingly. The decision hangs
  on the names, not on a library: when a book of one of the names comes into another library later, the readers there see the
  name that was chosen. The join by the spelling of the names never does that (it joins two names only when they stand in
  exactly the same libraries), and a person who disagrees splits the names again.
- **A photo of an author only when the author is certain, and only with its photographer and licence.** Wikidata (hosts `www.wikidata.org`, `commons.wikimedia.org`,
  `upload.wikimedia.org`, the same fixed table and the same rules as every source) must name an item that is a human with
  the author's name and has, among its works, a book that nexbooks holds; two items that pass are no item. The picture is
  taken only under Creative Commons BY or BY-SA, CC0 or the public domain, and is shown with its photographer and licence.
  No other source gives a photo (a picture without a named author and licence is not kept), and photos that earlier
  versions kept from other sources are removed when the server starts. What goes out is the name of the author and the
  titles of some of their books. One switch (Settings, Sources) turns the look-up off.
- **Tidying writes, so it asks, and it is the operator's alone.** Off from the start; preview first (nothing on the disk
  changes), then the operator's confirmation (a request without `confirm: true` starts nothing), then a job that does exactly
  what the preview showed (its digest is checked again). No file is overwritten (a goal that exists is a problem, a file goes
  by a hard link and the old name is removed, else by a rename that is checked first), no link is followed, nothing leaves
  the library folder, a book moves whole or not at all, every move is written into a journal, a run stopped by a restart goes
  on where it was (the disk decides what was done), and the newest run can be undone. The libraries are read-only from the
  start; where nexbooks may not write, the interface says so and nothing can start.

## Discover

- **What the caller has is decided by what the caller may see.** A list of the sources is public; the plaque "on the shelf", the
  switch that hides what the caller has, the number of books that were hidden, the gaps in a series and the books of an author
  are worked out from the editions of the libraries the caller is a member of (`services/discover/owned.py`), never
  stored with the list and never shared. The shelf of a person is kept in memory between two requests, until anything it is
  made of changes (a write to the editions, their authors, the names of the sources, the libraries, their members or the
  accounts drops every kept shelf); the libraries the caller is a member of are read at every request, so a withdrawn
  membership is never answered from what was kept. A test holds the whole answer of one person equal while another person's libraries
  change.
- **The wish list is the caller's own**: every function takes the caller and reads their rows only; putting a book on it and
  taking it off are idempotent, and the room is counted inside the statement that writes (two requests at once cannot pass a
  check made before).
- **The page of a book that is not on a shelf is reached by a token** nobody can count up to, so a book that only entered the
  table because of the gaps in somebody's series cannot be found by going through numbers. It shows public data and the
  caller's own shelf, nothing of anybody else's.
- **Covers of books that are not on a shelf** are fetched by the server only, from the source's own host (the same table and the
  same way out as every source), up to a size, drawn anew as WebP, and kept in a folder of their own that no scan sweeps; the
  browser never loads a picture from a host of a source (`img-src 'self'`), and no request of a browser makes the server fetch.
  They are there to be shown: a cover and a description of such a book are kept for a day (checked every hour), then removed
  and fetched again when a list or a page needs them. They are in no backup, and what Audible and Apple answered is kept
  for a day as well.
- **The shelves of Browse** are fetched the same way and by the same rules (a day apart, only for markets somebody
  looked at, a few at a time, through the throttle of the source; no job with the main switch off). What goes out is a fixed
  subject out of a table, quoted, a year and a language: **never a text a person typed** (a subject must be letters, digits and a few
  marks, else the question is not made), and the address of Apple's chart is built from a two-letter country and a number. The
  shelves made for a person ("because you read ...") are chosen on the server from their own history and the editions they may
  see; no request goes out for them and nothing of what they read is in any request. Another person's reading, library or
  wish list changes nothing in a person's answer (a test reads the same rows before and after).
- **Fetching is rationed and switchable**: the lists are fetched at most once in twelve hours, only for markets somebody looked
  at, two at a time, through the throttle of the source; with the operator's main switch off no job is queued and a queued one
  asks nothing. A market is looked up in a fixed table, never built from what a person typed.

## E-readers: connections, the catalogue, the sync of KOReader, the sync of a Kobo and mailing to a Kindle

- **A connection is a door of its own, and only a browser makes it.** A person makes a connection for a reading app under
  My account (a secret `nbo_…` of 32 random characters, shown once; the database keeps its SHA-256 and the first characters).
  Making, listing and deleting connections, and setting the address of a Kindle, answer `403 browser_only` to a device token, an
  API key and a connection, so a lost phone cannot make a door that outlives it. The operator switches the feature off for
  everybody (every connection stops at once) and may block one for good; both are read on every request. A block is not
  the person's to lift: they cannot delete the connection, cannot make a new one of that kind while it stands
  (`kind_blocked`), and signing out everywhere leaves it; the operator deletes it.
  "Sign out everywhere", a new password, and the operator signing the account out or blocking it delete every connection
  that is not blocked, as they end its devices: whoever took an account may have made a door for himself. The list of
  books for the Kobo is the person's own and stays; the connections are made anew. Making a connection is told to the
  person as a new sign-in is (a push to their devices and a mail, the same switch, the same limit per hour), never with
  the name they gave it.
- **The catalogue under `/opds` is not a way for a browser.** It answers `GET` only, reads no cookie and sets none, so a page on
  another site, or a session in the same browser, opens nothing there. A reading app signs in with `Authorization: Basic`: the
  name of the account and the secret of a connection of the kind `opds`; the **password of the account is no way in**, and a
  secret of another kind, a device token or an API key is none either. The secret must belong to a connection of that account.
- **Wrong secrets are braked on their own count** (five, then a quarter of an hour; the same wrong secret again counts once), so
  a mistyped reading app shuts nobody out of the sign-in; a connection that exists is looked up before the brake, as for a device
  token. A request without a header only learns how to sign in and counts nothing.
- **It shows what the account sees.** The lists, covers and files come from the same queries and checks as the interface
  (`services/works.py`, `services/authors.py`, `works.file_of`): a book, author, series, cover or file of a library the
  account is not a member of is `404`, and a thing that is not there answers the same. Audiobooks are not in it. Every text
  in a feed is cleaned of what XML cannot hold and escaped; an address names a work, a file or a library by its number and a
  file is `/opds/files/{id}/{id}.{format}`: no folder, no file name and no path of the server is written into a feed or a header.
- **The sync of KOReader under `/kosync` is the same kind of door.** It reads no cookie and takes only `GET` and the one `PUT`
  of the protocol, signs in with two headers of its own (`x-auth-user`, `x-auth-key`) that a page on another site cannot send
  (there is no CORS), and needs no tab header for that reason. The password of the account is no way in; a secret of the
  catalogue is none; wrong keys are braked on a count of their own like the catalogue's. A body is limited to 16 KiB before
  anything reads it. Registration is turned down for everybody (`402`): accounts exist only where a person made them.
- **What is stored of a KOReader connection.** KOReader sends the **MD5 of the password** as its key, which means that
  whoever reads the traffic has a key that works for the sync of that account (not for anything else), as with the official
  sync server. So run nexbooks behind HTTPS. On the server only the **SHA-256 of that MD5** is kept (`token_hash`), never the
  secret and never the MD5 itself, so a copy of the database holds nothing that signs in and nothing from which the secret
  could be worked out. The secret is 32 random characters (192 bits) that no person chooses, which is why a hash that is
  quick to compute is no weakness here; the MD5 only has the length of the key, not of the secret.
- **The sync of a Kobo under `/kobo/<secret>` has its secret in the address.** A Kobo can be given an address and nothing
  else, so the secret of the connection (`nbe_…`, 32 random characters, shown once) is part of every address, and that makes it
  the one secret of nexbooks that travels in a path. What follows from it: it is **kept as a hash** like every other (a copy of
  the database holds nothing that signs in); **no log line carries it** (the request line, the line of a slow request, a
  traceback and a message are masked, `services/logs.py`; uvicorn's own access line is off in the container and never finer than
  a warning); **no answer repeats it** but the ones that hand the device its own addresses (the first call, the sync, the
  metadata), and no error, 404 or refusal does; **nothing redirects** (a catch-all takes an address with a slash too many or
  too few, so no `Location` header holds it); and a reverse proxy in front of nexbooks keeps its own access log, which holds
  the address unless told not to, so keep that log as private as the database. Run nexbooks behind HTTPS: the address is a
  password on the wire. A person who suspects it deletes the connection, which stops it at once.
- **Behind a proxy that ends https, nexbooks must be told, or the Kobo is handed `http` addresses with its secret in them.**
  The addresses the first call hands out are made from the public address the operator set (`NEXBOOKS_PUBLIC_URL` or Settings,
  Sign-in) or, without it, from the address the request came to; the scheme of that is only believed from a proxy named in
  `NEXBOOKS_TRUSTED_PROXIES`. Without either, a Kobo that came over `https` is told `http://...`. The row of the Kobo under My
  account says so in its text, and warns when the page is seen over `https` while nexbooks assumes `http`
  (`kobo_base` in `GET /ereaders`). The `Host` header is taken as it comes when no public address is set, and shown to the owner
  of the connection only (the answers are `no-store`).
- **A Kobo is no browser.** The routes read no cookie and set none, take no `Origin`, and need no tab header, as the secret
  in the address cannot be known to a page on another site. The tokens the device is handed at its sign-in are made up on the
  spot and read by nothing. Wrong secrets are braked on a count of their own (five, then a quarter of an hour; the same wrong
  one counts once; a connection that exists is looked up first and never turned away by it), a body is limited to 32 KiB before
  anything reads it, and a flood of requests is braked per account. A blocked or deleted connection, a blocked
  account, and the operator's switch are read on every request and take effect at once. The lock after wrong passwords is for the
  sign-in with the password only: a connection has a secret of its own, so a stranger who knows a name cannot cut off the
  e-readers (or the devices and keys) of the account that way.
- **A Kobo reaches the list of its person and nothing else.** Only the e-books the person put on their list
  (`PUT /editions/{id}/kobo`, browser only) are handed out, as the EPUB that lies in the library (copy-protected ones cannot be
  put there; nothing is converted). The rights are read again on **every** request: a book of a library the person has left,
  a file that is gone and a protection found afterwards are told away at the next sync and answer `404` for the metadata, the
  place, the file and the cover, the same `404` as a name nobody has; a book told away carries no title. A book's name is a
  UUID made from the person and the edition, so one person's name opens nothing for another. The place a Kobo reports is the
  person's own, kept by the rule of every other report, and a Kobo is told only the places of its own person.
- **nexbooks passes nothing on to Kobo.** nexbooks calls no server of Kobo. The device is handed the table Kobo's store hands
  out, with every address of the store's server and of its pictures changed to nexbooks; the rest of the table (web pages, the
  sign-in of the shop) stays as it is and never holds the secret. A route nexbooks has no use of answers empty. The price is
  that the Kobo store and Kobo's own services do not work while the device points at nexbooks.
- **A book is looked up among what the account sees.** The checksum KOReader names a book by (the partial MD5 of the EPUB)
  is searched only in the libraries the account is a member of; a book of another library, or one nobody has, is answered
  alike: a report is accepted and dropped, a question has no answer. A checksum is not a secret, but it is not a way to learn
  that a book exists. The place of a person is theirs alone, as everywhere: the sync reads and writes only the places of the
  account that made the connection.
- **Mailing an EPUB to a Kindle** goes through the operator's mail server, in the background, from a button on the book. The
  rights are those of the download, and are read once more when the job runs (a membership or the address may have changed in
  between). Only an EPUB without copy protection and at most 50 MB goes. The address must end in `@kindle.com` with a plain
  name before it (nothing that could carry a second address or a line break), it is set in a browser only, and the name of
  the attachment holds no path. Mailing is braked per person (6 a minute, 30 an hour; 3 test mails a minute) and one book waiting
  twice is one mail. The log names the kind of failure of the mail server, never an address. A mail that never goes, after all
  its tries, is told to the person under "Braucht dich" with the reason (a note of their own, which they click away).
