"""Text and names: what is repaired, what is folded, which titles and authors are one and which are not."""

from __future__ import annotations

import pytest

from app.services import genres
from app.services.readers import names, text

# Characters that break when a name is read with the wrong code page, written as numbers so that this file is plain.
BULLET = chr(0x2022)
GAMMA_BULLET = chr(0x393) + chr(0xC7) + chr(0xF3)  # UTF-8 "bullet" read as code page 437
AUMLAUT = chr(0xE4)
UUMLAUT = chr(0xFC)
EN_DASH = chr(0x2013)


def test_a_name_read_with_the_wrong_code_page_is_put_right() -> None:
    broken = f"Falkner, Rune - [Anselm Quist 01] {GAMMA_BULLET} Nebelchor"
    assert text.repair(broken) == f"Falkner, Rune - [Anselm Quist 01] {BULLET} Nebelchor"
    good = f"Das verlorene Siegel {BULLET} Teil {EN_DASH} Ende"
    assert text.repair(good.encode("utf-8").decode("cp437")) == good
    assert text.repair(good.encode("utf-8").decode("cp1252", "ignore")) == good.replace(EN_DASH, EN_DASH)


def test_a_name_with_umlauts_and_no_sign_of_damage_is_left_alone() -> None:
    plain = f"G{AUMLAUT}rtner M{UUMLAUT}ller"
    assert text.repair(plain) == plain
    assert text.clean(plain) == plain


def test_a_name_damaged_in_one_place_loses_the_damage_and_keeps_the_umlauts() -> None:
    mixed = f"G{AUMLAUT}rtner {GAMMA_BULLET} Titel"
    assert text.repair(mixed) == f"G{AUMLAUT}rtner {BULLET} Titel"


def test_clean_takes_out_control_characters_and_the_mark_of_a_failed_decoding() -> None:
    assert text.clean("Rufer  der\x00 Quellen" + chr(0xFFFD) + chr(0x200B)) == "Rufer der Quellen"
    assert text.clean(None) == "" and text.clean(5) == "" and text.clean(["x"]) == ""
    assert len(text.clean("a" * 1000, 50)) == 50


def test_fold_compares_without_case_accents_umlauts_and_signs() -> None:
    assert text.fold(f"M{UUMLAUT}ller, Hans {EN_DASH} Der Beschlu{chr(0xDF)}") == "muller hans der beschluss"
    assert text.fold("  Mixed   CASE!! ") == "mixed case"


@pytest.mark.parametrize(("given", "wanted"), [
    ("3999900407", "9783999900407"),
    ("978-3-9999-0030-8", "9783999900308"),
    ("urn:isbn:9783999900209", "9783999900209"),
    ("9783999900200", ""),  # the check digit is wrong
    ("2a788e5b-5584-4dda-b6e4-33fcabccedc0", ""),  # a UUID has digits, and is no ISBN
    ("B0EXAMPL11", ""),
    ("12345", ""),
])
def test_isbn_is_only_a_valid_isbn(given: str, wanted: str) -> None:
    assert text.isbn13(given) == wanted


@pytest.mark.parametrize(("given", "wanted"), [
    ("de-DE", "de"), ("deu", "de"), ("ger", "de"), ("EN", "en"), ("Deutsch", "de"), ("English", "en"), ("", ""),
    ("12", ""), ("xyz-unknown", ""),
])
def test_a_language_becomes_a_short_code(given: str, wanted: str) -> None:
    assert text.language_of(given) == wanted


def test_html_in_a_description_becomes_plain_paragraphs() -> None:
    html = "<p>Ein &amp; Kern</p><div>zwei <b>fett</b></div><script>alert(1)</script><style>p{}</style>"
    assert text.html_to_text(html) == "Ein & Kern\n\nzwei fett"
    assert text.html_to_text("plain &amp; simple") == "plain & simple"
    assert text.html_to_text(5) == "" and text.html_to_text("") == ""
    assert len(text.html_to_text("<p>" + "x " * 10_000 + "</p>")) <= 4000


