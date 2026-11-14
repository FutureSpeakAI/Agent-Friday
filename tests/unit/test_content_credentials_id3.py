"""The audio credential is written without a GPL tag library (replaces mutagen).

`content_credentials._embed_id3` puts the manifest JSON into an MP3 or WAV as an ID3 TXXX frame
named "ContentCredential". It used to need `mutagen` (GPL-2.0+); with the package absent it
silently embedded nothing. These tests block `mutagen` outright and read the result back with a
small independent ID3 reader written here from the ID3v2 spec, so they fail if the writer drops
the frame, damages the audio, loses the tags already in the file, or writes a malformed tag.
All audio is synthetic.
"""
from __future__ import annotations

import re
import sys

import pytest

from agent_friday.services import content_credentials as cc

MANIFEST = '{"type":"ContentCredential","creator":"Friday","note":"café — ☃"}'
AUDIO = bytes(range(256)) * 8          # stands in for the MPEG frames / PCM samples


@pytest.fixture(autouse=True)
def no_mutagen(monkeypatch):
    """The point: nothing here may need the GPL package."""
    monkeypatch.setitem(sys.modules, "mutagen", None)
    for name in [m for m in sys.modules if m.startswith("mutagen.")]:
        monkeypatch.setitem(sys.modules, name, None)


# ── an independent reader ────────────────────────────────────────────────────

def _ss(b):
    assert all(x < 0x80 for x in b), "size bytes must be synchsafe"
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _read_tag(data: bytes):
    """-> (version, [(frame id, payload)], offset where the audio starts)"""
    assert data[:3] == b"ID3", "no ID3 header"
    ver, flags, size = data[3], data[5], _ss(data[6:10])
    assert ver in (3, 4) and flags & 0xC0 == 0
    body, pos, frames = data[10:10 + size], 0, []
    assert len(body) == size, "tag size runs past the data"
    while pos + 10 <= len(body) and body[pos] != 0:
        fid = body[pos:pos + 4].decode("ascii")
        n = _ss(body[pos + 4:pos + 8]) if ver == 4 else int.from_bytes(body[pos + 4:pos + 8], "big")
        frames.append((fid, body[pos + 10:pos + 10 + n]))
        pos += 10 + n
    assert pos == len(body) or body[pos] == 0
    return ver, frames, 10 + size


def _txxx(payload: bytes):
    enc, rest = payload[0], payload[1:]
    if enc == 3:
        desc, _, text = rest.partition(b"\x00")
        return desc.decode("utf-8"), text.decode("utf-8")
    assert enc == 1, enc                                   # v2.3: UTF-16 with a BOM
    i = 0
    while rest[i:i + 2] != b"\x00\x00":
        i += 2
    return rest[:i].decode("utf-16"), rest[i + 2:].decode("utf-16")


def _credentials(frames):
    return [_txxx(p)[1] for fid, p in frames if fid == "TXXX" and _txxx(p)[0] == "ContentCredential"]


def _frame4(fid: bytes, payload: bytes) -> bytes:
    n = len(payload)
    return fid + bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F]) + b"\x00\x00" + payload


def _frame3(fid: bytes, payload: bytes) -> bytes:
    return fid + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _tag(ver: int, frames: bytes, padding: int = 0, flags: int = 0) -> bytes:
    n = len(frames) + padding
    return b"ID3" + bytes([ver, 0, flags]) + bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F]) \
        + frames + b"\x00" * padding


def _wav(*chunks: bytes) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def _chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + len(payload).to_bytes(4, "little") + payload + (b"\x00" if len(payload) & 1 else b"")


FMT = _chunk(b"fmt ", b"\x01\x00\x01\x00\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00")
DATA = _chunk(b"data", AUDIO)


def _chunks(data: bytes):
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert int.from_bytes(data[4:8], "little") == len(data) - 8, "RIFF size must match the file"
    out, pos = [], 12
    while pos < len(data):
        sz = int.from_bytes(data[pos + 4:pos + 8], "little")
        out.append((data[pos:pos + 4], data[pos + 8:pos + 8 + sz]))
        pos += 8 + sz + (sz & 1)
    assert pos == len(data), "chunks must end exactly at the end of the file"
    return out


