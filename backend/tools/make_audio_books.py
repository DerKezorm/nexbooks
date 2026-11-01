"""Invented recordings for trying the player in a browser: silence a browser really plays, of an exact
length, with chapters, in parts, and in a format not every browser knows.

* ``The Quiet Harbour``: an MP3 of 60 seconds with three chapters inside (``CHAP``), and the e-book of the same book
  beside it (one work with two editions: the twins of the page of the book).
* ``Seven Bridges``: three MP3 parts of 15 seconds in one folder (one recording, one line of time over three files).
* ``The Lantern Keeper``: an hour in 38 chapters, at a low bit rate (for the face of a long book).
* ``Night Radio``: Ogg Vorbis of 20 seconds (Chromium and Firefox play it; WebKit does not know Ogg).
* ``Exact Parts``: 80 FLAC parts of exactly 1.3 s (the lengths add up wrongly in floating point: the border of the
  fourth part is 3.9000000000000004 s there, and the chapters start at whole milliseconds as the server counts them).
* ``Long Chapters``: six MP3 parts of 6 seconds that make three chapters (two, three and one file).
* ``Broken Part`` and ``Missing Part``: three MP3 parts of 6 seconds each; the test spoils the second one after the
  scan (damaged, and deleted).

* with ``--parts-out``: ``Der lange Weg (2019)``, the way most audiobooks lie in a folder: twelve MP3 files
  ``001 - Kapitel 1.mp3`` and on, each a chapter of its own, in the folder of the author (e-books and recordings of
  the same folder are another book in another folder; this one is a library of its own so that the counts of the
  other recordings stay as they are).

A development tool; it is not part of the image. Nothing here is a real book or a real voice.

    python tools/make_audio_books.py --out <folder>
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from tests import audiomaker, bookmaker

#: MPEG 1 layer 3 at 44.1 kHz: a frame is 1152 samples (26.12 ms). The header byte with the bit rate, and the frame's
#: size.
_FRAMES = {128: (0x90, 417), 32: (0x10, 104)}


def silent_mp3(path: Path, seconds: float, kbps: int = 128, **tags: object) -> Path:
    """Like ``bookmaker.make_mp3`` (the same tags), at 128 or 32 kbit/s (an hour at 32 is 14 MB)."""
    bookmaker.make_mp3(path, 0.1, **tags)
    tagged = path.read_bytes()
    # The tag of mutagen stands in front of the frames; the frames are written anew for the length asked.
    size = 10 + sum((b & 0x7F) << (7 * (3 - i)) for i, b in enumerate(tagged[6:10])) if tagged[:3] == b"ID3" else 0
    code, length = _FRAMES[kbps]
    frame = b"\xff\xfb" + bytes([code, 0x00]) + bytes(length - 4)
    path.write_bytes(tagged[:size] + frame * max(1, round(seconds / (1152 / 44100))))
    return path


def cover(color: tuple[int, int, int], title: str) -> bytes:
    """A plain invented cover: a colour, a frame and the title in large blocks of light (no font needed)."""
    image = Image.new("RGB", (600, 600), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([30, 30, 570, 570], outline=(255, 255, 255), width=6)
    for row, word in enumerate(title.split()[:3]):
        draw.rectangle([70, 120 + row * 110, 70 + 46 * len(word), 190 + row * 110], fill=(255, 255, 255, 200))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def many(out: Path) -> None:
    """One recording in twelve files, named the way a download names them, a chapter to a file."""
    folder = out / "Mira Hollander" / "Der lange Weg (2019)"
    for number in range(1, 13):
        silent_mp3(folder / f"{number:03d} - Kapitel {number}.mp3", 5.0, album="Der lange Weg", artist="Mira Hollander",
                   title=f"Kapitel {number}", track=number, narrator="Jonas Ebert")
    stamp = time.time() - 3600
    for each in out.rglob("*"):
        os.utime(each, (stamp, stamp))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--parts-out", type=Path, help="a folder for the recording in many files")
    args = parser.parse_args()
    out: Path = args.out
    if args.parts_out is not None:
        many(args.parts_out)
    out.mkdir(parents=True, exist_ok=True)

    harbour = cover((38, 84, 110), "Quiet Harbour")
    bookmaker.make_epub(out / "Harbour" / "the-quiet-harbour.epub", title="The Quiet Harbour",
                        authors=("Ada Feldkamp",), language="en", chapters=("Arrival", "The Pier", "Night"))
    silent_mp3(out / "Harbour Audio" / "the-quiet-harbour.mp3", 60.0, album="The Quiet Harbour",
               artist="Ada Feldkamp", title="The Quiet Harbour", narrator="Ben Hollister", cover=harbour,
               chapters=[("Arrival", 0.0), ("The Pier", 20.0), ("Night", 40.0)])
    for number in (1, 2, 3):
        silent_mp3(out / "Seven Bridges" / f"{number:02d}.mp3", 15.0, album="Seven Bridges", artist="Clara Wendt",
                   title=f"Bridge {number}", track=number, narrator="Iris Lindqvist",
                   cover=cover((122, 62, 48), "Seven Bridges"))
    names = ["The Keeper", "Salt", "The Logbook", "Fog", "A Visitor", "Oil and Wick", "The Storm", "Morning"]
    chapters = [(f"{n + 1}. {names[n % len(names)]}", n * 3600 / 38) for n in range(38)]
    silent_mp3(out / "Lantern Keeper" / "the-lantern-keeper.mp3", 3600.0, kbps=32, album="The Lantern Keeper",
               artist="Greta Holm", title="The Lantern Keeper", narrator="Theo Marlow",
               cover=cover((150, 108, 40), "Lantern Keeper"), chapters=chapters)
    audiomaker.make_vorbis(out / "Night Radio" / "night-radio.ogg", 20.0, album="Night Radio", artist="Mara Quist",
                           title="Night Radio")

    for number in range(1, 81):
        audiomaker.make_flac(out / "Exact Parts" / f"{number:02d}.flac", 1.3, album="Exact Parts", artist="Ida Ross",
                             title=f"Slice {number}", track=number, narrator="Ben Hart")
    for name in ("Broken Part", "Missing Part"):
        for number in (1, 2, 3):
            silent_mp3(out / name / f"{number:02d}.mp3", 6.0, album=name, artist="Pia Wren", title=f"Part {number}",
                       track=number, narrator="Max Roth")

    # Chapters that run over several files: three chapters in six parts (``chapter_groups``).
    parts = ["Opening (1/2)", "Opening (2/2)", "Crossing - Part 1", "Crossing - Part 2", "Crossing - Part 3", "Finale"]
    for number, name in enumerate(parts, start=1):
        silent_mp3(out / "Long Chapters" / f"{number:02d}.mp3", 6.0, album="Long Chapters", artist="Pia Wren",
                   title=name, track=number, narrator="Max Roth")

    # The scan leaves a file that was changed a moment ago for its next run: these are old enough.
    stamp = time.time() - 3600
    for each in out.rglob("*"):
        os.utime(each, (stamp, stamp))
    print(f"{sum(1 for p in out.rglob('*') if p.is_file())} files in {out}")


if __name__ == "__main__":
    main()
