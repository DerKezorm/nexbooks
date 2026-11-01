"""The stand-in of the sources for the searches of the catalogue (``fixtures/search/answers.json``): each source answers
the words of a search with what the services answered to such a search (the shapes recorded, the books invented).

A search is found by its words: Open Library's ``q`` (and the language its edition is asked in), the German National
Library's ``woe`` words (and the volume asked for in the title), Audible's ``keywords`` in a market. A question no search
holds is answered with nothing, as a service answers a search it finds nothing for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import httpx

from app.services.readers.text import fold

from .fake_sources import FakeSources, host_of, params_of

ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "search" / "answers.json").read_text(encoding="utf-8"))
MARC_LANGUAGE = {"ger": "de", "eng": "en", "fre": "fr"}


def words_of(text: str) -> frozenset[str]:
    return frozenset(word for word in fold(text).split() if len(word) >= 2)


def marc(records: list[dict[str, Any]]) -> bytes:
    """Records of the German National Library as the library sends them (MARC21-xml in an SRU answer)."""
    body = []
    for index, item in enumerate(records, start=1):
        fields = [f'<leader>00000n{"i" if item["audio"] else "a"}m a2200000 c 4500</leader>',
                  f'<controlfield tag="001">{escape(item["id"])}</controlfield>',
                  f'<controlfield tag="007">{"sd" if item["audio"] else "tu"}</controlfield>']
        language = f'<subfield code="a">{item["language"]}</subfield>'
        if item["translated"]:
            language += f'<subfield code="h">{item["translated"]}</subfield>'
        if item["language"]:
            # (A record that names no language has no 041.)
            fields.append(f'<datafield tag="041" ind1=" " ind2=" ">{language}</datafield>')
        if item["isbn"]:
            fields.append(f'<datafield tag="020" ind1=" " ind2=" "><subfield code="a">{item["isbn"]}</subfield>'
                          '</datafield>')
        fields.append(f'<datafield tag="082" ind1="0" ind2="4"><subfield code="a">{item["group"]}</subfield>'
                      '</datafield>')
        number = f'<subfield code="0">(DE-588){item["gnd"]}</subfield>' if item.get("gnd") else ""
        fields.append(f'<datafield tag="100" ind1="1" ind2=" "><subfield code="a">{escape(item["author"])}</subfield>'
                      f'{number}<subfield code="4">aut</subfield></datafield>')
        if item["original"]:
            fields.append(f'<datafield tag="240" ind1="1" ind2="0"><subfield code="a">{escape(item["original"])}'
                          '</subfield></datafield>')
        title = f'<subfield code="a">{escape(item["title"])}</subfield>'
        if item["subtitle"]:
            title += f'<subfield code="b">{escape(item["subtitle"])}</subfield>'
        if item.get("made_by"):
            title += f'<subfield code="c">{escape(item["made_by"])}</subfield>'
        if item.get("part_number"):
            title += f'<subfield code="n">{escape(item["part_number"])}</subfield>'
        if item.get("part_name"):
            title += f'<subfield code="p">{escape(item["part_name"])}</subfield>'
        fields.append(f'<datafield tag="245" ind1="1" ind2="0">{title}</datafield>')
        fields.append(f'<datafield tag="264" ind1=" " ind2="1"><subfield code="c">{item["year"]}</subfield>'
                      '</datafield>')
        if item["pages"]:
            fields.append(f'<datafield tag="300" ind1=" " ind2=" "><subfield code="a">{item["pages"]} S.</subfield>'
                          '</datafield>')
        if item["series"]:
            fields.append(f'<datafield tag="490" ind1="1" ind2=" "><subfield code="a">{escape(item["series"])}'
                          f'</subfield><subfield code="v">{item["position"]}</subfield></datafield>')
        if item.get("whole"):
            fields.append(f'<datafield tag="800" ind1="1" ind2=" "><subfield code="a">{escape(item["author"])}'
                          f'</subfield><subfield code="t">{escape(item["whole"])}</subfield>'
                          f'<subfield code="v">{item["position"]}</subfield></datafield>')
        if item.get("part_number") or item.get("part_name"):
            fields.append('<datafield tag="773" ind1="0" ind2="8"><subfield code="w">(DE-101)0</subfield></datafield>')
        body.append(f'<record><recordData><record xmlns="http://www.loc.gov/MARC21/slim">{"".join(fields)}</record>'
                    f'</recordData><recordPosition>{index}</recordPosition></record>')
    return ('<?xml version="1.0" encoding="UTF-8"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            f'<version>1.1</version><numberOfRecords>{len(records)}</numberOfRecords><records>{"".join(body)}'
            '</records></searchRetrieveResponse>').encode()


class SearchSources(FakeSources):
    """``FakeSources`` that answers the searches by words from the recorded answers; everything else as before."""

    def __init__(self) -> None:
        super().__init__()
        self.searches = ANSWERS["searches"]
        self.sets = ANSWERS["sets"]
        #: Seconds a host waits before it answers (a slow source), by the start of its name.
        self.slow: dict[str, float] = {}
        self.wait: Any = None

    def _search(self, text: str) -> dict[str, Any] | None:
        wanted = words_of(text)
        return next((search for search in self.searches if words_of(search["q"]) == wanted), None)

    def _set(self, name: str) -> list[Any]:
        return list(self.sets.get(name, [])) if name else []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host, path, params = host_of(request), request.url.path, params_of(request)
        for start, seconds in self.slow.items():
            if host.startswith(start) and self.wait is not None:
                self.wait(seconds)
        if host in self.status:
            return super().handle_request(request)
        if host == "openlibrary.org" and path == "/search.json" and "q" in params:
            self.log.append(request)
            search = self._search(params["q"][0])
            language = MARC_LANGUAGE.get(params.get("lang", [""])[0], "")
            docs = self._set(search["ol"].get(language, "")) if search else []
            return httpx.Response(200, json={"numFound": len(docs), "docs": docs}, request=request)
        if host.startswith("api.audible.") and "keywords" in params:
            self.log.append(request)
            market = next((code for code, suffix in (("de", "de"), ("us", "com")) if host == f"api.audible.{suffix}"),
                          "")
            search = self._search(params["keywords"][0])
            products = self._set(search["audible"].get(market, "")) if search else []
            return httpx.Response(200, json={"products": products}, request=request)
        if host == "services.dnb.de" and "woe=" in params.get("query", [""])[0]:
            self.log.append(request)
            query = params["query"][0]
            words = [part.split("=", 1)[1].strip('"') for part in query.split(" and ")]
            search = self._search(" ".join(words))
            records = self._set(search["dnb"]) if search else []
            return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=marc(records),
                                  request=request)
        return super().handle_request(request)
