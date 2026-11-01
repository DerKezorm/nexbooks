"""A bound on how deep the XML of a source may nest, checked before it is parsed.

``defusedxml`` refuses a DTD and entities, but a document of two megabytes nested element in element costs seconds and
hundreds of megabytes to parse (measured: 2 to 2.6 s and about 300 MB). The answers of the libraries nest seven or
eight deep (an SRU answer, its records, MARC fields and subfields); one that nests deeper than ``MAX_DEPTH`` is no
answer of theirs and is refused before the parser sees it. The scan stops at the first tag too deep, so a hostile
document costs next to nothing.

A CDATA section is text, whatever it holds: the German library writes the abstracts of theses in one, with ``<br>`` in
them (such an answer was refused once, and with it every book of a search for a famous scientist). It is passed
over as a whole; one that never ends is no answer.
"""

from __future__ import annotations

import re

MAX_DEPTH = 64
#: A tag: opening, closing (``</``) or empty (``/>``). Comments, processing instructions and character data do not
#: start with a letter and are passed over.
_TAG = re.compile(rb"<(/?)[A-Za-z_][^<>]*?(/?)>")
_CDATA_START = b"<![CDATA["
_CDATA_END = b"]]>"


def too_deep(xml: bytes, limit: int = MAX_DEPTH) -> bool:
    """Whether the elements of ``xml`` nest deeper than ``limit`` (or a CDATA section never ends)."""
    depth = 0
    at = 0
    while True:
        cdata = xml.find(_CDATA_START, at)
        stop = cdata if cdata >= 0 else len(xml)
        for found in _TAG.finditer(xml, at, stop):
            if found.group(1):
                depth -= 1
            elif not found.group(2):
                depth += 1
                if depth > limit:
                    return True
        if cdata < 0:
            return False
        end = xml.find(_CDATA_END, cdata + len(_CDATA_START))
        if end < 0:
            return True
        at = end + len(_CDATA_END)


__all__ = ["MAX_DEPTH", "too_deep"]
