"""Recordings a browser can really play, made by the test: silence of an exact length in FLAC, Ogg Opus and Ogg Vorbis.
``bookmaker.make_mp3`` already makes MP3 frames a decoder plays as silence; the FLAC and Opus files of ``bookmaker``
are headers without any sound, enough for the scanner but not for a player.

Nothing here is encoded from sound: every frame says "silence" in the simplest way its format has (a FLAC frame of
constant subframes, an Opus packet of a single byte, a Vorbis packet whose floor is unused). The files are small, a
minute of FLAC is about 5 KB, and their length is what the stream says, to the sample.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from .bookmaker import _ogg_crc

# ---- FLAC ------------------------------------------------------------------------------------------------------------

_FLAC_RATE = 44100
_FLAC_BLOCK = 4096


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _utf8_number(value: int) -> bytes:
    """The frame number as FLAC writes it: the way UTF-8 writes a code point."""
    if value < 0x80:
        return bytes([value])
    if value < 0x800:
        return bytes([0xC0 | value >> 6, 0x80 | value & 0x3F])
    if value < 0x10000:
        return bytes([0xE0 | value >> 12, 0x80 | value >> 6 & 0x3F, 0x80 | value & 0x3F])
    return bytes([0xF0 | value >> 18, 0x80 | value >> 12 & 0x3F, 0x80 | value >> 6 & 0x3F, 0x80 | value & 0x3F])


def _flac_frame(number: int, samples: int) -> bytes:
    block_code = 0b1100 if samples == _FLAC_BLOCK else 0b0111
    head = bytes([0xFF, 0xF8, block_code << 4 | 0b1001, 0b0000 << 4 | 0b100 << 1]) + _utf8_number(number)
    if block_code == 0b0111:
        head += struct.pack(">H", samples - 1)
    head += bytes([_crc8(head)])
    # One subframe (mono): constant, no wasted bits, the value 0 in 16 bits.
    frame = head + b"\x00" + b"\x00\x00"
    return frame + struct.pack(">H", _crc16(frame))


def make_flac(path: Path, seconds: float, **tags: Any) -> Path:
    """Mono silence of 16 bits at 44.1 kHz, ``seconds`` long to the sample, with Vorbis comments (``chapters``: a list of
    ``(title, start seconds)`` as ``CHAPTER001``)."""
    total = max(1, round(seconds * _FLAC_RATE))
    packed = (_FLAC_RATE << 44) | (0 << 41) | (15 << 36) | total
    streaminfo = struct.pack(">HH", _FLAC_BLOCK, _FLAC_BLOCK) + bytes(6) + packed.to_bytes(8, "big") + bytes(16)
    frames = []
    left, number = total, 0
    while left > 0:
        size = min(_FLAC_BLOCK, left)
        frames.append(_flac_frame(number, size))
        left -= size
        number += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big") + streaminfo + b"".join(frames))
    from mutagen.flac import FLAC

    audio = FLAC(path)
    audio.add_tags()
    for key, value in tags.items():
        if key == "chapters":
            for index, (name, start) in enumerate(value, start=1):
                moment = f"{int(start // 3600):02d}:{int(start % 3600 // 60):02d}:{int(start % 60):02d}." \
                         f"{round(start % 1 * 1000):03d}"
                audio[f"CHAPTER{index:03d}"] = moment
                audio[f"CHAPTER{index:03d}NAME"] = name
        elif value is not None:
            audio[key] = str(value)
    audio.save()
    return path


# ---- Ogg --------------------------------------------------------------------------------------------------------------

_SERIAL = 0x4E42


def _page(packets: list[bytes], *, granule: int, sequence: int, first: bool = False, last: bool = False) -> bytes:
    """One Ogg page holding whole packets (each under 255 * 255 bytes, all together under 255 lacing values)."""
    lacing: list[int] = []
    for packet in packets:
        lacing += [255] * (len(packet) // 255) + [len(packet) % 255]
    assert len(lacing) <= 255
    header_type = (2 if first else 0) | (4 if last else 0)
    head = b"OggS" + bytes([0, header_type]) + struct.pack("<qIII", granule, _SERIAL, sequence, 0) + bytes([len(lacing)]) \
        + bytes(lacing)
    page = head + b"".join(packets)
    return page[:22] + struct.pack("<I", _ogg_crc(page)) + page[26:]


def _comments(vendor: bytes, tags: dict[str, Any]) -> bytes:
    items = [f"{key.upper()}={value}".encode() for key, value in tags.items() if value is not None]
    out = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(items))
    for item in items:
        out += struct.pack("<I", len(item)) + item
    return out


def _audio_pages(packets: int, packet: bytes, granule_of: Any, sequence: int, per_page: int = 200) -> list[bytes]:
    pages = []
    done = 0
    while done < packets:
        count = min(per_page, packets - done)
        done += count
        pages.append(_page([packet] * count, granule=granule_of(done), sequence=sequence, last=done == packets))
        sequence += 1
    return pages


def make_opus(path: Path, seconds: float, **tags: Any) -> Path:
    """Ogg Opus, mono, ``seconds`` of silence: packets of 20 ms that hold a single byte (a frame of no length, which a
    decoder plays as silence). The end position says the length to the sample."""
    pre_skip = 312
    total = max(1, round(seconds * 48000))
    packets = -(-(total + pre_skip) // 960)
    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", pre_skip, 48000, 0, 0)
    out = [_page([head], granule=0, sequence=0, first=True),
           _page([b"OpusTags" + _comments(b"nexbooks test", tags)], granule=0, sequence=1)]
    # TOC byte: configuration 31 (CELT, full band, 20 ms), mono, one frame; nothing after it.
    out += _audio_pages(packets, bytes([31 << 3]), lambda done: min(done * 960, total + pre_skip), 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(out))
    return path


class _Bits:
    """Bits written the way Vorbis packs them: the lowest bit of a value first, into the lowest free bit of the byte."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.used = 0

    def put(self, value: int, count: int) -> None:
        for index in range(count):
            if self.used % 8 == 0:
                self.data.append(0)
            if value >> index & 1:
                self.data[-1] |= 1 << (self.used % 8)
            self.used += 1


