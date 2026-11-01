"""Audible's catalogue, one market at a time (``api.audible.<market>/1.0/catalog/products``): narrators, running time,
abridged or not, plays, series and place in it, description, cover, ASIN.

The interface is not documented and not offered to others: nexbooks only *reads*, slowly, caches what it read, and the
operator can switch it off. It is the proof for recordings: the running time of a recording on the shelf, compared
with the
running times of the products (within 5 percent), says whether it is the unabridged reading, an abridged one or a play
(``services/matching.py``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from .. import genres
from ..readers.names import authors_compatible, clean_authors, core_title
from ..readers.text import clean, fold, html_to_text, year_of
from . import authorq, cache, config, http
from .model import DRAMATIZED, Candidate, Entry, Query, as_written, day_of, language_code

SOURCE = "audible"
#: What is asked of a product, categories included: the genre of the shelf is voted from the names of the categories and
#: the keywords of the thesaurus (``genres.tally``), whether the question is a search or a chart.
GROUPS = "contributors,product_attrs,product_desc,series,media,category_ladders"
CHART_GROUPS = GROUPS
ASIN = re.compile(r"^[A-Z0-9]{10}$")
NUMBER = re.compile(r"^\d{1,4}(?:[.,]\d{1,2})?$")
MAX_RESULTS = 25


def _text(value: Any, limit: int = 300) -> str:
    return clean(value, limit) if isinstance(value, str) else ""


def _names(value: Any, *, authors: bool = False) -> list[str]:
    """The names of a list of contributors. Audible lists a translator among the authors (``Dietmar Weber -
    Übersetzer``): in the list of ``authors`` he is left out (``clean_authors``)."""
    found = []
    for item in value[:20] if isinstance(value, list) else []:
        name = _text(item.get("name"), 200) if isinstance(item, dict) else ""
        if name:
            found.append(name)
    return clean_authors(found, any_role=not authors)


def _picture(images: Any) -> str:
    """The address of the largest picture of a product, as long as it is one of Amazon's image hosts (the check is made
    again before anything is fetched)."""
    if not isinstance(images, dict):
        return ""
    best, best_size = "", 0
    for size, url in images.items():
        if isinstance(url, str) and str(size).isdigit() and int(size) >= best_size and url.startswith("https://"):
            best, best_size = url, int(size)
    return best if http.host_allowed(SOURCE, best) else ""


def variant_of(product: dict[str, Any]) -> str:
    """``dramatized`` for a play (the words that say so), else what Audible's format says, else empty."""
    said = " ".join(_text(product.get(name)) for name in ("title", "subtitle", "publisher_name"))
    if DRAMATIZED.search(said) or DRAMATIZED.search(fold(said)):
        return "dramatized"
    kind = _text(product.get("format_type")).lower()
    return kind if kind in ("unabridged", "abridged") else ""


def _categories(product: dict[str, Any]) -> list[str]:
    """The names of the categories a product stands in (``category_ladders``: steps from the broad to the narrow)."""
    names: list[str] = []
    ladders = product.get("category_ladders")
    for ladder in ladders[:6] if isinstance(ladders, list) else []:
        steps = ladder.get("ladder") if isinstance(ladder, dict) else None
        for step in steps[:6] if isinstance(steps, list) else []:
            name = _text(step.get("name"), 100) if isinstance(step, dict) else ""
            if name and name not in names:
                names.append(name)
    return names


def _keywords(product: dict[str, Any]) -> list[str]:
    """The keywords of Audible's thesaurus for a product (``Psychothriller``, ``Ermittler``), a list or one text."""
    value = product.get("thesaurus_subject_keywords")
    if isinstance(value, str):
        value = [value]
    return [_text(item, 100) for item in value[:30] if _text(item, 100)] if isinstance(value, list) else []


