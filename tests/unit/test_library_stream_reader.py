"""The Library's search stream is read with fetch so it can carry the page's session token
(EventSource cannot set a header), and every Library request is refused without that token."""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
node = shutil.which("node")
WS = (ROOT / "static" / "library_ws.js").read_text(encoding="utf-8")


def _fn() -> str:
    a = WS.index("// begin readEventStream")
    b = WS.index("// end readEventStream")
    return WS[a:b]


def _run(chunks: list[list[int]]) -> list[str]:
    src = (
        _fn()
        + "\nconst chunks = " + json.dumps(chunks) + ";\n"
        + r"""
let i = 0;
const res = { body: { getReader: () => ({ read: () => Promise.resolve(
  i < chunks.length ? { done: false, value: Uint8Array.from(chunks[i++]) } : { done: true, value: undefined }) }) } };
const got = [];
readEventStream(res, m => got.push(m.data)).then(() => console.log(JSON.stringify(got)));
"""
    )
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_stream_reader_splits_frames_across_chunks_and_multibyte_characters():
    stream = ('data: {"event":"decision","n":1}\n\n' 'data: {"event":"answer","text":"caf\u00e9 \u2014 ok"}\n\n'
              'data: {"event":"done"}\n\n').encode("utf-8")
    whole = _run([list(stream)])
    assert [json.loads(x)["event"] for x in whole] == ["decision", "answer", "done"]
    # one byte at a time: frames and the two-byte and three-byte characters all straddle chunk edges
    drip = _run([[b] for b in stream])
    assert drip == whole and json.loads(drip[1])["text"] == "caf\u00e9 \u2014 ok"
    assert _run([list(b'data: {"event":"decision"}\n')]) == []     # an unfinished frame is not delivered


def test_the_ask_path_no_longer_uses_event_source_and_carries_the_token():
    assert "new EventSource" not in WS
    assert "api(url, { signal: ctl.signal })" in WS and "X-Friday-Token" in WS
