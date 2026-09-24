"""Nothing in an assembled system prompt may override the action policy.

Friday, serving on bonsai2, quoted two of its own directives back at Stephen:

  (A) "You have FULL authority to take multi-step actions without pausing for
      permission... Never ask 'should I continue?' mid-task."
  (B) "Before you take any real-world action... you MUST ask permission first
      and wait."

Stephen's decision, 2026-09-24: **B wins.** "It must ask."

What the investigation found, and what this file pins:

* (A) lives in exactly one place, `FRIDAY_SYSTEM_PROMPT`'s "== AUTONOMOUS
  OPERATION ==" block in `services/model_router.py`. Provenance is two ordinary
  human commits by Stephen -- `69e9f271` (2026-06-21, initial public release)
  and `b15f1fdf` (2026-06-27). It did NOT come from ingested content: every hit
  in `~/.friday` was a transcript of Friday quoting it.

* (B) was bolted onto TWO surfaces. Thirty call sites build the system prompt
  and only the two chat endpoints appended `ACTION_PERMISSION_POLICY`. Every
  other path -- background tasks, news, briefings, voice, research -- carried
  the full-authority text with no action policy at all. That asymmetry is the
  actual defect: the override was global and the rule was local.

So the policy belongs to the assembler, not to its callers, and no prompt may
contain text that claims authority over it. Working through internal, reversible
steps without "should I continue?" nagging is still allowed and still said.
"""

import re

import pytest

from agent_friday.services import model_router as mr
from agent_friday.services.agent import ACTION_PERMISSION_POLICY


#: The marker that proves the policy is present in an assembled prompt.
#:
#: The full HEADER, not the bare phrase: the rewritten AUTONOMOUS OPERATION
#: block cross-references "the ACTION PERMISSION POLICY below", which is good
#: writing and made a bare-phrase count report two copies where there is one.
POLICY_MARKER = "=== ACTION PERMISSION POLICY (REQUIRED) ==="

#: Phrasings that grant blanket authority over real-world actions. Each is
#: matched case-insensitively against the WHOLE assembled prompt. These are the
#: shapes an override actually takes -- not a general ban on the word
#: "autonomous", which has legitimate uses (background tasks, federation).
OVERRIDE_PATTERNS = [
    r"full authority to take",
    r"without pausing for permission",
    # "never ask 'should I continue?'" is deliberately NOT here -- see the
    # note in services/action_policy.py. It is anti-nagging guidance about
    # internal steps, not a claim of authority over real-world actions, and
    # the test below asserts it is still present.
    r"without asking for permission",
    r"no need to ask (?:for )?permission",
    r"do not ask for permission",
    r"don't ask for permission",
    r"without waiting for approval",
    r"you may act without",
    r"permission is not required",
]

#: Every provider family a prompt is ever gated for. `provider` is the axis that
#: changes what this function emits, so it is the axis worth sweeping.
PROVIDERS = ["local", "cloud", "anthropic", "openai", "gemini", "openrouter",
             "ollama-local", "llama-cpp-local"]

#: The workspaces the 30 call sites actually pass.
WORKSPACES = ["", "chat", "task", "draft", "news", "briefing", "voice",
              "research"]


def _assemble(provider, workspace=""):
    """The real assembler, gated explicitly, with no vault control.

    `vault_control=None` is the documented "decide explicitly" value and is
    what a test wants: nothing here is sent anywhere, and the vault's contents
    are not what is under test.
    """
    return mr._get_friday_system_prompt(
        keywords="do the thing", workspace=workspace,
        provider=provider, vault_control=None)


def _offending(text):
    """Every override pattern that matches, so a failure names all of them."""
    low = text.lower()
    return [p for p in OVERRIDE_PATTERNS if re.search(p, low)]


# ─────────────────────────────────────────────────────────────────────────────
# The constant itself.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_base_prompt_constant_claims_no_authority_over_actions():
    found = _offending(mr.FRIDAY_SYSTEM_PROMPT)
    assert not found, (
        "FRIDAY_SYSTEM_PROMPT grants blanket authority over real-world "
        "actions, contradicting the action policy: %s" % found)


def test_the_base_prompt_still_permits_uninterrupted_internal_work():
    """The legitimate half of (A) must survive. Stephen does not want
    "should I continue?" nagging between internal, reversible steps -- he wants
    a stop before real-world actions. Removing the contradiction must not turn
    Friday into something that asks permission to think."""
    low = mr.FRIDAY_SYSTEM_PROMPT.lower()
    assert "should i continue" in low, (
        "the prompt no longer tells Friday to avoid mid-task nagging")


# ─────────────────────────────────────────────────────────────────────────────
# Every assembled prompt, every provider, every workspace.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider", PROVIDERS)
def test_no_assembled_prompt_overrides_the_policy(provider):
    text = _assemble(provider)
    found = _offending(text)
    assert not found, (
        "the prompt assembled for provider %r contains an override of the "
        "action policy: %s" % (provider, found))


@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_assembled_prompt_carries_the_action_policy(provider):
    """The policy belongs to the assembler.

    Before this change only `/api/chat` and `/api/chat/send` appended it, so a
    background task or a voice turn ran with the full-authority text and no rule
    at all. Any of the 30 call sites is a path to a real-world action.
    """
    text = _assemble(provider)
    assert POLICY_MARKER in text, (
        "the prompt assembled for provider %r has no action permission policy"
        % provider)


@pytest.mark.parametrize("workspace", WORKSPACES)
def test_the_policy_survives_every_workspace(workspace):
    text = _assemble("local", workspace=workspace)
    assert POLICY_MARKER in text, (
        "workspace %r produced a prompt with no action policy" % workspace)
    assert not _offending(text)


def test_the_policy_is_stated_once_not_duplicated():
    """Moving it into the assembler must not leave the callers' copies behind:
    the same rule twice reads as emphasis in one place and as a diff in
    another, and invites the two copies to drift apart."""
    text = _assemble("local", workspace="chat")
    assert text.count(POLICY_MARKER) == 1, (
        "the action policy appears %d times in one prompt"
        % text.count(POLICY_MARKER))


def test_the_policy_comes_last_so_nothing_recalled_can_outrank_it():
    """Position is the cheap half of the defence against injected text.

    Recalled memory, vault content and self-knowledge are all spliced into this
    prompt. Putting the rule after them means an instruction that arrived
    through ingested content is contradicted by the last word rather than the
    first.
    """
    text = _assemble("local", workspace="chat")
    tail = text[-(len(ACTION_PERMISSION_POLICY) + 400):]
    assert POLICY_MARKER in tail, (
        "the action policy is not near the end of the assembled prompt, so "
        "recalled context appears after it")
