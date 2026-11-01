"""Invented books for trying the reader in a browser: a long novel with a nested table of contents, an EPUB 2 with an NCX, a
book with pages of fixed layout, one that reads from right to left, one in vertical Japanese, a MOBI, a PDF with an outline,
books that are copy-protected, one that lies about its size, and a hostile one that tries everything a book should not be able to do.

A development tool; it is not part of the image. The text is made from a small list of invented words, so that a search has
something to find and nothing is a real book.

    python tools/make_reader_books.py --out <folder> [--canary http://127.0.0.1:18588]

``--canary``: the address the hostile book tries to reach (a listener of the test, which counts every visit).
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from tests import bookmaker

WORDS = (
    "the lantern harbour morning slowly across quiet water while gulls turned over grey roofs and a boat with patched sails "
    "waited beside the old pier where nets hung drying in the wind she carried a letter folded small inside her coat and "
    "walked past the market the baker the clockmaker the shop of ropes toward the lighthouse that nobody had lit in years "
    "behind her the bell of the chapel counted eleven strokes each answered by a dog somewhere far up the hill"
).split()
NAMES = ("Ada", "Ben", "Clara", "Jonas", "Iris", "Theo", "Greta", "Mara")


def sentence(seed: int, length: int = 14) -> str:
    words = [WORDS[(seed * 7 + i * 3 + seed // 5) % len(WORDS)] for i in range(length)]
    words[0] = words[0].capitalize()
    if seed % 6 == 0:
        words[3] = NAMES[seed % len(NAMES)]
    return " ".join(words) + "."


def paragraph(seed: int, sentences: int = 6) -> str:
    return " ".join(sentence(seed * 10 + n, 9 + (seed + n) % 8) for n in range(sentences))


def chapter_html(title: str, number: int, paragraphs: int = 12, extra: str = "") -> str:
    body = "".join(f"<p>{paragraph(number * 100 + n)}</p>" for n in range(paragraphs))
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>' + title + "</title></head><body>"
        f'<h1 id="top">{title}</h1>{body}{extra}</body></html>'
    )


def png(color: tuple[int, int, int], size: tuple[int, int] = (240, 160)) -> bytes:
    image = Image.new("RGB", size, color)
    if min(size) > 30:
        ImageDraw.Draw(image).rectangle([10, 10, size[0] - 10, size[1] - 10], outline=(255, 255, 255), width=4)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def write_epub(path: Path, *, title: str, author: str, language: str, files: dict[str, bytes | str], opf_manifest: str,
               opf_spine: str, version: str = "3.0", extra_meta: str = "", spine_attrs: str = "",
               extra_entries: dict[str, bytes | str] | None = None, guide: str = "") -> Path:
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" unique-identifier="bookid" '
        f'version="{version}"><metadata><dc:identifier id="bookid">urn:uuid:5b6f1c9e-0000-4000-8000-{sum(map(ord, title)) % 10**12:012d}'
        f"</dc:identifier><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:language>{language}</dc:language>"
        f"{extra_meta}</metadata><manifest>{opf_manifest}</manifest><spine{spine_attrs}>{opf_spine}</spine>{guide}</package>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", bookmaker.CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        for name, data in files.items():
            archive.writestr("OEBPS/" + name, data)
        for name, data in (extra_entries or {}).items():
            archive.writestr(name, data)
    return path


def nav(items: list[tuple[str, str, list[tuple[str, str]]]]) -> str:
    """A navigation document; each item is (title, href, children)."""
    def li(title: str, href: str, children: list[tuple[str, str]]) -> str:
        inner = ""
        if children:
            inner = "<ol>" + "".join(f'<li><a href="{h}">{t}</a></li>' for t, h in children) + "</ol>"
        return f'<li><a href="{href}">{title}</a>{inner}</li>'
    return (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol>'
        + "".join(li(*item) for item in items) + "</ol></nav></body></html>"
    )


def long_novel(path: Path) -> Path:
    """The Long Road: three parts of four chapters, a picture, a note, an internal and an external link."""
    files: dict[str, bytes | str] = {}
    manifest: list[str] = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                           '<item id="pic" href="images/harbour.png" media-type="image/png"/>',
                           '<item id="css" href="style.css" media-type="text/css"/>']
    spine: list[str] = []
    toc: list[tuple[str, str, list[tuple[str, str]]]] = []
    files["images/harbour.png"] = png((92, 60, 70))
    files["style.css"] = "p { margin: 0 0 .6em; } h1 { font-size: 1.6em; margin: 1.2em 0 .6em; } .note { font-size: .85em; }"
    number = 0
    for part in range(1, 4):
        children: list[tuple[str, str]] = []
        for chapter in range(1, 5):
            number += 1
            name = f"text/c{number:02d}.xhtml"
            title = f"Chapter {number}: {['The Pier', 'A Letter', 'The Clockmaker', 'Eleven Strokes'][chapter - 1]}"
            extra = ""
            if number == 2:
                extra = ('<p><img src="../images/harbour.png" alt="The harbour"/></p>'
                         '<p>See <a href="c05.xhtml#top">chapter 5</a> and <a href="https://example.com/notes">the notes</a>.</p>')
            if number == 3:
                extra = '<p class="note" id="note1">The lighthouse stands where the old chapel stood.</p>'
            html = chapter_html(title, number, 12, extra).replace("</head>", '<link rel="stylesheet" href="../style.css"/></head>')
            files[name] = html
            manifest.append(f'<item id="c{number}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{number}"/>')
            children.append((title, name))
        toc.append((f"Part {part}", children[0][1], children))
    files["nav.xhtml"] = nav(toc)
    return write_epub(path, title="The Long Road", author="Mara Lindqvist", language="en", files=files,
                      opf_manifest="".join(manifest), opf_spine="".join(spine))


def german_ncx(path: Path) -> Path:
    files: dict[str, bytes | str] = {}
    manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    spine = []
    points = []
    for number in range(1, 7):
        name = f"text/k{number}.xhtml"
        title = f"Kapitel {number}"
        files[name] = chapter_html(title, 50 + number, 8)
        manifest.append(f'<item id="k{number}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="k{number}"/>')
        points.append(f'<navPoint id="n{number}" playOrder="{number}"><navLabel><text>{title}</text></navLabel>'
                      f'<content src="{name}"/></navPoint>')
    files["toc.ncx"] = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
        + "".join(points) + "</navMap></ncx>")
    return write_epub(path, title="Das alte Boot", author="Jonas Brandauer", language="de", files=files, version="2.0",
                      opf_manifest="".join(manifest), opf_spine="".join(spine), spine_attrs=' toc="ncx"')


def fixed_layout(path: Path) -> Path:
    files: dict[str, bytes | str] = {}
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine = []
    colors = [(140, 60, 70), (60, 90, 140), (70, 120, 90), (160, 130, 60)]
    for number, color in enumerate(colors, 1):
        files[f"images/p{number}.png"] = png(color, (600, 800))
        files[f"page{number}.xhtml"] = (
            '<?xml version="1.0"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Page</title>'
            '<meta name="viewport" content="width=600, height=800"/></head><body style="margin:0">'
            f'<img src="images/p{number}.png" width="600" height="800" alt="Page {number}"/></body></html>')
        manifest.append(f'<item id="p{number}" href="page{number}.xhtml" media-type="application/xhtml+xml"/>')
        manifest.append(f'<item id="i{number}" href="images/p{number}.png" media-type="image/png"/>')
        spine.append(f'<itemref idref="p{number}"/>')
    files["nav.xhtml"] = nav([(f"Page {n}", f"page{n}.xhtml", []) for n in range(1, 5)])
    meta = '<meta property="rendition:layout">pre-paginated</meta>'
    return write_epub(path, title="Four Pages", author="Iris Quist", language="en", files=files,
                      opf_manifest="".join(manifest), opf_spine="".join(spine), extra_meta=meta)


def right_to_left(path: Path) -> Path:
    files: dict[str, bytes | str] = {}
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine = []
    arabic = "كان يا ما كان في قديم الزمان ميناء صغير على حافة البحر وكانت السفن تأتي وتذهب مع الريح " * 12
    for number in range(1, 4):
        name = f"c{number}.xhtml"
        paragraphs = "".join(f"<p>{arabic}</p>" for _ in range(6))
        files[name] = ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
                       f'xml:lang="ar" dir="rtl"><head><title>{number}</title></head><body><h1>الفصل {number}</h1>{paragraphs}</body></html>')
        manifest.append(f'<item id="c{number}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')
    files["nav.xhtml"] = nav([(f"الفصل {n}", f"c{n}.xhtml", []) for n in range(1, 4)])
    return write_epub(path, title="الميناء", author="Test Author", language="ar", files=files,
                      opf_manifest="".join(manifest), opf_spine="".join(spine), spine_attrs=' page-progression-direction="rtl"')


def vertical(path: Path) -> Path:
    files: dict[str, bytes | str] = {}
    text = "港の朝は静かで、灰色の屋根の上をかもめが回っていた。古い桟橋のそばで、継ぎのあたった帆の船が風を待っていた。" * 30
    files["c1.xhtml"] = ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">'
                         '<head><title>1</title><style>html{writing-mode:vertical-rl}</style></head>'
                         f"<body><h1>第一章</h1><p>{text}</p><p>{text}</p></body></html>")
    files["nav.xhtml"] = nav([("第一章", "c1.xhtml", [])])
    manifest = ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>')
    return write_epub(path, title="港の朝", author="Test Author", language="ja", files=files, opf_manifest=manifest,
                      opf_spine='<itemref idref="c1"/>', spine_attrs=' page-progression-direction="rtl"')


def protected_epub(path: Path) -> Path:
    files = {"c1.xhtml": chapter_html("Locked", 1, 3), "nav.xhtml": nav([("Locked", "c1.xhtml", [])])}
    manifest = ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>')
    encryption = ('<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                  'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>'
                  '<enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/><enc:CipherData>'
                  '<enc:CipherReference URI="OEBPS/c1.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>')
    return write_epub(path, title="Locked Room", author="Test Author", language="en", files=files, opf_manifest=manifest,
                      opf_spine='<itemref idref="c1"/>',
                      extra_entries={"META-INF/encryption.xml": encryption, "META-INF/rights.xml": "<rights/>"})


def hostile(path: Path, canary: str) -> Path:
    """Everything a book should not be able to do. Each attempt leaves a mark if it works: the page's own address (a script
    that ran would write ``pwned`` into ``document.title`` and ``window.name`` and try the parent), or a visit to ``canary``."""
    script = (
        "document.title='pwned';window.name='pwned';document.documentElement.setAttribute('data-pwned','1');"
        "try{window.parent.document.title='pwned';window.parent.name='pwned'}catch(e){}"
        "try{window.top.location='https://example.com/top'}catch(e){}"
        f"try{{fetch('{canary}/fetch')}}catch(e){{}}"
        f"try{{new Image().src='{canary}/image-script'}}catch(e){{}}"
        f"try{{navigator.sendBeacon('{canary}/beacon','x')}}catch(e){{}}"
        f"try{{var w=new WebSocket('ws://127.0.0.1:1/x')}}catch(e){{}}"
    )
    evil = (
        '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><head><title>Hostile</title>'
        f'<base href="{canary}/base/"/>'
        f'<meta http-equiv="refresh" content="0;url={canary}/refresh"/>'
        f'<link rel="stylesheet" href="{canary}/style.css"/>'
        f'<link rel="prefetch" href="{canary}/prefetch"/>'
        "<script>" + script + "</script>"
        f'<script src="{canary}/external.js"></script>'
        '<script src="evil.js"></script>'
        f"<style>@import url('{canary}/import.css');body{{background:url({canary}/bg.png)}}"
        f"@font-face{{font-family:X;src:url({canary}/font.woff2)}}p.x{{behavior:url(x.htc);-moz-binding:url(x)}}</style>"
        "</head>"
        f"<body onload=\"document.title='pwned';fetch('{canary}/onload')\">"
        "<h1>Hostile chapter</h1><p>This chapter is made to do harm and should show only this text.</p>"
        f'<img src="{canary}/image.png" onerror="document.title=\'pwned\'" alt="x"/>'
        f'<img src="x" onerror="document.title=\'pwned\';fetch(\'{canary}/onerror\')" alt="y"/>'
        f'<img src="big.png" alt="a picture that says it has billions of pixels"/>'
        f'<iframe src="{canary}/frame"></iframe><iframe srcdoc="&lt;script&gt;parent.document.title=\'pwned\'&lt;/script&gt;"></iframe>'
        f'<object data="{canary}/object"></object><embed src="{canary}/embed"/>'
        f'<form action="{canary}/form" method="post"><input name="a" value="b"/><button>Go</button></form>'
        f'<video src="{canary}/video.mp4" autoplay="autoplay"></video><audio src="{canary}/audio.mp3" autoplay="autoplay"></audio>'
        '<p><a href="javascript:document.title=\'pwned\'" id="js">javascript link</a> '
        f'<a href="{canary}/click" id="out" target="_top">outside link</a> '
        '<a href="data:text/html,&lt;script&gt;1&lt;/script&gt;" id="datalink">data link</a> '
        '<a href="c2.xhtml#top" id="in">next chapter</a></p>'
        '<p style="background:url(http://127.0.0.1:1/inline.png);width:expression(alert(1))" id="styled">styled</p>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><script>document.title=\'pwned\'</script>'
        '<a xlink:href="javascript:document.title=\'pwned\'"><circle cx="20" cy="20" r="10" onclick="document.title=\'pwned\'"/></a>'
        f'<image xlink:href="{canary}/svg-image.png"/><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">'
        '<script>document.title=\'pwned\'</script></div></foreignObject></svg>'
        "</body></html>"
    )
    svg_attack = ('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50"><script>document.title="pwned"</script>'
                  '<rect width="50" height="50" fill="red" onload="document.title=\'pwned\'"/></svg>')
    # A picture whose header says 60,000 x 60,000 pixels, in a few hundred bytes.
    big = bytearray(png((10, 10, 10), (4, 4)))
    big[16:20] = struct.pack(">I", 60000)
    big[20:24] = struct.pack(">I", 60000)
    files: dict[str, bytes | str] = {
        "c1.xhtml": evil,
        "c2.xhtml": chapter_html("Second chapter", 2, 3),
        "evil.js": "document.title='pwned'",
        "attack.svg": svg_attack,
        "big.png": bytes(big),
        "nav.xhtml": nav([("Hostile chapter", "c1.xhtml", []), ("Second chapter", "c2.xhtml", [])]),
    }
    manifest = ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml" properties="scripted"/>'
                '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="js" href="evil.js" media-type="text/javascript"/>'
                '<item id="svg" href="attack.svg" media-type="image/svg+xml"/>'
                '<item id="big" href="big.png" media-type="image/png"/>')
    return write_epub(path, title="Hostile Pages", author="Nobody", language="en", files=files, opf_manifest=manifest,
                      opf_spine='<itemref idref="c1"/><itemref idref="c2"/><itemref idref="svg" linear="no"/>')


def lying_bomb(path: Path) -> Path:
    """An EPUB whose first chapter says in its headers that it holds 2 KB and holds 120 MB (of one letter, so the file itself is
    a few hundred kilobytes). Every check of the headers passes; only unpacking it shows what it is."""
    big = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Big</title></head><body><p>' + "A" * 120_000_000 + "</p></body></html>"
    files = {"c1.xhtml": big, "nav.xhtml": nav([("Big", "c1.xhtml", [])])}
    manifest = ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>')
    write_epub(path, title="Heavy Reading", author="Nobody", language="en", files=files, opf_manifest=manifest,
               opf_spine='<itemref idref="c1"/>')
    data = bytearray(path.read_bytes())
    claimed = 2000
    # The central directory entry (signature PK 01 02, the size at offset 24) and the local header (PK 03 04, offset 22).
    first = data.index(b"OEBPS/c1.xhtml")  # in the local header
    last = data.rindex(b"OEBPS/c1.xhtml")  # in the central directory
    local = data.rindex(b"PK\x03\x04", 0, first)
    central = data.rindex(b"PK\x01\x02", 0, last)
    data[central + 24:central + 28] = struct.pack("<I", claimed)
    data[local + 22:local + 26] = struct.pack("<I", claimed)
    path.write_bytes(bytes(data))
    return path


def mobi6(path: Path, title: str = "Kite Season", chapters: int = 6) -> Path:
    """A MOBI 6 that opens: sections split at the page breaks, no compression, no table of contents."""
    html = ""
    for number in range(1, chapters + 1):
        html += f"<html><head></head><body><h2>Chapter {number}</h2>" + "".join(
            f"<p>{paragraph(200 + number * 10 + n)}</p>" for n in range(10)) + "</body></html><mbp:pagebreak/>"
    text = html.encode("utf-8")
    pieces = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    name = title.encode("utf-8")
    header_length = 232
    mobi = bytearray(header_length)
    mobi[0:4] = b"MOBI"
    mobi[4:8] = struct.pack(">I", header_length)
    mobi[8:12] = struct.pack(">I", 2)
    mobi[12:16] = struct.pack(">I", 65001)
    mobi[20:24] = struct.pack(">I", 6)
    full_name_offset = 16 + header_length
    mobi[68:72] = struct.pack(">I", full_name_offset)
    mobi[72:76] = struct.pack(">I", len(name))
    mobi[76:80] = struct.pack(">I", 0x07)
    mobi[92:96] = struct.pack(">I", 0xFFFFFFFF)
    mobi[96:100] = struct.pack(">I", 0xFFFFFFFF)
    mobi[112:116] = struct.pack(">I", 0)
    mobi[116:120] = struct.pack(">I", 0)
    mobi[228:232] = struct.pack(">I", 0xFFFFFFFF)
    palmdoc = struct.pack(">HHIHHHH", 1, 0, len(text), len(pieces), 4096, 0, 0)
    record0 = palmdoc + bytes(mobi) + name + b"\x00\x00"
    records = [record0, *pieces]
    offset = 78 + 8 * len(records) + 2
    table = b""
    position = offset
    for record in records:
        table += struct.pack(">II", position, 0)
        position += len(record)
    header = bytearray(b"Kite Season".ljust(32, b"\x00") + b"\x00" * 28 + b"BOOKMOBI" + b"\x00" * 8 + b"\x00\x00")
    header = bytes(header[:76]) + struct.pack(">H", len(records))
    path.write_bytes(header + table + b"\x00\x00" + b"".join(records))
    return path


def text_pdf(path: Path, pages: int = 8) -> Path:
    """A PDF with real text on every page and an outline of three entries (written by hand, no library)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for number in range(1, pages + 1):
        lines = [f"BT /F1 12 Tf 60 740 Td 18 TL (Page {number} of the field notes) Tj T*"]
        for line in range(30):
            lines.append(f"({sentence(number * 31 + line, 11)}) Tj T*")
        lines.append("ET")
        stream = "\n".join(lines).encode("latin-1", "replace")
        content = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 595 842] /Contents {content} 0 R "
                            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()))
    outline_root = add(b"")
    entries = [("Start", page_ids[0]), ("Middle", page_ids[pages // 2]), ("End", page_ids[-1])]
    entry_ids = [add(b"") for _ in entries]
    for index, ((title, page), entry) in enumerate(zip(entries, entry_ids, strict=True)):
        prev = f"/Prev {entry_ids[index - 1]} 0 R " if index else ""
        nxt = f"/Next {entry_ids[index + 1]} 0 R " if index < len(entries) - 1 else ""
        objects[entry - 1] = (f"<< /Title ({title}) /Parent {outline_root} 0 R {prev}{nxt}/Dest [{page} 0 R /Fit] >>").encode()
    objects[outline_root - 1] = f"<< /Type /Outlines /First {entry_ids[0]} 0 R /Last {entry_ids[-1]} 0 R /Count {len(entries)} >>".encode()
    objects[pages_obj - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {pages} >>").encode()
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R /Outlines {outline_root} 0 R >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def late_start(path: Path) -> Path:
    """An EPUB 2 whose guide names as "the start of the text" the page of its cover, which stands last, after the imprint
    (as a real book did): the reader opens it at its first page, not at 100 %."""
    files: dict[str, bytes | str] = {}
    manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    spine = []
    points = []
    names = [("title", "Titelei")] + [(f"k{number}", f"Kapitel {number}") for number in range(1, 9)] + [
        ("imprint", "Impressum"), ("cover", "Umschlag")]
    for number, (key, title) in enumerate(names, start=1):
        name = f"Text/{key}.html"
        files[name] = chapter_html(title, 70 + number, 6)
        manifest.append(f'<item id="{key}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{key}"/>')
        points.append(f'<navPoint id="n{number}" playOrder="{number}"><navLabel><text>{title}</text></navLabel>'
                      f'<content src="{name}"/></navPoint>')
    files["toc.ncx"] = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
        + "".join(points) + "</navMap></ncx>")
    guide = ('<guide><reference href="Text/imprint.html" title="Impressum" type="copyright-page"/>'
             '<reference href="Text/title.html" title="Titelei" type="title-page"/>'
             '<reference href="Text/cover.html" title="Textanfang" type="text"/></guide>')
    return write_epub(path, title="Der spaete Anfang", author="Ida Brandt", language="de", files=files, version="2.0",
                      opf_manifest="".join(manifest), opf_spine="".join(spine), spine_attrs=' toc="ncx"', guide=guide)


def plain_page(title: str, number: int, paragraphs: int = 8) -> str:
    """A page of an EPUB 2 as a program that writes ``.xml`` or ``.htm`` files writes it."""
    return chapter_html(title, number, paragraphs)


def hand_written_epub(path: Path, *, title: str, opf_path: str, pages: list[tuple[str, str | None, str, str]],
                      language: str = "en", end_record_count: int | None = None, spine_extra: str = "") -> Path:
    """An EPUB 2 put together by hand: the package where ``opf_path`` says, an empty ``dc:creator`` beside the real one, and
    pages given as (address in the manifest, name of the entry in the archive or None for a part the book names and does
    not hold, title, media type). ``end_record_count`` writes that count into the "entries on this disk" field of the end
    record (some programs write twice the true count there)."""
    folder = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    spine = []
    points = []
    for number, (href, _entry, page_title, media) in enumerate(pages, start=1):
        manifest.append(f'<item id="p{number}" href="{href}" media-type="{media}"/>')
        spine.append(f'<itemref idref="p{number}"/>')
        points.append(f'<navPoint id="n{number}" playOrder="{number}"><navLabel><text>{page_title}</text></navLabel>'
                      f'<content src="{href}"/></navPoint>')
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid"><metadata>'
        f'<dc:title>{title}</dc:title><dc:language>{language}</dc:language><dc:identifier id="bookid">urn:test:{sum(map(ord, title))}</dc:identifier>'
        '<dc:creator opf:role="aut">Rune Falkner</dc:creator><dc:creator opf:role="bkp"></dc:creator>'
        f'</metadata><manifest>{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine></package>')
    ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
           + "".join(points) + "</navMap></ncx>")
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        f'<rootfile full-path="{opf_path}" media-type="application/oebps-package+xml"/></rootfiles></container>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr(opf_path, opf)
        archive.writestr(folder + "toc.ncx", ncx)
        for number, (_href, entry, page_title, _media) in enumerate(pages, start=1):
            if entry is not None:
                archive.writestr(folder + entry, plain_page(page_title, 90 + number))
    if end_record_count is not None:
        data = bytearray(path.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        data[end + 8:end + 10] = struct.pack("<H", end_record_count)
        path.write_bytes(bytes(data))
    return path


def escaped_names(path: Path) -> Path:
    """The package writes the addresses of its pages as URLs (``Rune%20Falkner%20-%20Part%2C%20One``); the archive holds the plain
    names, with spaces and a comma in them, and the pages end in ``.htm``. The reader finds every one of them."""
    pages = [("Text/cover.xhtml", "Text/cover.xhtml", "Cover", "application/xhtml+xml")]
    for number, name in enumerate(("One", "Two", "Three", "Four"), start=1):
        pages.append((f"Text/Rune%20Falkner%20-%20Part%2C%20{name}_split_00{number}.htm",
                      f"Text/Rune Falkner - Part, {name}_split_00{number}.htm", f"Part {name}", "application/xhtml+xml"))
    return hand_written_epub(path, title="Names With Commas", opf_path="OEBPS/content.opf", pages=pages)


def loose_end_record(path: Path) -> Path:
    """The pages are ``.xml`` files the manifest types as XHTML, the package lies in the root of the archive under a name of its
    own, and the end record counts the entries on this disk twice as high as it should."""
    names = ("one", "two", "three", "four", "five")
    pages = [(f"falkner-pier-{name}.xml", f"falkner-pier-{name}.xml", f"Pier {name.capitalize()}", "application/xhtml+xml")
             for name in names]
    return hand_written_epub(path, title="Notes From The Pier", opf_path="inhalt.opf", pages=pages,
                             end_record_count=2 * (len(pages) + 4))


def missing_parts(path: Path) -> Path:
    """A book that names five pages and holds three: the reader shows what is there, says that part is missing and does
    not run to 100 % at the last page that has text."""
    pages = []
    for number, name in enumerate(("One", "Two", "Three", "Four", "Five"), start=1):
        held = number in (1, 2, 3)
        pages.append((f"Text/part-{number}.xhtml", f"Text/part-{number}.xhtml" if held else None, f"Missing {name}", "application/xhtml+xml"))
    return hand_written_epub(path, title="Parts Gone Astray", opf_path="OEBPS/content.opf", pages=pages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--canary", default="http://127.0.0.1:18588")
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    long_novel(out / "the-long-road.epub")
    german_ncx(out / "das-alte-boot.epub")
    late_start(out / "der-spaete-anfang.epub")
    escaped_names(out / "names-with-commas.epub")
    loose_end_record(out / "notes-from-the-pier.epub")
    missing_parts(out / "parts-gone-astray.epub")
    fixed_layout(out / "four-pages.epub")
    right_to_left(out / "port-ar.epub")
    vertical(out / "port-ja.epub")
    protected_epub(out / "locked-room.epub")
    hostile(out / "hostile-pages.epub", args.canary)
    lying_bomb(out / "heavy-reading.epub")
    mobi6(out / "kite-season.mobi")
    bookmaker.make_mobi(out / "kindle-locked.azw3", title="Kindle Locked", encryption=2)
    # A Kobo book: a rights file in the root of the archive and chapters that are noise, no encryption file.
    bookmaker.make_epub(out / "kobo-locked.epub", title="Kobo Locked", authors=("Test Author",), language="en",
                        extra_entries={"rights.xml": b"<?xml version='1.0'?><kdrm><sign>AAAA</sign></kdrm>"},
                        scrambled=(0, 1, 2))
    text_pdf(out / "field-notes.pdf")
    # The scan leaves a file that was changed a moment ago for its next run: these are old enough.
    stamp = time.time() - 3600
    for each in out.iterdir():
        os.utime(each, (stamp, stamp))
    print(f"{len(list(out.iterdir()))} books in {out}")


if __name__ == "__main__":
    main()
