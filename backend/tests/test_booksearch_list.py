"""Cases found when a list of two hundred known books was searched one after the other, the way a person puts them on a
wish list (the answers of the real sources recorded and rebuilt here with invented names, titles and numbers, in the
shapes the sources answered in): a book called like its series is a book, a translator credited first splits no work,
the series is named in the language of the title, a translation that keeps the original title shows its own, and a
search the person gave up waiting for has not failed."""

from __future__ import annotations

import importlib
from typing import Any

from app.services.booksearch import ask, present
from app.services.booksearch.asked import read
from app.services.readers.names import authors_compatible, compare_key, core_title
from app.services.readers.text import fold
from app.services.sources.model import Entry

build = importlib.import_module("app.services.booksearch.build")


def entry(source: str, key: str, title: str, *authors: str, **more: Any) -> Entry:
    return Entry(source=source, key=key, title=title, authors=list(authors), **more)


def hits(entries: list[Entry], words: str) -> list[Any]:
    return build.build(entries, read(words), languages=["de"], original=False)


def named(found: list[Any], title: str) -> list[Any]:
    return [hit for hit in found if fold(hit.title) == fold(title)]


# ---- A book called like its series ------------------------------------------------------------------------------


def test_a_book_whose_series_is_only_the_recordings_of_a_play_of_it_can_be_acquired() -> None:
    """The shop sells a play made of the novel in two parts and files them as a series of its name (``Stromriss, Teil
    1`` and ``Teil 2``): the novel is no whole of volumes, it is the book."""
    entries = [entry("dnb", f"9100{n}", "Stromriss", "Ada Example", language="de", year=2012 + n,
                     isbns=[f"97830009100{n}0"]) for n in range(3)]
    entries += [entry("audible", f"B91000000{n}", f"Stromriss, Teil {n}", "Ada Example", language="de", form="audio",
                      series="Stromriss", series_pos=float(n), variant="dramatized", runtime_min=560)
                for n in (1, 2)]
    found = hits(entries, "Stromriss Ada Example")
    book = next(hit for hit in found if hit.title == "Stromriss" and hit.label == "work")
    assert present.why_not(book) == "", [(hit.title, hit.label, present.why_not(hit)) for hit in found]


def test_the_book_that_gives_its_series_the_name_is_its_first_volume() -> None:
    """``Mira Mohnblume`` beside ``Mira Mohnblume geht an Bord`` (2) and ``Mira Mohnblume im Süden`` (3): no volume
    of another title stands at the first place, so the book of the name is a book, not the whole."""
    entries = [entry("dnb", "9200", "Mira Mohnblume", "Ada Example", language="de", year=1949, isbns=["9783000920000"]),
               entry("openlibrary", "/works/OL9200W", "Mira Mohnblume", "Ada Example", language="de", year=1945,
                     work_key="OL9200W", signals={"work_level": True}),
               entry("audible", "B920000002", "Mira Mohnblume geht an Bord", "Ada Example", language="de",
                     form="audio", series="Mira Mohnblume", series_pos=2.0, runtime_min=300),
               entry("audible", "B920000003", "Mira Mohnblume im Süden", "Ada Example", language="de", form="audio",
                     series="Mira Mohnblume", series_pos=3.0, runtime_min=300)]
    found = hits(entries, "Mira Mohnblume Ada Example")
    book = next(hit for hit in found if hit.title == "Mira Mohnblume")
    assert (book.label, present.why_not(book)) == ("work", ""), [(hit.title, present.why_not(hit)) for hit in found]


