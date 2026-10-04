"""The artifact block that rides in every turn shows no key material.

When the user edits an artifact by hand, the model is shown that edit as a diff
on its next turn (`context_block`). The diff is made from each version with a
key block or token already withheld from its whole text, so a changed line in
the middle of a key, which a diff shows with no armor around it and a "+" or "-"
in front, can never reach the model. `diff_between` without `redact` is the whole
diff and stays whole. All key material here is synthetic.
"""
from __future__ import annotations

import pytest

from agent_friday.services import artifacts as art
from agent_friday.services import secret_patterns

CID = "conv-artifact-credentials-test"
PEM_HEAD = "-----BEGIN OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
SENTINEL = "SYNTHETICSENTINELKEYMATERIAL0123456789abcdef"
CHANGED = "CHANGEDLINESENTINELZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXphYmNkZWZnaGlqa2xtbm9w"
TOKEN = "gh" + "p_" + "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"  # pragma: allowlist secret
BODY = (["b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
         "QyNTUxOQAAACBS" + SENTINEL]
        + ["ZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5%02d" % i for i in range(2, 14)])


def _pem(lines):
    return PEM_HEAD + "\n" + "\n".join(lines) + "\n" + PEM_TAIL + "\n"


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    art._OFF_MEMORY.clear()
    yield
    art._OFF_MEMORY.clear()


def test_a_key_block_added_by_hand_is_withheld_from_the_diff_the_model_is_shown():
    a = art.put(CID, kind="markdown", title="Notes", content="alpha\nbeta\n")
    art.edit(CID, a["id"], content="alpha\nbeta\n" + _pem(BODY))
    block = art.context_block(CID)
    assert "edited by the user" in block, "the model is still told the user edited it"
    assert SENTINEL not in block, "the diff showed a key's body"
    assert PEM_HEAD not in block and PEM_TAIL not in block, "the diff showed a key's armor lines"
    assert secret_patterns.WITHHELD in block


def test_a_changed_line_in_the_middle_of_a_key_never_reaches_the_model():
    """A diff of one body line has no armor within its three lines of context, and
    each line carries a "+" or "-": redacting the diff text would miss it."""
    a = art.put(CID, kind="markdown", title="Keys", content=_pem(BODY))
    changed = list(BODY)
    changed[7] = CHANGED
    art.edit(CID, a["id"], content=_pem(changed))
    block = art.context_block(CID)
    assert CHANGED not in block, "the diff showed the line the user changed in a key"
    assert BODY[7] not in block, "the diff showed the line the user replaced in a key"


def test_a_token_added_by_hand_is_withheld_from_the_diff():
    a = art.put(CID, kind="markdown", title="Config", content="retries: 3\n")
    art.edit(CID, a["id"], content="retries: 3\ngithub_token: %s\n" % TOKEN)  # pragma: allowlist secret
    block = art.context_block(CID)
    assert TOKEN not in block and TOKEN[:10] not in block
    assert "+github_token: " + secret_patterns.WITHHELD in block, "the rest of the changed line is still shown"


def test_an_ordinary_hand_edit_is_still_shown_as_a_diff():
    a = art.put(CID, kind="markdown", title="Draft", content="alpha\nbeta\n")
    art.edit(CID, a["id"], content="alpha\nBETA\n")
    block = art.context_block(CID)
    assert "-beta" in block and "+BETA" in block


def test_the_whole_diff_stays_whole_and_only_the_prompt_copy_is_redacted():
    a = art.put(CID, kind="markdown", title="Config", content="retries: 3\n")
    art.edit(CID, a["id"], content="retries: 3\ngithub_token: %s\n" % TOKEN)  # pragma: allowlist secret
    assert TOKEN in art.diff_between(CID, a["id"], 1, 2), "without `redact` the diff is whole"
    prompt_copy = art.diff_between(CID, a["id"], 1, 2, redact=True)
    assert TOKEN not in prompt_copy and secret_patterns.WITHHELD in prompt_copy