# ── MP3 ──────────────────────────────────────────────────────────────────────

def test_an_mp3_with_no_tag_gets_the_credential_and_keeps_its_audio(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(AUDIO)
    assert cc._embed_id3(p, MANIFEST) is True
    data = p.read_bytes()
    ver, frames, start = _read_tag(data)
    assert ver == 4 and _credentials(frames) == [MANIFEST]
    assert data[start:] == AUDIO, "the audio after the tag must be untouched, byte for byte"


def test_the_tags_already_in_an_mp3_are_kept(tmp_path):
    p = tmp_path / "a.mp3"
    title = _frame4(b"TIT2", b"\x03Song title")
    artist = _frame4(b"TPE1", b"\x03The Band")
    p.write_bytes(_tag(4, title + artist, padding=64) + AUDIO)
    assert cc._embed_id3(p, MANIFEST) is True
    data = p.read_bytes()
    _, frames, start = _read_tag(data)
    assert [f for f in frames if f[0] != "TXXX"] == [("TIT2", b"\x03Song title"), ("TPE1", b"\x03The Band")]
    assert _credentials(frames) == [MANIFEST] and data[start:] == AUDIO


def test_an_id3v23_tag_stays_v23_and_still_carries_a_readable_credential(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(_tag(3, _frame3(b"TIT2", b"\x00Old song")) + AUDIO)
    assert cc._embed_id3(p, MANIFEST) is True
    data = p.read_bytes()
    ver, frames, start = _read_tag(data)
    assert ver == 3 and ("TIT2", b"\x00Old song") in frames
    assert _credentials(frames) == [MANIFEST] and data[start:] == AUDIO


def test_embedding_twice_leaves_one_credential_the_latest(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(AUDIO)
    assert cc._embed_id3(p, '{"n":1}') and cc._embed_id3(p, '{"n":2}')
    _, frames, start = _read_tag(p.read_bytes())
    assert _credentials(frames) == ['{"n":2}']
    assert p.read_bytes()[start:] == AUDIO


def test_another_txxx_frame_is_not_mistaken_for_the_credential(tmp_path):
    p = tmp_path / "a.mp3"
    other = _frame4(b"TXXX", b"\x03Mood\x00calm")
    p.write_bytes(_tag(4, other) + AUDIO)
    assert cc._embed_id3(p, MANIFEST)
    _, frames, _ = _read_tag(p.read_bytes())
    assert ("TXXX", b"\x03Mood\x00calm") in frames and _credentials(frames) == [MANIFEST]


def test_an_id3v1_footer_survives(tmp_path):
    p = tmp_path / "a.mp3"
    v1 = b"TAG" + b"Old title".ljust(30, b"\x00") + b"\x00" * 95
    p.write_bytes(AUDIO + v1)
    assert cc._embed_id3(p, MANIFEST)
    assert p.read_bytes().endswith(AUDIO + v1)


@pytest.mark.parametrize("name,blob", [
    ("v22", b"ID3\x02\x00\x00\x00\x00\x00\x00" + AUDIO),
    ("unsynchronised", _tag(4, _frame4(b"TIT2", b"\x03x"), flags=0x80) + AUDIO),
    ("extended header", _tag(4, b"\x00\x00\x00\x06\x01\x00" + _frame4(b"TIT2", b"\x03x"), flags=0x40) + AUDIO),
    ("tag runs past the file", b"ID3\x04\x00\x00\x00\x00\x7f\x7f" + AUDIO[:20]),
    ("bad frame id", _tag(4, b"\xff\xfe\xfd\xfc\x00\x00\x00\x01\x00\x00x") + AUDIO),
    ("frame runs past the tag", _tag(4, b"TIT2\x00\x00\x7f\x7f\x00\x00x") + AUDIO),
])
def test_a_tag_that_is_not_understood_is_refused_and_the_file_is_left_alone(tmp_path, name, blob):
    p = tmp_path / "a.mp3"
    p.write_bytes(blob)
    assert cc._embed_id3(p, MANIFEST) is False, name
    assert p.read_bytes() == blob, name
    assert [x.name for x in tmp_path.iterdir()] == ["a.mp3"], "no temp file may be left behind"


# ── WAV ──────────────────────────────────────────────────────────────────────

def test_a_wav_gets_an_id3_chunk_and_stays_a_valid_riff(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav(FMT, DATA))
    assert cc._embed_id3(p, MANIFEST) is True
    chunks = _chunks(p.read_bytes())
    assert [c[0] for c in chunks] == [b"fmt ", b"data", b"id3 "]
    assert chunks[0][1] == FMT[8:8 + 16] and chunks[1][1] == AUDIO
    _, frames, _ = _read_tag(chunks[2][1])
    assert _credentials(frames) == [MANIFEST]


def test_a_wav_with_an_odd_sized_chunk_is_padded_correctly(tmp_path):
    p = tmp_path / "a.wav"
    odd = _chunk(b"LIST", b"abc")                      # 3 bytes + a pad byte
    p.write_bytes(_wav(FMT, odd, DATA))
    assert cc._embed_id3(p, MANIFEST) is True
    chunks = _chunks(p.read_bytes())
    assert (b"LIST", b"abc") in chunks and chunks[-1][0] == b"id3 "


def test_embedding_twice_in_a_wav_leaves_one_id3_chunk_with_the_latest_and_other_tags(tmp_path):
    p = tmp_path / "a.wav"
    existing = _tag(4, _frame4(b"TIT2", b"\x03Kept"))
    p.write_bytes(_wav(FMT, _chunk(b"id3 ", existing), DATA))
    assert cc._embed_id3(p, '{"n":1}') and cc._embed_id3(p, '{"n":2}')
    chunks = _chunks(p.read_bytes())
    tags = [c for c in chunks if c[0] == b"id3 "]
    assert len(tags) == 1 and [c[0] for c in chunks if c[0] == b"data"] == [b"data"]
    _, frames, _ = _read_tag(tags[0][1])
    assert ("TIT2", b"\x03Kept") in frames and _credentials(frames) == ['{"n":2}']
    assert dict(chunks)[b"data"] == AUDIO


@pytest.mark.parametrize("name,blob", [
    ("not RIFF", b"not a wav at all" * 4),
    ("riff longer than the file", b"RIFF" + (10_000).to_bytes(4, "little") + b"WAVE" + FMT),
    ("chunk runs past the riff", _wav(b"data" + (999_999).to_bytes(4, "little") + b"xx")),
    ("id3 chunk that is not a tag", _wav(FMT, _chunk(b"id3 ", b"garbage!"), DATA)),
])
def test_a_wav_that_is_not_understood_is_refused_and_left_alone(tmp_path, name, blob):
    p = tmp_path / "a.wav"
    p.write_bytes(blob)
    assert cc._embed_id3(p, MANIFEST) is False, name
    assert p.read_bytes() == blob, name
    assert [x.name for x in tmp_path.iterdir()] == ["a.wav"]


def test_other_audio_types_are_still_skipped(tmp_path):
    p = tmp_path / "a.flac"
    p.write_bytes(AUDIO)
    assert cc._embed_id3(p, MANIFEST) is False and p.read_bytes() == AUDIO


# ── the package is really gone ───────────────────────────────────────────────

def test_the_service_never_imports_mutagen():
    src = (cc.__file__ and open(cc.__file__, encoding="utf-8").read())
    assert not re.search(r"^\s*(import|from)\s+mutagen", src, re.M)


def test_no_install_path_asks_for_mutagen():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for rel in ("pyproject.toml", "packaging/windows/requirements/recommended.txt",
                "packaging/supply-chain/reviewed.json"):
        assert "mutagen" not in (root / rel).read_text(encoding="utf-8").lower(), rel