def test_a_classic_whose_parts_a_shop_reads_centuries_later_is_the_book_and_its_parts_stand_apart() -> None:
    """A shop names the parts of an old epic by the epic and gives the part's title as the subtitle (``Das Lied vom
    Salztor`` at place 2, ``Der Hafen``): the epic is the book, the part at place 2 is a hit of its own."""
    entries = [entry("openlibrary", "/works/OL9300W", "Das Lied vom Salztor", "Ada Example", language="de",
                     year=1472, work_key="OL9300W", signals={"work_level": True}),
               *(entry("dnb", f"930{n}", "Das Lied vom Salztor", "Ada Example", language="de", year=2020 + n,
                       isbns=[f"97830009300{n}0"]) for n in range(3)),
               entry("audible", "B930000001", "Das Lied vom Salztor", "Ada Example", language="de", form="audio",
                     series="Das Lied vom Salztor", series_pos=1.0, year=2024, runtime_min=300,
                     signals={"subtitle": "Die Klippe"}),
               entry("audible", "B930000002", "Das Lied vom Salztor", "Ada Example", language="de", form="audio",
                     series="Das Lied vom Salztor", series_pos=2.0, year=2026, runtime_min=300,
                     signals={"subtitle": "Der Hafen"}),
               # (A library writes the parts of a new edition alike, the part's title and number as the subtitle.)
               *(entry("dnb", f"931{n}", "Das Lied vom Salztor", "Ada Example", language="de", year=2024,
                       series="Das Lied vom Salztor", series_pos=float(n), signals={"subtitle": f"{part}, {n}"})
                 for n, part in ((1, "Die Klippe"), (2, "Der Hafen")))]
    found = hits(entries, "Das Lied vom Salztor Ada Example")
    whole = next(hit for hit in found if hit.title == "Das Lied vom Salztor" and hit.work.series_pos is None)
    assert present.why_not(whole) == "", [(hit.title, hit.work.series_pos, present.why_not(hit)) for hit in found]
    assert all(item.series_pos is None for item in whole.work.entries), "the parts are not the epic"
    assert {"Die Klippe", "Der Hafen"} <= {hit.title for hit in found}


def test_a_volume_named_by_the_whole_is_not_joined_to_the_whole_by_its_title_in_another_language() -> None:
    """The whole carries ``Das Lied vom Salztor`` in German and in English; the shop's volume at place 2 shows ``Das
    Lied vom Salztor: Der Hafen``: the same core, not the same book."""
    works = importlib.import_module("app.services.oeuvre.works")
    whole = works.make_work([entry("dnb", f"933{n}", "Das Lied vom Salztor", "Ada Example", language=code,
                                   signals={"shown_title": "Das Lied vom Salztor"})
                             for n, code in ((0, "de"), (1, "de"), (2, "en"))], author_language="de", pen_names=set())
    volume = works.make_work([entry("audible", "B933000002", "Das Lied vom Salztor", "Ada Example", language="de",
                                    form="audio", series="Das Lied vom Salztor", series_pos=2.0,
                                    signals={"subtitle": "Der Hafen", "shown_title": "Das Lied vom Salztor: Der Hafen"})],
                             author_language="de", pen_names=set())
    assert len(build.join_same_title([whole, volume], author_language="de", author="Ada Example")) == 2


# ---- Who wrote it -----------------------------------------------------------------------------------------------


def test_a_translation_that_open_library_credits_to_its_translator_first_is_the_book() -> None:
    """A German edition at Open Library credited ``Ulla Muster, Ada Example`` (the translator first) of a book Ada
    Example wrote in English: one hit, under her name, not a second one under the translator's."""
    entries = [entry("dnb", f"940{n}", "Die Laternenstraße", "Ada Example", language="de", original_language="en",
                     original_title="The Lantern Road", year=2010 + n, isbns=[f"97830009400{n}0"]) for n in range(3)]
    entries += [entry("audible", "B940000001", "Die Laternenstraße", "Ada Example", language="de", form="audio",
                      runtime_min=600),
                entry("openlibrary", "/works/OL9400W", "Die Laternenstraße", "Ulla Muster", "Ada Example",
                      language="de", work_key="OL9400W", year=2021, signals={"work_level": True}),
                entry("openlibrary", "/books/OL9400M", "Die Laternenstraße", "Ulla Muster", "Ada Example",
                      language="de", work_key="OL9400W", year=2021, isbns=["9798000940001"])]
    found = [hit for hit in hits(entries, "Die Laternenstraße Ada Example") if hit.label == "work"]
    assert [(hit.title, hit.authors) for hit in found] == [("Die Laternenstraße", ["Ada Example"])]