def _hints(title: str, subtitle: str) -> list[str]:
    """What the title and the subtitle of a product say of the genre: the words in brackets (``Ein Bodenstein-Kirchhoff-
    Krimi 1``), and a short subtitle (``Kriminalroman``). A long subtitle is advertising and says nothing."""
    parts = re.findall(r"[\(\[]([^()\[\]]{2,80})[\)\]]", title)
    parts.append(subtitle)
    return [part for part in parts if part and len(part.split()) <= 6 and genres.genre_of_one(part)]


def parse_products(data: Any, market: str, limit: int = MAX_RESULTS) -> list[Candidate]:
    """The products of an answer, at most ``limit`` (a search asks for ``MAX_RESULTS``, a chart for ``PAGE``)."""
    products = data.get("products") if isinstance(data, dict) else None
    if isinstance(data, dict) and isinstance(data.get("product"), dict):
        products = [data["product"]]
    found: list[Candidate] = []
    for product in products[:limit] if isinstance(products, list) else []:
        if not isinstance(product, dict):
            continue
        asin = _text(product.get("asin"), 20).upper()
        title = _text(product.get("title"))
        if not ASIN.match(asin) or not title:
            continue
        subtitle = _text(product.get("subtitle"))
        authors = _names(product.get("authors"), authors=True)
        series, position = "", None
        entries = product.get("series")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            series = _text(entries[0].get("title"), 200)
            raw = str(entries[0].get("sequence") or "").strip()
            position = float(raw.replace(",", ".")) if NUMBER.match(raw) else None
        minutes = product.get("runtime_length_min")
        released = product.get("release_date") or product.get("issue_date")
        found.append(Candidate(
            source=SOURCE, key=asin, asin=asin, title=core_title(title, authors[0] if authors else "") or title,
            subtitle=subtitle, authors=authors, narrators=_names(product.get("narrators")),
            languages=[code] if (code := language_code(product.get("language"))) else [], market=market,
            year=year_of(released), released=day_of(released), series=series, series_pos=position,
            subjects=list(dict.fromkeys(_categories(product) + _keywords(product) + _hints(title, subtitle))),
            description=html_to_text(product.get("merchandising_summary") or product.get("publisher_summary")),
            runtime_min=minutes if isinstance(minutes, int) and 0 < minutes < 60 * 24 * 14 else None,
            variant=variant_of(product), publisher=_text(product.get("publisher_name"), 200),
            cover_url=_picture(product.get("product_images")),
            full_title=title if core_title(title, authors[0] if authors else "") != title else ""))
    return found


def _url(market: str, path: str, params: dict[str, str | int]) -> str:
    return f"https://{config.market_host(market)}/1.0/catalog/{path}?{urlencode(params)}"


def search(db: Session, query: Query, market: str, *, interactive: bool = False) -> list[Candidate]:
    """The products that fit the question in one market: found by the title and the first author. For a recording that
    has an ASIN of its own that is one request for exactly that product. When the free text finds nothing, the fields
    ``title`` and ``author`` are tried apart (the catalogue knows both): its free text weighs a long title down with
    the author's name, a field does not."""
    if market not in config.AUDIBLE_MARKETS:
        return []
    author = clean(query.authors[0], 100) if query.authors else ""
    title = core_title(clean(query.title, 200), author)
    asin = query.asin.upper() if ASIN.match(query.asin.upper()) else ""
    key = cache.make_key("search2", market, asin, title, author)
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING and (kept or asin or not query.fallback or not title or not author):
        return [Candidate.from_json(item) for item in kept]
    if kept is not cache.MISSING:
        # The words found nothing before (kept): only the question by the fields is left to ask.
        return _by_fields(db, market, title, author, interactive=interactive)
    if asin:
        url = _url(market, f"products/{quote(asin)}", {"response_groups": GROUPS})
    elif title:
        url = _url(market, "products", {"keywords": f"{author} {title}".strip(), "num_results": MAX_RESULTS,
                                        "products_sort_by": "Relevance", "response_groups": GROUPS})
    else:
        return []
    answer = http.request(db, SOURCE, url, interactive=interactive)
    found = [] if answer.status_code == 404 else parse_products(http.json_of(SOURCE, answer), market)
    cache.put(SOURCE, key, [item.to_json() for item in found], hit=bool(found))
    if not found and not asin and title and author and query.fallback:
        return _by_fields(db, market, title, author, interactive=interactive)
    return found