_VORBIS_RATE = 44100
_SHORT, _LONG = 8, 12  # blocks of 256 and 4096 samples (powers of two)


def _vorbis_setup() -> bytes:
    bits = _Bits()
    bits.put(0, 8)  # one codebook
    bits.put(0x564342, 24)
    bits.put(1, 16)  # dimensions
    bits.put(2, 24)  # entries
    bits.put(0, 1)  # not ordered
    bits.put(0, 1)  # not sparse
    bits.put(0, 5)  # length 1
    bits.put(0, 5)  # length 1
    bits.put(0, 4)  # no lookup
    bits.put(0, 6)  # one time transform, unused
    bits.put(0, 16)
    bits.put(0, 6)  # one floor, of type 1, without partitions
    bits.put(1, 16)
    bits.put(0, 5)
    bits.put(0, 2)  # multiplier 1
    bits.put(8, 4)  # range bits
    bits.put(0, 6)  # one residue, of type 0, empty
    bits.put(0, 16)
    bits.put(0, 24)
    bits.put(0, 24)
    bits.put(15, 24)  # partitions of 16
    bits.put(0, 6)  # one classification
    bits.put(0, 8)  # its book
    bits.put(0, 3)  # no cascade
    bits.put(0, 1)
    bits.put(0, 6)  # one mapping, of type 0
    bits.put(0, 16)
    bits.put(0, 1)  # one submap
    bits.put(0, 1)  # no coupling
    bits.put(0, 2)
    bits.put(0, 8)  # submap: time (unused), floor 0, residue 0
    bits.put(0, 8)
    bits.put(0, 8)
    bits.put(0, 6)  # one mode: long blocks, mapping 0
    bits.put(1, 1)
    bits.put(0, 16)
    bits.put(0, 16)
    bits.put(0, 8)
    bits.put(1, 1)  # framing
    return b"\x05vorbis" + bytes(bits.data)


def make_vorbis(path: Path, seconds: float, **tags: Any) -> Path:
    """Ogg Vorbis, mono, 44.1 kHz, ``seconds`` of silence: every audio packet is one byte, a long block whose floor is
    unused. The end position says the length to the sample."""
    total = max(1, round(seconds * _VORBIS_RATE))
    half = (1 << _LONG) // 2
    packets = -(-total // half) + 1
    ident = b"\x01vorbis" + struct.pack("<IBIiii", 0, 1, _VORBIS_RATE, 0, 0, 0) + bytes([_LONG << 4 | _SHORT, 1])
    comment = b"\x03vorbis" + _comments(b"nexbooks test", tags) + b"\x01"
    audio = _Bits()
    audio.put(0, 1)  # an audio packet
    audio.put(1, 1)  # long window before
    audio.put(1, 1)  # and after
    audio.put(0, 1)  # the floor of the channel is unused: silence
    out = [_page([ident], granule=0, sequence=0, first=True), _page([comment, _vorbis_setup()], granule=0, sequence=1)]
    out += _audio_pages(packets, bytes(audio.data), lambda done: min(max(done - 1, 0) * half, total), 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(out))
    return path