def test_a_retelling_credited_first_to_its_maker_in_the_authors_own_language_stays_apart() -> None:
    """``Rune Probe, Ada Example`` on an English record of a book Ada Example wrote in English is a book Rune Probe
    made after hers (a comic, a reader for learners): not joined to hers."""
    entries = [entry("openlibrary", "/works/OL9500W", "The Lantern Road", "Ada Example", language="en",
                     work_key="OL9500W", year=1950, signals={"work_level": True}),
               *(entry("dnb", f"950{n}", "Die Laternenstraße", "Ada Example", language="de", original_language="en",
                       original_title="The Lantern Road", year=2010 + n) for n in range(2)),
               entry("openlibrary", "/works/OL9501W", "The Lantern Road", "Rune Probe", "Ada Example", language="en",
                     work_key="OL9501W", year=1990, signals={"work_level": True})]
    found = [hit for hit in hits(entries, "The Lantern Road") if hit.label == "work"]
    led = {hit.authors[0] for hit in found}
    assert "Rune Probe" in led, [(hit.title, hit.authors) for hit in found]
    assert all(len({item.authors[0] for item in hit.work.entries}) == 1 for hit in found)


def test_a_book_made_after_another_names_its_maker_and_not_the_author_of_the_other() -> None:
    """``Die Laternenstraße`` by Rune Probe, ``nach dem Roman von Ada Example``: a comic of her book, his alone."""
    entries = [entry("dnb", f"960{n}", "Die Laternenstraße", "Rune Probe", "Ada Example", language="de",
                     year=2024, signals={"subtitle": "nach dem Roman von Ada Example" if n else ""})
               for n in range(3)]
    entries += [entry("dnb", "9609", "Die Laternenstraße", "Ada Example", language="de", year=1990,
                      original_language="en", original_title="The Lantern Road"),
                entry("audible", "B96000001", "Die Laternenstraße", "Lena Muster", "Ada Example", language="de",
                      form="audio", runtime_min=500, signals={"subtitle": "nach dem Roman von Ada Example"})]
    found = [hit for hit in hits(entries, "Die Laternenstraße") if hit.label == "work"]
    assert sorted(hit.authors[0] for hit in found) == ["Ada Example", "Lena Muster", "Rune Probe"]
    assert next(hit for hit in found if hit.authors[0] == "Rune Probe").authors == ["Rune Probe"]


def test_who_retells_books_of_others_is_the_only_author_of_a_retelling() -> None:
    """A library says ``nacherzählt von Rune Probe``; Open Library credits his retelling ``Rune Probe, Ada Example``."""
    entries = [entry("dnb", "9700", "Ada Example", "Rune Probe", language="de", year=2024,
                     signals={"credits": "nacherzählt von Rune Probe ; Bilder von Lena Muster"}),
               entry("openlibrary", "/works/OL9700W", "Glanznacht", "Rune Probe", "Ada Example", language="de",
                     work_key="OL9700W", year=2016, signals={"work_level": True}),
               entry("dnb", "9701", "Glanznacht", "Ada Example", language="de", year=1990)]
    found = [hit for hit in hits(entries, "Glanznacht") if hit.label == "work"]
    assert ("Glanznacht", ["Rune Probe"]) in [(hit.title, hit.authors) for hit in found]


