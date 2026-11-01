"""Torrent files and magnet links: the info hash, and no path that leaves the torrent's folder."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from app.services.procure import torrent


def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b"e"


def test_the_info_hash_is_the_sha1_of_the_info_dictionary_as_it_stands() -> None:
    info = {"name": "Die.Laternenstrasse", "piece length": 16384, "pieces": b"x" * 20,
            "files": [{"path": ["a.epub"], "length": 10}, {"path": ["Extra", "b.jpg"], "length": 5}]}
    found = torrent.read_torrent(bencode({"announce": "https://tracker.example.com/a", "info": info}))
    assert found.info_hash == hashlib.sha1(bencode(info), usedforsecurity=False).hexdigest()
    assert found.paths == ("Die.Laternenstrasse/a.epub", "Die.Laternenstrasse/Extra/b.jpg")
    assert found.size_bytes == 15


@pytest.mark.parametrize("bad", ["..", ".", "", "a/b", "a" + chr(92) + "b", "C:x", "a" + chr(0) + "b"])
def test_a_name_that_could_leave_the_folder_makes_the_torrent_invalid(bad: str) -> None:
    info = {"name": "Book", "piece length": 1, "pieces": b"x" * 20, "files": [{"path": [bad], "length": 1}]}
    with pytest.raises(torrent.TorrentInvalid):
        torrent.read_torrent(bencode({"info": info}))


@pytest.mark.parametrize("data", [b"", b"x", b"d4:infoi1ee", b"d4:info" + b"l" * 200, b"i1e", b"d3:abc"])
def test_anything_else_is_invalid(data: bytes) -> None:
    with pytest.raises(torrent.TorrentInvalid):
        torrent.read_torrent(data)


def test_the_hash_of_a_magnet_link_in_hex_and_in_base32() -> None:
    assert torrent.magnet_hash("magnet:?xt=urn:btih:" + "A" * 40 + "&dn=x") == "a" * 40
    assert torrent.magnet_hash("magnet:?xt=urn:btih:" + "A" * 32) == "00" * 20
    assert torrent.magnet_hash("magnet:?dn=only-a-name") is None
    assert torrent.magnet_hash("https://example.com/x.torrent") is None