def test_a_description_keeps_its_paragraphs_and_not_the_wrap_of_its_lines() -> None:
    # Escaped HTML in an OPF with a hard wrap in the text and two line breaks between the paragraphs (as e-books come).
    wrapped = "<div>First line of the blurb, wrapped \nat the width of the file and going on \n<br><br>Second paragraph.</div>"
    assert text.html_to_text(wrapped) == "First line of the blurb, wrapped at the width of the file and going on\n\nSecond paragraph."
    # A single line break, a closing and an opening block, and a break with a slash all start a paragraph.
    assert text.html_to_text("One<br>Two<br/>Three") == "One\n\nTwo\n\nThree"
    assert text.html_to_text("<p>One</p>\n<p>Two</p>") == "One\n\nTwo"
    assert text.html_to_text("<br><br><br>Only<br><br>") == "Only"
    # Plain text: an empty line (even one with blanks in it, or many) starts a paragraph; a single line break stays what
    # it is, for the page to set as a space.
    assert text.html_to_text("Wrapped at\nsixty characters.\n\nNext paragraph.\n \n\n\nThird.") == (
        "Wrapped at\nsixty characters.\n\nNext paragraph.\n\nThird.")
    assert text.html_to_text("Windows\r\nline\r\n\r\nbreaks") == "Windows\nline\n\nbreaks"


# ---- names ---------------------------------------------------------------------------------------------------------


def test_the_same_person_in_both_orders_is_one_person() -> None:
    assert names.person_key("Falkner, Rune") == names.person_key("Rune Falkner")
    assert names.display_name("Falkner, Rune") == "Rune Falkner"
    assert names.sort_name("Rune Falkner") == "Falkner, Rune"
    assert names.sort_name("Ulla K. Le Moor") == "Le Moor, Ulla K."


def test_an_author_is_never_matched_by_the_last_name_alone() -> None:
    """The lesson of the measuring run: Silas Marlowe is not Hedda Thorne, and not every Marlowe."""
    assert names.authors_compatible("Rune Falkner", "Falkner, R.")
    assert names.authors_compatible("J. P. Q. Hartmoor", "John Peter Quill Hartmoor")
    assert not names.authors_compatible("Rune Falkner", "Ralf Falkner")
    assert not names.authors_compatible("Silas Marlowe", "Hedda Thorne")
    assert not names.authors_compatible("Silas Marlowe", "Joe Marlowe")
    assert not names.authors_compatible("Marlowe", "Silas Marlowe")
    # A name of one word on both sides is that name (Homer, Voltaire), unless it is too short to tell anybody apart.
    assert names.authors_compatible("Falkner", "Falkner")
    assert not names.authors_compatible("Ida", "Ida")


def test_authors_in_a_field_are_split_but_a_comma_name_is_one_author() -> None:
    assert names.split_authors("Falkner, Rune") == ["Rune Falkner"]
    assert names.split_authors(["Rune Falkner", "Anna K. Smith & Bob Lee"]) == ["Rune Falkner", "Anna K. Smith", "Bob Lee"]
    assert names.split_authors("A. Smith, B. Jones, C. Lee") == ["A. Smith", "B. Jones", "C. Lee"]
    assert names.split_authors("Unknown") == [] and names.split_authors(None) == [] and names.split_authors(5) == []
    assert names.split_authors("Rune Falkner; rune falkner") == ["Rune Falkner"]


def test_the_noise_around_a_title_does_not_decide_whether_two_titles_are_one() -> None:
    assert names.title_keys("Aschenfeld (Ungek" + chr(0xFC) + "rzt)")[0] == "aschenfeld"
    assert names.title_keys("Aschenfeld - H" + chr(0xF6) + "rbuch")[0] == "aschenfeld"
    assert names.title_keys("Ahnenland - Thriller")[0] == "ahnenland"
    assert names.title_keys("Aschenfeld [Unabridged Audiobook]")[0] == "aschenfeld"
    whole, main = names.title_keys(f"Der G{AUMLAUT}rtner von Aldenmoor {chr(0xB7)} Die wahre Geschichte")
    assert whole == "der gaertner von aldenmoor die wahre geschichte" and main == "der gaertner von aldenmoor"
    # A number in a title stays: volume 2 is not volume 3.
    assert names.title_keys("Band 2")[0] != names.title_keys("Band 3")[0]


def test_a_title_that_carries_its_series_gives_it_up() -> None:
    parts = names.split_series_title(f"[Anselm Quist 01] {BULLET} Nebelchor")
    assert (parts.title, parts.series, parts.pos) == ("Nebelchor", "Anselm Quist", 1.0)
    parts = names.split_series_title("Anselm Quist Bd. 5 - Ahnenland | Thriller")
    assert (parts.title, parts.series, parts.pos) == ("Ahnenland", "Anselm Quist", 5.0)
    parts = names.split_series_title("002 - Frevelpfad", "Anselm Quist", 2.0)
    assert (parts.title, parts.series) == ("Frevelpfad", "")
    # A title is a title: a number at its end is no volume.
    assert names.split_series_title("Lantern 451").title == "Lantern 451"
    assert names.split_series_title("Gambit-17").series == ""
    assert names.split_series_title("2131").title == "2131"
    # A number that is not the volume stays.
    assert names.split_series_title("002 - Frevelpfad", "Anselm Quist", 3.0).title == "002 - Frevelpfad"


