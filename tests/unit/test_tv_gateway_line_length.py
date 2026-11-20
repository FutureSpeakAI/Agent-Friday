"""No line over 120 characters in the phone/tablet gateway modules this lane wrote (CODE_QUALITY Q13, Q1).

A reviewer returned the same fault twice (long docstring lines), so it is a check, not a note: this test names the
modules, and a line over the limit fails with its file and line number. A new module of this lane is added to MODULES
when it lands. Warn-only for one batch is not needed here: the modules are new and already clean.
"""
import pathlib

import pytest

import agent_friday.tv_gateway as gateway_package

LIMIT = 120
MODULES = ("pc_common", "pc_act", "pc_act_media", "pc_approve_decide", "approval_verify", "phone_update")
MODULES += ("ws_news", "ws_news_later")                       # the News adapters (PW-G2)
ROOT = pathlib.Path(gateway_package.__file__).resolve().parent


def too_long(text: str, limit: int = LIMIT) -> list:
    return [number for number, line in enumerate(text.split("\n"), 1) if len(line) > limit]


@pytest.mark.parametrize("module", [m for m in MODULES if (ROOT / (m + ".py")).exists()])
def test_no_line_over_the_limit(module):
    path = ROOT / (module + ".py")
    assert too_long(path.read_text(encoding="utf-8")) == [], "%s has lines over %d characters" % (path.name, LIMIT)


def test_the_check_itself_flags_a_long_line():
    assert too_long("short\n" + "x" * 121 + "\nshort") == [2]
    assert too_long("x" * 120) == []