def test_a_play_a_library_credits_first_to_its_maker_and_a_guide_publisher_stay_apart() -> None:
    """A library's record ``Rune Probe, Ada Example`` (a stage version of her book) and ``Lernhilfe Verlag, Ada
    Example`` at Open Library are no editions of her book; a house of audiobooks credited first on a recording of her
    book is no author (one word: ``Hörbuchzeitraum``)."""
    entries = [*(entry("dnb", f"985{n}", "Die Laternenstraße", "Ada Example", language="de", year=2000 + n,
                       original_language="en", original_title="The Lantern Road") for n in range(3)),
               entry("dnb", "9859", "Die Laternenstraße", "Rune Probe", "Ada Example", language="de", year=2011),
               entry("openlibrary", "/works/OL9850W", "Die Laternenstraße", "Lernhilfe Verlag", "Ada Example",
                     language="de", work_key="OL9850W", signals={"work_level": True}),
               entry("audible", "B985000001", "Die Laternenstraße", "Hörbuchzeitraum", "Ada Example", language="de",
                     form="audio", runtime_min=500)]
    found = [hit for hit in hits(entries, "Die Laternenstraße Ada Example") if hit.label == "work"]
    book = next(hit for hit in found if hit.authors[0] == "Ada Example")
    keys = {item.key for item in book.work.entries}
    assert "B985000001" in keys and "9859" not in keys and "/works/OL9850W" not in keys, [
        (hit.title, hit.authors, sorted(item.key for item in hit.work.entries)) for hit in found]


def test_a_house_of_audiobooks_credited_first_in_the_authors_own_language_is_no_author() -> None:
    entries = [*(entry("dnb", f"986{n}", "Die Laternenstraße", "Ada Example", language="de", year=2000 + n)
                 for n in range(2)),
               entry("audible", "B986000001", "Die Laternenstraße", "Hörbuchzeitraum", "Ada Example", language="de",
                     form="audio", runtime_min=500)]
    first = hits(entries, "Die Laternenstraße Ada Example")[0]
    assert first.authors == ["Ada Example"] and "B986000001" in {item.key for item in first.work.entries}


def test_the_one_a_book_was_written_after_an_idea_of_comes_next_to_its_author() -> None:
    """The library says ``nach einer Idee von Ada Example``, the shop names her: she is shown beside Rune Probe."""
    entries = [entry("dnb", f"980{n}", "Tinnit", "Rune Probe", language="de", year=2019,
                     signals={"subtitle": "Thriller : nach einer Idee von Ada Example"}) for n in range(4)]
    entries += [entry("audible", "B980000001", "Tinnit", "Ada Example", "Rune Probe", language="de", form="audio",
                      runtime_min=400),
                entry("audible", "B980000002", "Tinnit 2", "Rune Probe", "Ada Example", language="de", form="audio",
                      runtime_min=420),
                entry("openlibrary", "/works/OL9800W", "Tinnit", "Rune Probe", language="de", work_key="OL9800W",
                      signals={"work_level": True})]
    first = hits(entries, "Tinnit Ada Example")[0]
    assert first.authors == ["Rune Probe", "Ada Example"]


def test_initials_written_twice_are_a_name_and_a_middle_initial_is_the_same_person() -> None:
    assert build.names_of(entry("dnb", "1", "x", "George R. R. Example")) == ["George R. R. Example"]
    assert authors_compatible("Rune Falkner", "Rune K. Falkner")
    assert authors_compatible("R. K. K. Falkner", "R. K. Falkner")
    assert not authors_compatible("Rune Falkner", "Ralf K. Falkner")


# ---- The series ------------------------------------------------------------------------------------------------


def test_the_series_is_named_in_the_language_of_the_title() -> None:
    """The work's series is ``The Lights of Varn`` at the catalogue and the English shop; the German records say ``Die
    Lichter von Varn``, and they number it as the German edition does."""
    entries = [entry("audible", f"B99{n}", title, "Ada Example", language="en", form="audio", series="The Lights of Varn",
                     series_pos=float(n), runtime_min=600) for n, title in ((1, "Ember"), (2, "Ash"))]
    entries += [entry("audible", f"B98{n}", title, "Ada Example", language="de", form="audio",
                      series="Die Lichter von Varn", series_pos=float(n), runtime_min=600, original_title=original,
                      original_language="en") for n, title, original in ((1, "Glut", "Ember"), (2, "Asche", "Ash"))]
    entries += [entry("dnb", f"99{n}", title, "Ada Example", language="de", series="Die Lichter von Varn",
                      series_pos=float(n), original_title=original, original_language="en")
                for n, title, original in ((1, "Glut", "Ember"), (2, "Asche", "Ash"))]
    first = hits(entries, "Glut Ada Example")[0]
    assert (first.title, first.work.series, first.work.series_pos) == ("Glut", "Die Lichter von Varn", 1)