def _by_fields(db: Session, market: str, title: str, author: str, *, interactive: bool) -> list[Candidate]:
    """The products by the fields ``title`` and ``author`` (asked when the words found nothing), kept apart."""
    key = cache.make_key("fields1", market, title, author)
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING:
        return [Candidate.from_json(item) for item in kept]
    by_fields = _url(market, "products", {"title": title, "author": author, "num_results": MAX_RESULTS,
                                          "products_sort_by": "Relevance", "response_groups": GROUPS})
    answer = http.request(db, SOURCE, by_fields, interactive=interactive)
    found = [] if answer.status_code == 404 else parse_products(http.json_of(SOURCE, answer), market)
    cache.put(SOURCE, key, [item.to_json() for item in found], hit=bool(found))
    return found


#: How many products a page of the catalogue holds at most (Audible's own limit).
PAGE = 50
#: How a chart is asked: the best-selling products, and the newest first (which puts the ones still to come first).
SORTS = {"best": "BestSellers", "release": "-ReleaseDate"}


def chart(db: Session, market: str, kind: str, *, pages: int = 1,
          enough: Callable[[list[Candidate]], bool] | None = None) -> list[Candidate]:
    """A list of the catalogue of one market: ``best`` (the best-selling) or ``release`` (the newest first; the products
    that come out later lie before the ones that came out), every page whole (``PAGE`` products). ``pages`` is the most
    pages asked for; ``enough`` is asked after each page with everything found so far and stops the asking when it
    says so. Not kept in the cache: Discover keeps the list itself, for twelve hours, and asks again after that.
    ``http.SourceOff`` when Audible may not be asked."""
    if market not in config.AUDIBLE_MARKETS or kind not in SORTS:
        return []
    found: list[Candidate] = []
    for page in range(1, max(1, pages) + 1):
        url = _url(market, "products", {"num_results": PAGE, "page": page, "products_sort_by": SORTS[kind],
                                        "response_groups": CHART_GROUPS})
        answer = http.request(db, SOURCE, url)
        if answer.status_code == 404:
            break
        batch = parse_products(http.json_of(SOURCE, answer), market, PAGE)
        found += batch
        if len(batch) < PAGE // 2 or (enough is not None and enough(found)):
            break
    return found


def series_volumes(db: Session, market: str, series: str, author: str) -> list[Candidate]:
    """The products of a series as Audible lists them: found by the series' name and the author, and only the ones that
    say they belong to a series of that name. Kept in the cache like every question."""
    if market not in config.AUDIBLE_MARKETS or not clean(series, 200):
        return []
    key = cache.make_key("series", market, series, author)
    kept = cache.get(SOURCE, key)
    if kept is cache.MISSING:
        words = f"{clean(author, 100)} {clean(series, 200)}".strip()
        url = _url(market, "products", {"keywords": words, "num_results": PAGE, "products_sort_by": "Relevance",
                                        "response_groups": GROUPS})
        answer = http.request(db, SOURCE, url)
        found = [] if answer.status_code == 404 else parse_products(http.json_of(SOURCE, answer), market, PAGE)
        wanted = fold(series)
        found = [c for c in found if c.series and fold(c.series) == wanted]
        cache.put(SOURCE, key, [c.to_json() for c in found], hit=bool(found), days=7)
        return found
    return [Candidate.from_json(item) for item in kept]


#: Pages of an author's products at most, for the catalogue of their works (counted from 0 at Audible).
AUTHOR_PAGES = 6