def test_a_series_field_gives_name_and_volume() -> None:
    assert names.series_from_field("Anselm Quist #1.0") == ("Anselm Quist", 1.0)
    assert names.series_from_field("Pippo (4)") == ("Pippo", 4.0)
    assert names.series_from_field("Die Chroniken von Arvel") == ("Die Chroniken von Arvel", None)
    assert names.number_of("04") == 4.0 and names.number_of("2,5") == 2.5 and names.number_of("x") is None


def test_the_name_of_a_file_or_folder_tells_what_it_can() -> None:
    parts = names.parse_name("Falkner,.Rune.-.Anselm.Quist.5.-.Ahnenland.-.Thriller")
    assert (parts.author, parts.series, parts.pos, parts.title) == ("Rune Falkner", "Anselm Quist", 5.0, "Ahnenland")
    parts = names.parse_name(f"Falkner, Rune - [Anselm Quist 01] {BULLET} Nebelchor")
    assert (parts.author, parts.series, parts.pos, parts.title) == ("Rune Falkner", "Anselm Quist", 1.0, "Nebelchor")
    parts = names.parse_name("Edda Sandvik - Der Titel - Der Untertitel")
    assert (parts.author, parts.title, parts.extra) == ("Edda Sandvik", "Der Titel", ["Der Untertitel"])
    # No author is invented from a title.
    assert names.parse_name("Der Zeuge - Berta Wiesner").author == ""
    assert names.parse_name("Lantern 451").title == "Lantern 451"
    assert names.parse_name("Lantern 451").series == ""
    assert names.parse_name("") .title == ""


def test_a_cd_folder_is_not_a_book() -> None:
    assert all(names.is_cd_folder(name) for name in ("CD 1", "Disc 02", "Teil 3", "cd2", "03"))
    assert not names.is_cd_folder("Aschenfeld")


# ---- genres --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("subjects", "genre"), [
    (["Roman-Thriller"], "krimi"), (["Thriller/Krimi"], "krimi"), (["Lesbenliteratur"], "roman"), (["Roman"], "roman"),
    (["Science Fiction"], "scifi"), (["Kinderkrimi"], "kinder"), (["Liebesroman"], "roman"), (["Epic Fantasy"], "fantasy"),
    (["Biografie"], "sachbuch"), (["Poetry"], "lyrik"), (["Weltliteratur"], "klassiker"), ([], ""), (["Gartenzwerge"], ""),
])
def test_subjects_map_onto_the_genres_of_the_shelf(subjects: list[str], genre: str) -> None:
    assert genres.genre_of(subjects) == genre


def test_the_look_of_a_spine_is_fixed_by_the_work() -> None:
    assert genres.style_of(7) == genres.style_of(7)
    seen = {genres.style_of(number)[0] for number in range(1, 200)}
    assert seen == {0, 1, 2, 3, 4}
    assert all(0 <= genres.style_of(number)[1] < 1 for number in range(1, 50))
    assert genres.palette_color("krimi", "x") == genres.palette_color("krimi", "x")
    assert genres.palette_color("", "x").startswith("#")


@pytest.mark.parametrize(("written", "wished"), [
    ("Falkner, Erik", "Erik Fälkner"), ("Erik Falkner", "Erik Fälkner"), ("Sjowall, Maj", "Maj Sjöwall"),
    ("Jorg Falkner", "Jörg Falkner"), ("Faelkner, Erik", "Erik Fälkner"), ("Müller, Hanne", "Hanne Muller"),
])
def test_a_name_written_without_the_marks_over_its_letters_is_the_same_name(written: str, wished: str) -> None:
    assert names.authors_compatible(written, wished)
    assert names.authors_compatible(wished, written)


@pytest.mark.parametrize(("one", "other"), [
    ("Hanne Möller", "Hanne Müller"), ("Hanne Bauer", "Hanne Baur"), ("Erik Falkner", "Erika Fälkner"),
    ("Falkner", "Erik Fälkner"),
])
def test_other_names_stay_other_names_without_the_marks(one: str, other: str) -> None:
    assert not names.authors_compatible(one, other)
    assert not names.authors_compatible(other, one)