def test_a_line_that_names_one_book_of_the_author_is_no_series() -> None:
    """A shop's line of readings (``Hörbuch-Klassiker 12``) and a library's line that is no series of the author
    (``Verfilmt von Rune Probe 12``) name one book: no series."""
    entries = [entry("audible", "B9900001", "Die Laternenstraße", "Ada Example", "Lena Muster", language="de",
                     form="audio", series="Vorgelesen von Lena Muster", series_pos=12.0, runtime_min=900),
               entry("dnb", "9910", "Die Laternenstraße", "Ada Example", language="de", series="Verfilmt von Rune Probe",
                     series_pos=12.0),
               *(entry("dnb", f"991{n}", "Die Laternenstraße", "Ada Example", language="de", year=2000 + n)
                 for n in range(1, 4))]
    first = hits(entries, "Die Laternenstraße")[0]
    assert (first.work.series, first.work.series_pos) == ("", None)


def test_a_shop_and_a_library_that_name_one_series_more_or_less_fully_name_a_series() -> None:
    entries = [entry("audible", "B9920001", "Das Moorhaus", "Ada Example", language="de", form="audio",
                     series="Kommissar Lund", series_pos=1.0, runtime_min=700),
               entry("dnb", "9920", "Das Moorhaus", "Ada Example", language="de", series="Krimi-Reihe um Kommissar Lund",
                     series_pos=1.0),
               entry("dnb", "9921", "Das Moorhaus", "Ada Example", language="de")]
    first = hits(entries, "Das Moorhaus")[0]
    assert (first.work.series, first.work.series_pos) == ("Kommissar Lund", 1)


def test_a_reading_method_is_a_publishers_line() -> None:
    from app.services import series_names
    assert series_names.is_line("Die Laternenstraße. Lesemethode von Rune Probe", 1.0, library=True)


# ---- The title -------------------------------------------------------------------------------------------------


def test_a_translation_that_keeps_the_original_title_shows_its_own_when_that_was_typed() -> None:
    """``Lantern Girl`` in German is ``Lantern Girl - Das stille Haus``: whoever types the German title sees it."""
    entries = [entry("openlibrary", "/works/OL9930W", "Lantern Girl", "Ada Example", language="en",
                     work_key="OL9930W", year=2011, signals={"work_level": True}),
               entry("audible", "B993000001", "Lantern Girl", "Ada Example", language="de", form="audio",
                     original_title="Lantern Girl", original_language="en", runtime_min=900,
                     signals={"subtitle": "Das stille Haus"}),
               *(entry("dnb", f"993{n}", "Lantern Girl", "Ada Example", language="de", original_language="en",
                       original_title="Lantern Girl", signals={"subtitle": "das stille Haus ; Roman"}) for n in range(2))]
    assert hits(entries, "Das stille Haus Ada Example")[0].title == "Lantern Girl - Das stille Haus"
    assert hits(entries, "Lantern Girl Ada Example")[0].title == "Lantern Girl"


def test_letters_in_brackets_inside_a_word_are_part_of_the_word() -> None:
    assert core_title("Bis(s) zum Morgenlicht") == "Biss zum Morgenlicht"
    assert compare_key("Bis(s) zum Morgenlicht") == compare_key("Biss zum Morgenlicht")
    entries = [entry("dnb", f"994{n}", "Biss zum Morgenlicht", "Ada Example", language="de",
                     signals={"raw_title": "Bis(s) zum Morgenlicht"} if n == 0 else {}) for n in range(2)]
    assert hits(entries, "Biss zum Morgenlicht")[0].title == "Bis(s) zum Morgenlicht"