def entry_of(candidate: Candidate, authors_count: int, person: str = "") -> Entry:
    """A product as an entry of the catalogue of an author's works (``person``: Audible's number of the author)."""
    return Entry(
        source=SOURCE, key=candidate.asin or candidate.key, title=candidate.title,
        language=candidate.languages[0] if candidate.languages else "", authors=candidate.authors[:6],
        year=candidate.year, released=candidate.released, asin=candidate.asin, runtime_min=candidate.runtime_min,
        variant=candidate.variant, form="audio", series=candidate.series, series_pos=candidate.series_pos,
        signals={"keywords": [k.lower() for k in candidate.subjects[:30]],
                 **as_written(candidate.full_title or candidate.title, candidate.title, subtitle=candidate.subtitle)},
        authors_count=max(1, authors_count),
        cover_url=candidate.cover_url, person=person)


def author_numbers(products: Any, author: str) -> dict[str, str]:
    """For each product of an answer (by its ASIN), Audible's number of the author of that name on it (empty when it
    names none): two people of one name have two."""
    found: dict[str, str] = {}
    for product in products if isinstance(products, list) else []:
        if not isinstance(product, dict) or not isinstance(product.get("authors"), list):
            continue
        numbers = [_text(item.get("asin"), 20).upper() for item in product["authors"] if isinstance(item, dict)
                   and authorq.is_author(author, _text(item.get("name"), 200)) and ASIN.match(
                       _text(item.get("asin"), 20).upper())]
        found[_text(product.get("asin"), 20).upper()] = numbers[0] if numbers else ""
    return found


def author_entries(db: Session, market: str, author: str) -> list[Entry]:
    """The products of an author in one market, by the field ``author``, paged through; only the ones that name the
    author. Kept for a day (what Audible says is there to be shown)."""
    if market not in config.AUDIBLE_MARKETS or not clean(author, 100):
        return []

    def fetch() -> list[Entry]:
        found: list[Entry] = []
        for page in range(AUTHOR_PAGES):
            url = _url(market, "products", {"author": clean(author, 100), "num_results": PAGE, "page": page,
                                            "products_sort_by": "-ReleaseDate", "response_groups": GROUPS})
            answer = http.request(db, SOURCE, url)
            if answer.status_code == 404:
                break
            data = http.json_of(SOURCE, answer)
            products = data.get("products") if isinstance(data, dict) else None
            counts = {str(p.get("asin") or "").upper(): len(p.get("authors") or []) for p in products or []
                      if isinstance(p, dict) and isinstance(p.get("authors"), list)} if isinstance(products, list) \
                else {}
            batch = parse_products(data, market, PAGE)
            numbers = author_numbers(products, author)
            # (A number is the market's: the same person may have another one in another market.)
            found += [entry_of(c, counts.get(c.asin, 1), f"{market}:{numbers[c.asin]}" if numbers.get(c.asin) else "")
                      for c in batch
                      if any(authorq.is_author(author, name) for name in c.authors)]
            if len(batch) < PAGE:
                break
        return found

    return authorq.kept(SOURCE, cache.make_key("author2", market, author), fetch)


def by_author(db: Session, market: str, author: str) -> list[Candidate]:
    """The products of an author as Audible lists them (found by the name, the best-selling first), and only the ones
    that name an author that fits. Kept in the cache for a week."""
    if market not in config.AUDIBLE_MARKETS or not clean(author, 100):
        return []
    key = cache.make_key("author", market, author)
    kept = cache.get(SOURCE, key)
    if kept is cache.MISSING:
        url = _url(market, "products", {"keywords": clean(author, 100), "num_results": PAGE,
                                        "products_sort_by": "BestSellers", "response_groups": GROUPS})
        answer = http.request(db, SOURCE, url)
        found = [] if answer.status_code == 404 else parse_products(http.json_of(SOURCE, answer), market, PAGE)
        found = [c for c in found if any(authors_compatible(author, name) for name in c.authors)]
        cache.put(SOURCE, key, [c.to_json() for c in found], hit=bool(found), days=7)
        return found
    return [Candidate.from_json(item) for item in kept]
