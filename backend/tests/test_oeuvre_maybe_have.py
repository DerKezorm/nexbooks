"""A work of the catalogue that a book on the shelf may be, though no title rule took it for that book: the same
place in the same series, or a title that is only the series and the number. "Alle fehlenden beschaffen" leaves it
out. All names invented."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.models import CatalogWork
from app.services.oeuvre.view import Own, maybe_own


def work(series: str = "Nebelchor", position: float | None = 2.0) -> CatalogWork:
    return CatalogWork(key="w", series=series, series_pos=position, titles={"de": {"title": "Die Laternenstraße"}})


def own(title: str, *, series: str = "", position: float | None = None) -> Own:
    view: Any = SimpleNamespace(series_name=series)
    return Own(view=view, titles=[title], series_pos=position)


def test_a_book_at_the_place_of_the_work_in_its_series_may_be_it() -> None:
    shelf = own("Der Ruf der Möwe", series="Nebelchor", position=2.0)
    assert maybe_own(work(), [shelf]) is shelf


def test_a_book_whose_title_is_the_series_and_the_number_may_be_it() -> None:
    shelf = own("Nebelchor 2")
    assert maybe_own(work(), [shelf]) is shelf


def test_another_place_another_series_or_a_work_without_a_place_say_nothing() -> None:
    assert maybe_own(work(), [own("Der Ruf der Möwe", series="Nebelchor", position=3.0)]) is None
    assert maybe_own(work(), [own("Der Ruf der Möwe", series="Laternen", position=2.0)]) is None
    assert maybe_own(work(), [own("Nebelchor 3")]) is None
    assert maybe_own(work(position=None), [own("Nebelchor 2", series="Nebelchor", position=2.0)]) is None