def test_a_fraction_is_a_number_of_its_own_and_a_lost_umlaut_still_says_study_guide() -> None:
    assert fold("Die 13½ Leben") == fold("Die 13 1/2 Leben")
    assert build.pile_of(entry("openlibrary", "1", "Erla uterungen zu Ada Example, Die Laternenstraße",
                               "Rune Probe")) == "guide"


def test_a_series_and_a_title_behind_a_dash_is_the_book_of_that_series() -> None:
    """``Mias Heft - Von Riesen umzingelt!`` of a library that names no series, beside the volumes of ``Mias Heft``:
    the book ``Von Riesen umzingelt!``, one hit with the others' records of it."""
    entries = [entry("dnb", f"995{n}", "Von Riesen umzingelt!", "Ada Example", language="de", series="Mias Heft",
                     series_pos=1.0) for n in range(2)]
    entries += [entry("dnb", "9952", "Mias Heft", "Ada Example", language="de",
                      signals={"full_title": "Mias Heft - Von Riesen umzingelt!"}),
                entry("dnb", "9953", "Mias Film-Heft", "Ada Example", language="de",
                      signals={"full_title": "Mias Film-Heft - Von Riesen umzingelt!"}),
                entry("dnb", "9954", "Gibt's Ärger?", "Ada Example", language="de", series="Mias Heft",
                      series_pos=2.0)]
    found = [hit for hit in hits(entries, "Mias Heft Von Riesen umzingelt") if hit.label == "work"]
    book = named(found, "Von Riesen umzingelt!")
    assert len(book) == 1 and "9952" in {item.key for item in book[0].work.entries}, [
        (hit.title, [item.key for item in hit.work.entries]) for hit in found]
    assert not named(found, "Mias Heft")


def test_a_book_known_only_by_its_translations_and_the_book_itself_are_one() -> None:
    """The translations name the original ``Glanz : das erste Buch der Nacht``; the German records are ``Glanz - Das
    erste Buch der Nacht`` and the shop's ``Das erste Buch der Nacht``: one book."""
    entries = [entry("dnb", f"996{n}", title, "Ada Example", language=code, original_title="Glanz : das erste Buch der Nacht",
                     original_language="de") for n, title, code in ((0, "Glans", "nl"), (1, "Lustre", "fr"))]
    entries += [entry("dnb", "9962", "Glanz", "Ada Example", language="de",
                      signals={"full_title": "Glanz - Das erste Buch der Nacht"}),
                entry("audible", "B996000003", "Das erste Buch der Nacht", "Ada Example", language="de", form="audio",
                      series="Die Glanz-Trilogie", series_pos=1.0, runtime_min=600),
                entry("dnb", "9964", "Dämmerung", "Ada Example", language="de", series="Glanz", series_pos=1.0,
                      signals={"subtitle": "das erste Buch der Nacht"})]
    found = [hit for hit in hits(entries, "Glanz Das erste Buch der Nacht Ada Example") if hit.label == "work"]
    assert not named(found, "Glanz"), [(hit.title, sorted(hit.work.titles)) for hit in found]


def test_a_work_known_by_its_translations_names_the_title_the_book_itself_carries() -> None:
    """The translations name ``Glutrot`` as their original; the German records of the book carry it: the grouping made
    two works of them (the translations stand under places of their own series), they are one book."""
    works = importlib.import_module("app.services.oeuvre.works")
    translations = works.make_work([entry("dnb", f"9961{n}", title, "Ada Example", language=code,
                                          original_title="Glutrot", original_language="de", series=line,
                                          series_pos=1.0)
                                    for n, title, code, line in ((0, "Rouge braise", "fr", "Trilogie de braise"),
                                                                 (1, "Gloedrood", "nl", "Gloed"))],
                                   author_language="de", pen_names=set())
    book = works.make_work([entry("dnb", f"9962{n}", "Glutrot", "Ada Example", language="de",
                                  series="Die Glut-Reihe", series_pos=1.0, year=2010 + n) for n in range(2)],
                           author_language="de", pen_names=set())
    joined = build.join_alike([translations, book], author_language="de", author="Ada Example")
    assert len(joined) == 1 and len(joined[0].entries) == 4


