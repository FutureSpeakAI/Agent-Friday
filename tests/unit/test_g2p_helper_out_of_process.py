"""espeak-ng stays out of Friday's process (orchestrator decision 2026-10-03,
delegated by the owner): names Kokoro's dictionary does not know are looked
up by a separate helper program over a pipe; Friday never imports or loads
phonemizer or espeak-ng (GPL-3.0); without the helper, a name is spelled
out, logged, never skipped and never a crash.
"""
import logging
import pathlib
import re
import sys
import textwrap

import pytest

from agent_friday.services import g2p_fallback as g2p

SRC = pathlib.Path(g2p.__file__).resolve().parents[1]


class _Tok:
    def __init__(self, text):
        self.text = text


def test_spelling_covers_letters_and_digits_and_skips_nothing_spellable():
    ps = g2p.spell_out("Nguyen7")
    assert ps.split(" ") == [g2p.LETTER_PHONEMES[c] for c in "nguyen7"]
    assert g2p.spell_out("--") == ""


def test_without_the_helper_a_name_is_spelled_out_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(g2p, "helper_installed", lambda: False)
    g2p._SPELLED_LOGGED.discard("kubernetes")
    with caplog.at_level(logging.INFO, logger="friday.g2p_fallback"):
        ps, rating = g2p.PipeFallback()(_Tok("Kubernetes"))
        g2p.PipeFallback()(_Tok("Kubernetes"))
    assert ps == g2p.spell_out("Kubernetes") and rating == 1
    lines = [r for r in caplog.records if "spelling out 'Kubernetes'" in r.getMessage()]
    assert len(lines) == 1 and "not installed" in lines[0].getMessage()


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    """A stand-in helper program speaking the real protocol, run by path."""
    def make(body):
        script = tmp_path / "helper.py"
        script.write_text(textwrap.dedent(body), encoding="utf-8")
        monkeypatch.setattr(g2p, "HELPER_PATH", script)
        monkeypatch.setattr(g2p, "helper_installed", lambda: True)
        monkeypatch.setattr(g2p, "_HELPER", g2p._Helper())
        return script
    yield make
    g2p._HELPER.close()


def test_a_name_is_pronounced_by_the_helper_over_the_pipe(fake_helper):
    fake_helper('''
        import json, sys
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            req = json.loads(line)
            print(json.dumps({"ps": "nwˈɪn<" + req["text"] + ">", "rating": 2}), flush=True)
    ''')
    assert g2p.PipeFallback()(_Tok("Nguyen")) == ("nwˈɪn<Nguyen>", 2)


def test_a_helper_that_cannot_start_means_spelling_not_silence(fake_helper):
    fake_helper('''
        import json
        print(json.dumps({"ready": False, "error": "no espeak library"}), flush=True)
    ''')
    ps, rating = g2p.PipeFallback()(_Tok("Ceph"))
    assert ps == g2p.spell_out("Ceph") and rating == 1
    assert "no espeak library" in g2p._HELPER.unavailable


def test_the_stub_replaces_misaki_espeak_without_loading_gpl_code(monkeypatch):
    monkeypatch.delitem(sys.modules, "misaki.espeak", raising=False)
    before = {m for m in sys.modules if m.startswith(("phonemizer", "espeakng_loader"))}
    out = g2p.install_stub()
    assert out["stubbed"] is True
    stub = sys.modules["misaki.espeak"]
    assert stub.EspeakFallback is g2p.PipeFallback
    with pytest.raises(RuntimeError):
        stub.EspeakG2P(language="fr")
    after = {m for m in sys.modules if m.startswith(("phonemizer", "espeakng_loader"))}
    assert after == before


_GPL_IMPORT = re.compile(r"^\s*(?:import|from)\s+(phonemizer|espeakng_loader|misaki\.espeak)\b",
                         re.M)


def test_no_file_in_friday_imports_gpl_code_except_the_helper_program():
    helper = g2p.HELPER_PATH.resolve()
    offenders = []
    for f in SRC.rglob("*.py"):
        if f.resolve() == helper:
            continue
        if _GPL_IMPORT.search(f.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(f.relative_to(SRC)))
    assert offenders == [], "GPL code imported into Friday's process: %s" % offenders


def test_the_helper_program_imports_nothing_of_friday():
    src = g2p.HELPER_PATH.read_text(encoding="utf-8")
    assert "agent_friday" not in re.sub(r'""".*?"""', "", src, flags=re.S)
    assert _GPL_IMPORT.search(src), "the helper is where espeak is loaded"


@pytest.mark.skipif(not g2p.helper_installed(),
                    reason="the optional espeak-ng helper is not installed here")
def test_the_real_helper_pronounces_a_name(monkeypatch):
    monkeypatch.setattr(g2p, "_HELPER", g2p._Helper())
    try:
        ps, rating = g2p.PipeFallback()(_Tok("Nguyen"))
    finally:
        g2p._HELPER.close()
    assert rating == 2 and ps and ps != g2p.spell_out("Nguyen")
