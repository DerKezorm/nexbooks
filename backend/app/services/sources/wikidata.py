"""Wikidata and Wikimedia Commons: who an author is, what a picture of them is and under which licence it may be
shown (the photos and short biographies of authors).

**A photo only for a person who is certain.** A name is not enough: there are many people of one name. nexbooks
takes the Wikidata item of an author only when
1. the item is a human (``P31`` is ``Q5``),
2. the name is the name of the author - a label of the item in one of the languages asked, or an alias that only
  writes a label another way (initials); an alias that is another name (a pen name the item is known by) is not
  enough: a pen name never gets the photo of the person behind it - and
3. **a book of the author's, one nexbooks holds, is among the works that name this item as their author**
  (``P50``): the title of a book in the library is a label or an alias of a work whose author is the item, and the
  title says something by itself (two words, or eight letters: not ``Poems`` or ``It``), else two works have two
  titles of ours (``readers/names.titles_prove_author``).
When two items pass, or none, or the proof is two common words and another human of the same name has works
too, there is no photo: no photo is better than the wrong one. (A human of the same name with works of their own
is common: scientists with articles share the names of novelists; a title that says something by itself still
tells them apart.) The proof is made with
the search of Wikidata (``haswbstatement:P50=Q...`` and the title), so a prolific author costs a few requests,
not their whole list of works.

**A picture only under a free licence.** The picture is the ``P18`` of the item, a file on Commons. Its licence
is read from Commons (``extmetadata``), and only Creative Commons BY and BY-SA, CC0 and public domain are taken
(``free``); a file under any other licence, or with none, is left. The author and the licence are kept with the
picture and shown under it (``Photo: <author>, <licence>``), with a link to its page on Commons.

Every request goes through ``http.request`` (the switches, the host table, the turn, the way out); what a person
typed or a book is called is only ever a query value. The description in the language of the person and the
years of birth and death make the short biography; nothing longer is taken (a text of Wikipedia lies on hosts
that are not in the table).
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..readers.limits import COVER_MAX_BYTES
from ..readers.names import (
    PROOF_DISTINCT,
    PROOF_NONE,
    PROOF_TWO,
    authors_compatible,
    title_keys,
    title_proof,
    works_proved,
)
from ..readers.text import clean
from . import cache, http

SOURCE = "wikidata"
API = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
QID = re.compile(r"^Q[1-9]\d{0,12}$")
#: The file name a ``P18`` carries (no namespace, no path), and the kinds of picture that are taken.
FILE_NAME = re.compile(r"^[^\x00-\x1f/\\|#<>\[\]{}]{1,230}\.(?:jpe?g|png|webp)$", re.IGNORECASE)
#: Labels and descriptions are asked for in these languages (the biographies are kept per language); the first ones are
#: those the interface and most books come in.
LANGUAGES = ("en", "de", "fr", "es", "it", "nl", "pt", "sv", "da", "pl", "ru", "ja", "tr", "cs", "fi", "no")
#: Candidates of one name that are looked at (the first page of the search holds ten), and the titles asked for when an
#: author has more books than the first ``LISTED`` the search lists.
MAX_CANDIDATES = 10
MAX_PROBES = 4
LISTED = 50
#: The width of the picture asked of Commons.
PICTURE_WIDTH = 640
#: The licences that may be shown: Creative Commons BY and BY-SA
#: (any version, also the ported ones), CC0 and public domain.
_FREE = re.compile(r"^(?:CC[ -]BY(?:[ -]SA)?[ -]\d\.\d(?:[ -][A-Za-z]{2,3})?|CC0(?:[ -]1\.0)?(?: Universal)?|"
                   r"Public domain|PD[ -][\w -]{0,40})$", re.IGNORECASE)


def free(licence: str) -> bool:
    """Whether a licence, as Commons names it, may be shown with a photo. ``CC BY-NC`` and ``CC BY-ND`` may not."""
    return bool(_FREE.match(clean(licence, 80)))


@dataclass
class Hit:
    id: str
    label: str
    description: str
    #: The text the search matched (a label or an alias).
    matched: str


@dataclass
class Entity:
    id: str
    labels: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    human: bool = False
    image: str = ""
    born: int | None = None
    died: int | None = None

    def names(self) -> list[str]:
        found = list(self.labels.values())
        for values in self.aliases.values():
            found.extend(values)
        return list(dict.fromkeys(found))


@dataclass
class Identity:
    qid: str
    descriptions: dict[str, str]
    born: int | None
    died: int | None
    image: str


@dataclass
class Picture:
    thumb_url: str
    page_url: str
    author: str
    license: str
    license_url: str


# ---- Reading the answers (pure)
# ---------------------------------------------------------------------------------------


def _text(value: Any, limit: int = 300) -> str:
    return clean(value, limit) if isinstance(value, str) else ""


def parse_search(data: Any) -> list[Hit]:
    """The items a ``wbsearchentities`` answer lists."""
    items = data.get("search") if isinstance(data, dict) else None
    found: list[Hit] = []
    for item in items[:20] if isinstance(items, list) else []:
        if not isinstance(item, dict) or not QID.match(str(item.get("id") or "")):
            continue
        match = item.get("match")
        found.append(Hit(id=str(item["id"]), label=_text(item.get("label")), description=_text(item.get("description")),
                         matched=_text(match.get("text")) if isinstance(match, dict) else ""))
    return found


def parse_titles(data: Any) -> list[str]:
    """The ids of the items a ``list=search`` answer lists."""
    query = data.get("query") if isinstance(data, dict) else None
    items = query.get("search") if isinstance(query, dict) else None
    return [str(item["title"]) for item in items[:50] if isinstance(items, list) and isinstance(item, dict)
            and QID.match(str(item.get("title") or ""))] if isinstance(items, list) else []


def _year(snak: Any) -> int | None:
    value = snak.get("datavalue", {}).get("value") if isinstance(snak, dict) else None
    time = value.get("time") if isinstance(value, dict) else None
    match = re.match(r"^([+-])(\d{1,4})-", time) if isinstance(time, str) else None
    # Only years that are years of a lifetime (the precision of a century says nothing).
    precision = value.get("precision") if isinstance(value, dict) else None
    if not match or match.group(1) != "+" or not isinstance(precision, int) or precision < 9:
        return None
    year = int(match.group(2))
    return year if 100 <= year <= 3000 else None


def _claim_ids(claims: Any, prop: str) -> list[str]:
    found = []
    for claim in claims.get(prop, [])[:20] if isinstance(claims, dict) and isinstance(claims.get(prop), list) else []:
        snak = claim.get("mainsnak") if isinstance(claim, dict) else None
        value = snak.get("datavalue", {}).get("value") if isinstance(snak, dict) else None
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            found.append(value["id"])
    return found


def parse_entities(data: Any) -> dict[str, Entity]:
    """The items of a ``wbgetentities`` answer, cut down to what nexbooks reads."""
    entities = data.get("entities") if isinstance(data, dict) else None
    found: dict[str, Entity] = {}
    for ident, raw in list(entities.items())[:60] if isinstance(entities, dict) else []:
        if not QID.match(str(ident)) or not isinstance(raw, dict) or "missing" in raw:
            continue
        entity = Entity(id=str(ident))
        for language, label in (raw.get("labels") or {}).items() if isinstance(raw.get("labels"), dict) else []:
            if isinstance(label, dict) and _text(label.get("value")):
                entity.labels[str(language)[:12]] = _text(label.get("value"))
        for language, items in (raw.get("aliases") or {}).items() if isinstance(raw.get("aliases"), dict) else []:
            if isinstance(items, list):
                names = [_text(item.get("value")) for item in items[:20] if isinstance(item, dict)]
                entity.aliases[str(language)[:12]] = [name for name in names if name]
        for language, item in (raw.get("descriptions") or {}).items() if isinstance(raw.get("descriptions"), dict) \
                else []:
            if isinstance(item, dict) and _text(item.get("value"), 200):
                entity.descriptions[str(language)[:12]] = _text(item.get("value"), 200)
        claims = raw.get("claims")
        entity.human = "Q5" in _claim_ids(claims, "P31")
        pictures = claims.get("P18") if isinstance(claims, dict) and isinstance(claims.get("P18"), list) else []
        for claim in pictures[:5]:
            snak = claim.get("mainsnak") if isinstance(claim, dict) else None
            value = snak.get("datavalue", {}).get("value") if isinstance(snak, dict) else None
            # A picture marked as outdated or deprecated is not the one to show.
            if isinstance(value, str) and FILE_NAME.fullmatch(value) and claim.get("rank") != "deprecated":
                entity.image = value
                break
        for prop, slot in (("P569", "born"), ("P570", "died")):
            statements = claims.get(prop) if isinstance(claims, dict) and isinstance(claims.get(prop), list) else []
            if statements and isinstance(statements[0], dict):
                setattr(entity, slot, _year(statements[0].get("mainsnak")))
        found[entity.id] = entity
    return found


def _plain(markup: Any) -> str:
    """The text of a field Commons sends as HTML (the author is often a link): tags gone, entities resolved."""
    if not isinstance(markup, str):
        return ""
    return clean(html.unescape(re.sub(r"<[^>]*>", " ", markup[:2000])), 200)


def parse_picture(data: Any) -> Picture | None:
    """The picture of a Commons answer (``imageinfo`` with ``extmetadata``),
    when it is under a licence that may be shown
    and lies on a host of the source. None for anything else."""
    query = data.get("query") if isinstance(data, dict) else None
    pages = query.get("pages") if isinstance(query, dict) else None
    for page in list(pages.values())[:1] if isinstance(pages, dict) else []:
        infos = page.get("imageinfo") if isinstance(page, dict) else None
        info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else None
        meta = info.get("extmetadata") if info else None
        if not info or not isinstance(meta, dict):
            return None

        def field_of(name: str, meta: dict[str, Any] = meta) -> str:
            item = meta.get(name)
            return item.get("value") if isinstance(item, dict) and isinstance(item.get("value"), str) else ""

        licence = _text(field_of("LicenseShortName"), 80)
        thumb = info.get("thumburl")
        description = info.get("descriptionurl")
        if not licence or not free(licence) or field_of("NonFree").lower() == "true":
            return None
        if not isinstance(thumb, str) or not http.host_allowed(SOURCE, thumb):
            return None
        page_url = description if isinstance(description, str) and http.host_allowed(SOURCE, description) else ""
        link = re.match(r"^https?://(creativecommons\.org/(?:licenses|publicdomain)/[\w./-]{1,60})$",
                        field_of("LicenseUrl"))
        return Picture(thumb_url=thumb, page_url=page_url, author=_plain(field_of("Artist")) or _plain(
            field_of("Credit")), license=licence, license_url=f"https://{link.group(1)}" if link else "")
    return None


# ---- Asking
# --------------------------------------------------------------------------------------------------------------


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:24]


def _ask(db: Session, url: str, key: str, parse: Any, to_cache: Any, from_cache: Any) -> Any:
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING:
        return from_cache(kept)
    answer = http.request(db, SOURCE, url)
    result = parse(http.json_of(SOURCE, answer)) if answer.status_code == 200 else parse({})
    cache.put(SOURCE, key, to_cache(result), hit=bool(result))
    return result


def search_people(db: Session, name: str, language: str) -> list[Hit]:
    """The items whose label or alias is like ``name`` (humans and everything else; the caller looks)."""
    url = API + "?" + urlencode({"action": "wbsearchentities", "search": name[:100], "language": language,
                                 "uselang": language, "type": "item", "limit": 10, "format": "json"})
    return _ask(db, url, cache.make_key("search", language, name), parse_search,
                lambda hits: [h.__dict__ for h in hits], lambda kept: [Hit(**row) for row in kept])


def entities(db: Session, ids: list[str], props: str, languages: tuple[str, ...]) -> dict[str, Entity]:
    """The items ``ids`` (at most 50) with the given ``props`` (``labels|aliases|descriptions|claims``)
    in the languages."""
    wanted = [i for i in dict.fromkeys(ids) if QID.match(i)][:50]
    if not wanted:
        return {}
    url = API + "?" + urlencode({"action": "wbgetentities", "ids": "|".join(wanted), "props": props,
                                 "languages": "|".join(languages), "format": "json"})
    key = "entities|" + _digest(props, ",".join(languages), ",".join(sorted(wanted)))
    return _ask(db, url, key, parse_entities, lambda found: {k: v.__dict__ for k, v in found.items()},
                lambda kept: {k: Entity(**row) for k, row in kept.items()})


def works_listed(db: Session, qid: str) -> list[str]:
    """The items whose author (``P50``) is ``qid``: the books
    of the author, the first fifty. Empty for someone who wrote
    nothing that Wikidata knows (a footballer of the same name)."""
    if not QID.match(qid):
        return []
    url = API + "?" + urlencode({"action": "query", "list": "search", "srsearch": f"haswbstatement:P50={qid}",
                                 "srlimit": LISTED, "srnamespace": 0, "format": "json"})
    return _ask(db, url, "works|" + _digest(qid), parse_titles, list, list)


def works_titled(db: Session, qid: str, title: str) -> list[str]:
    """The items whose author (``P50``) is ``qid`` and whose text
    holds ``title``: the books of the author with this name
    in some language. For an author with more books than ``works_listed`` lists."""
    phrase = " ".join(title.replace('"', " ").replace(chr(92), " ").split())[:80]
    if not QID.match(qid) or not phrase:
        return []
    query = f'haswbstatement:P50={qid} "{phrase}"'
    url = API + "?" + urlencode({"action": "query", "list": "search", "srsearch": query, "srlimit": 10,
                                 "srnamespace": 0, "format": "json"})
    return _ask(db, url, "works|" + _digest(qid, phrase), parse_titles, list, list)


def picture_of(db: Session, file_name: str) -> Picture | None:
    """The picture ``file_name`` of Commons with its author and licence; None when it is not under a free licence."""
    if not FILE_NAME.fullmatch(file_name):
        return None
    url = COMMONS + "?" + urlencode({
        "action": "query", "titles": "File:" + file_name, "prop": "imageinfo", "iiprop": "url|extmetadata|mime",
        "iiurlwidth": PICTURE_WIDTH, "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|NonFree",
        "format": "json"})
    return _ask(db, url, "picture|" + _digest(file_name), parse_picture,
                lambda found: found.__dict__ if found else {}, lambda kept: Picture(**kept) if kept else None)


def fetch_picture(db: Session, url: str) -> bytes | None:
    """The bytes of a picture on the upload host (through the one way out); None when it is not there."""
    if not http.host_allowed(SOURCE, url):
        return None
    answer = http.request(db, SOURCE, url, headers={"Accept": "image/*"}, limit=COVER_MAX_BYTES)
    return answer.content if answer.status_code == 200 else None


# ---- Who is the author
# ---------------------------------------------------------------------------------------------------


def _fits(name: str, entity: Entity) -> bool:
    """Whether ``name`` is the name of the item: a label of it (in any language), or an alias that is only another way
    to write a label (initials, ``A. Feldkamp`` for ``Ada Feldkamp``). An alias that is another name altogether (a pen
    name the item is known by, a name from before a marriage) is not enough: a pen name stays a pen name, and never gets
    the photo and the text of the person behind it."""
    labels = list(dict.fromkeys(entity.labels.values()))
    if any(authors_compatible(name, label) for label in labels):
        return True
    aliases = [alias for values in entity.aliases.values() for alias in values]
    return any(authors_compatible(name, alias) and any(authors_compatible(alias, label) for label in labels)
               for alias in aliases)


def _titles_of(entity: Entity) -> list[str]:
    return entity.names()


def identify(db: Session, name: str, titles: list[str], language: str = "", *, least: int = 1) -> Identity | None:
    """The Wikidata item of the author ``name`` whose works include
    one of ``titles`` (titles of the books nexbooks holds
    of them), or None. The checks are the ones at the top of this module; two items that pass are no item, and an item
    proved only by two books whose titles are common words is none either when another human of the same name has works
    too (two writers of one name: no photo rather than the wrong one)."""
    if not any(key for title in titles for key in title_keys(title)) or not name.strip():
        return None
    hits = search_people(db, name, "en")
    if language and language != "en":
        seen = {hit.id for hit in hits}
        hits += [hit for hit in search_people(db, name, language) if hit.id not in seen]
    fitting: list[Hit] = []
    for hit in hits:
        # Each item once (the search in two languages, or by label and by alias, may list it twice).
        if hit.id not in {h.id for h in fitting} and any(
                authors_compatible(name, text) for text in (hit.label, hit.matched) if text):
            fitting.append(hit)
    if not fitting:
        return None
    languages = LANGUAGES if language in ("", *LANGUAGES) else (language, *LANGUAGES)
    people = entities(db, [h.id for h in fitting[:MAX_CANDIDATES]], "labels|aliases|descriptions|claims", languages)
    proven: list[tuple[Entity, str]] = []
    writers = 0
    for hit in fitting[:MAX_CANDIDATES]:
        person = people.get(hit.id)
        if person is None or not person.human or not _fits(name, person):
            continue
        listed = works_listed(db, person.id)
        if not listed:
            continue
        writers += 1
        works = [_titles_of(work) for work in entities(db, listed, "labels|aliases", languages).values()]
        proof = title_proof(titles, works)
        if proof != PROOF_DISTINCT and len(listed) >= LISTED:
            # More books than the search lists: ask for the titles one by one.
            for title in titles[:MAX_PROBES]:
                found = works_titled(db, person.id, title)
                works += [_titles_of(work) for work in (
                    entities(db, found, "labels|aliases", languages) if found else {}).values()]
                proof = title_proof(titles, works)
                if proof == PROOF_DISTINCT:
                    break
        if least > 1:
            # (An author nobody of the libraries is: ``least`` works of the item must be works of the catalogue, a
            # single title proves nothing then, not even one that says something by itself.)
            count, distinct = works_proved(titles, works)
            proof = (PROOF_DISTINCT if distinct else PROOF_TWO) if count >= least else PROOF_NONE
        if proof:
            proven.append((person, proof))
    if len(proven) != 1:
        return None
    # Two books whose titles are common words prove an author only when nobody else of the name wrote anything.
    if proven[0][1] != PROOF_DISTINCT and writers > 1:
        return None
    person = proven[0][0]
    return Identity(qid=person.id, descriptions=person.descriptions, born=person.born, died=person.died,
                    image=person.image)