def test_a_work_of_a_catalogue_that_names_no_language_and_the_book_of_its_title_are_one() -> None:
    """Open Library's work ``Glutrot`` names no language (its editions are French and Dutch); the German records carry
    ``Glutrot``: one book. A short title says too little for that (``Glut``)."""
    works = importlib.import_module("app.services.oeuvre.works")

    def two(title: str) -> list[Any]:
        catalogue = works.make_work([entry("openlibrary", "/works/OL9970W", title, "Ada Example", language="und",
                                           work_key="OL9970W", signals={"work_level": True}),
                                     entry("dnb", "99701", "Rouge braise", "Ada Example", language="fr")],
                                    author_language="de", pen_names=set())
        book = works.make_work([entry("dnb", f"9971{n}", title, "Ada Example", language="de") for n in range(2)],
                               author_language="de", pen_names=set())
        return build.join_same_title([catalogue, book], author_language="de", author="Ada Example")

    assert len(two("Glutrot")) == 1
    assert len(two("Glut")) == 2


def test_two_volumes_that_share_the_core_of_their_titles_carry_their_whole_titles() -> None:
    """``Glut: Der Wüstenweg`` and ``Glut: Der Bote``: the work of each carries its whole title in the language, which
    a wish for it looks for, not the core both share."""
    entries = [entry("audible", f"B9970000{n}", "Glut", "Ada Example", language="de", form="audio", series="Glut",
                     series_pos=float(n), signals={"subtitle": subtitle}, runtime_min=600)
               for n, subtitle in ((1, "Der Wüstenweg"), (2, "Der Bote"))]
    found = hits(entries, "Glut Ada Example")
    assert [hit.work.titles["de"].title for hit in found] == ["Glut: Der Wüstenweg", "Glut: Der Bote"]


def test_a_library_record_of_a_part_named_by_a_word_is_the_part_of_its_book() -> None:
    record = entry("dnb", "1", "Erster Teil", "Ada Example", language="de", series="Mondspiel", series_pos=1.0)
    build.tidy(record, ["Ada Example"])
    assert record.title == "Mondspiel. Erster Teil"
    record = entry("dnb", "2", "Mondspiel. Des Spiels erster Teil", "Ada Example", language="de", series="Mondspiel",
                   series_pos=1.0)
    build.tidy(record, ["Ada Example"])
    assert record.title == "Mondspiel. Des Spiels erster Teil"


def test_what_a_book_was_written_after_is_no_part_of_its_title() -> None:
    record = entry("audible", "1", "Tinnit. Nach einer Idee von Ada Example", "Rune Probe", language="de",
                   form="audio")
    build.tidy(record, ["Rune Probe"])
    assert record.title == "Tinnit"
    record = entry("dnb", "2", "Glutstein 451", "Rune Probe", language="de",
                   signals={"subtitle": "nach dem Roman von Ada Example"})
    build.tidy(record, ["Rune Probe"])
    assert (record.title, record.series, record.series_pos) == ("Glutstein 451", "", None)


# ---- What a hit is ---------------------------------------------------------------------------------------------


def test_a_reading_sold_in_numbered_parts_is_parts_of_the_book() -> None:
    entries = [entry("audible", f"B997{n}", f"Die Laternenstraße {n}", "Ada Example", language="de", form="audio",
                     series="Die Laternenstraße", series_pos=float(n), runtime_min=200) for n in (1, 2, 3)]
    entries += [entry("dnb", f"997{n}", "Die Laternenstraße", "Ada Example", language="de") for n in range(2)]
    found = hits(entries, "Die Laternenstraße")
    assert [hit.label for hit in found if "Laternenstraße " in hit.title] == ["part"] * 3


def test_a_short_recording_with_a_cast_is_a_play() -> None:
    record = entry("audible", "1", "Die kleine Laterne", "Ada Example", form="audio", runtime_min=60,
                   signals={"narrators": ["Lena Muster", "Rune Probe", "Ulla Test"]})
    assert build.pile_of(record) == "drama"
    record.runtime_min = 600
    assert build.pile_of(record) == "work"


def test_the_volumes_of_one_edition_beside_the_whole_are_parts() -> None:
    """A library's edition in three volumes of one year, each with the title of the whole, and the whole: the volumes
    are parts, the whole is the book."""
    entries = [entry("dnb", f"998{n}", "Die Sagen vom Salztor", "Ada Example", language="de", year=2022,
                     series="Sagen vom Salztor", series_pos=float(n)) for n in (1, 2, 3)]
    entries += [entry("dnb", f"9989{n}", "Die Sagen vom Salztor", "Ada Example", language="de", year=1990 + n)
                for n in range(3)]
    found = hits(entries, "Die Sagen vom Salztor")
    assert [(hit.label, hit.work.series_pos) for hit in found if hit.label == "work"] == [("work", None)]
    assert sorted(hit.work.series_pos for hit in found if hit.label == "part") == [1, 2, 3]


def test_a_librarys_recording_at_another_place_than_the_book_is_an_episode_of_a_play() -> None:
    entries = [entry("audible", "B9990001", "Der Hund vom Moorhaus", "Ada Example", language="de", form="audio",
                     series="Kommissar Lund", series_pos=3.0, variant="unabridged", runtime_min=400),
               entry("dnb", "9990", "Der Hund vom Moorhaus", "Ada Example", language="de"),
               *(entry("dnb", f"999{n}", "Der Hund vom Moorhaus", "Ada Example", language="de", form="audio",
                       series="Kommissar Lund", series_pos=18.0,
                       signals={"full_title": "Der Hund vom Moorhaus : empfohlen ab 12 Jahre"}) for n in (1, 2))]
    found = hits(entries, "Der Hund vom Moorhaus")
    assert [(hit.label, hit.work.series_pos) for hit in found] == [("work", 3), ("drama", 18)]


def test_retellings_crafts_and_comics_a_series_names_are_set_apart() -> None:
    assert build.pile_of(entry("openlibrary", "1", "The Lantern Road (adaptation)", "Rune Probe")) == "companion"
    assert build.pile_of(entry("dnb", "2", "Die Laternenstraße", "Rune Probe",
                               signals={"subtitle": "Basteln, spielen | Kreativbuch zum Klassiker"})) == "companion"
    assert build.pile_of(entry("dnb", "3", "Die Laternenstraße", "Rune Probe",
                               series="Eine Laternen Graphic Novel", series_pos=3.0)) == "comic"
    work = entry("openlibrary", "/works/OL1W", "The Lantern Road (adaptation)", "Rune Probe", work_key="OL1W")
    edition = entry("openlibrary", "/books/OL1M", "The Lantern Road", "Rune Probe", work_key="OL1W")
    assert build.piles_of([work, edition]) == ["companion", "companion"]


# ---- The sources -----------------------------------------------------------------------------------------------


def test_a_search_given_up_on_while_a_source_still_answered_has_not_failed() -> None:
    """One source failed, the others did not answer before the person gave up (called off): the search did not fail,
    the hits are incomplete."""
    run = ask.Run(ticket="t", uid=1, asked=read("Die Laternenstraße"), languages=["de"], original=False)
    run.states = {"dnb": "failed", "openlibrary:de": "called_off", "audible:de": "called_off", "people": "called_off"}
    assert run.failed is False
    run.states = {"dnb": "failed", "openlibrary:de": "failed", "audible:de": "off", "people": "done"}
    assert run.failed is True
