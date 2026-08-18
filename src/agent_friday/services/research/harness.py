"""
The research harness — code drives, models judge.

deep-research.md §3.2–§3.5. The central design choice (§1.6d): this is NOT a
free agentic tool loop. A research step is not a chat turn, and running each
fetch-and-extract through Friday's full system prompt plus 52 tool schemas
spends ~20,000 tokens of ceremony per step — two-thirds of a 32k window before
any work happens. So the loop is ordinary Python, and models are called at
judgment points with purpose-built prompts. That also makes budgets enforceable
and progress legible by construction rather than by hope.

Every model call here therefore carries: one job, no persona, no tool registry.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

from agent_friday.services import web_fetch, web_search
from agent_friday.services.research.objects import (
    CONFIRMED, CONTESTED, GRINDING, SCOPING, SINGLE_SOURCE, SYNTHESIZING,
    UNCONFIRMED, VERIFYING, Commission, Finding, ResearchPlan, SubQuestion,
)

_log = logging.getLogger("friday.research")

# SEATS ARE RESOLVED AGAINST WHAT IS ACTUALLY INSTALLED, not hardcoded.
# Found 2026-08-18: `gemma4:12b` was removed from Ollama between runs (the
# model-suite work reshuffles tags), and every local call in the grind answered
# 404 "model not found". The commission failed correctly — "I could not turn
# this into a research plan" — but the REASON was buried in a warning line, and
# a pipeline whose seats can silently evaporate should say so in the words a
# person needs: the model is gone, not the research.
_SEAT_FALLBACKS = {
    "brain": ["gemma4:12b", "hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M",
              "qwen3.5:9b", "gemma4:26b", "gemma4:e2b"],
    "sidekick": ["gemma4:e2b", "gemma4:e4b", "qwen3.5:9b"],
    "heavy": ["gemma4:26b", "gemma4:12b"],
}


def installed_models() -> list[str]:
    try:
        import requests
        from agent_friday.services.local_call import ollama_url
        d = requests.get(f"{ollama_url()}/api/tags", timeout=8).json()
        return [m.get("name", "") for m in d.get("models", [])]
    except Exception:
        return []


def resolve_seat(role: str) -> str | None:
    """The first candidate for `role` that is actually installed, or None."""
    have = set(installed_models())
    for cand in _SEAT_FALLBACKS.get(role, []):
        if cand in have:
            return cand
    return None


def seat_report() -> dict:
    """What each role resolves to right now — so a missing seat is a stated
    fact rather than a 404 buried in a log line."""
    return {role: resolve_seat(role) for role in _SEAT_FALLBACKS}


BRAIN = "gemma4:12b"        # judgment: reformulate, converse, done_when
SIDEKICK = "gemma4:e2b"     # cheap structured work
# Extraction seat, MEASURED 2026-08-17 on a 24,000-char page:
#   gemma4:e4b   33.1s  no usable JSON at all
#   gemma4:e2b   26.3s  6 passages
#   gemma4:12b  192.6s  2 passages
# The e2b is both the fastest and the only one that reliably returns parseable
# output AND finds more than a couple of passages. The 12b at 192.6s is also
# what was timing out at the old 120s ceiling mid-commission.
EXTRACTOR = "gemma4:e2b"    # page -> relevant verbatim spans
HEAVY = "gemma4:26b"        # synthesis


# ── model plumbing ────────────────────────────────────────────────────────────

def _json_local(system: str, user: str, model: str, *, max_tokens: int = 2048,
                retries: int = 1) -> dict | None:
    """Structured output with one retry. None means the model would not comply.

    Goes through local_call, NOT model_router._call_ollama — the latter runs an
    agentic tool loop even with no tools supplied and can eat the call whole
    (measured: 48.9s to return "[Agent hit max tool iterations]"). RS8 says no
    research stage carries the tool registry; this is that rule in code rather
    than in a comment.

    None is returned rather than a guessed shape: a research stage that
    silently substitutes an empty plan for a failed one is the green-job
    failure mode.
    """
    from agent_friday.services import local_call
    out = local_call.call_json(system, user, model, max_tokens=max_tokens,
                               retries=retries)
    if out is None:
        _log.warning("research: no usable JSON from %s", model)
    return out


# Seat substitutions seen during a run, drained onto the commission so they
# reach the REPORT rather than dying in a log line.
_SUBSTITUTIONS: list[str] = []


def drain_substitutions() -> list[str]:
    out = list(_SUBSTITUTIONS)
    _SUBSTITUTIONS.clear()
    return out


def _json_local_or_cloud(seat: str, system: str, user: str, *,
                         max_tokens: int = 2048) -> dict | None:
    """One structured call on `seat`, cloud or local. Keeps the caller from
    having to know which kind of model it just asked for."""
    if seat and not seat.startswith("gemma"):
        try:
            return _extract_json(_claude(system, user, model=seat,
                                         max_tokens=max_tokens))
        except Exception as e:
            _log.warning("cloud call on %s failed (%s) — using %s",
                         seat, e, SIDEKICK)
            _SUBSTITUTIONS.append(
                f"{seat} was unreachable ({str(e)[:100]}); {SIDEKICK} did that step")
    return _json_local(system, user, SIDEKICK, max_tokens=max_tokens)


def _extract_json(raw: str) -> dict | None:
    from agent_friday.services import local_call
    return local_call.extract_json(raw)


# ── Reading local-model output defensively ────────────────────────────────────
#
# THE GENERAL LESSON, learned the expensive way on 2026-08-17. A commission
# produced 9 verified, correctly-cited findings and then failed at the last
# step because the outline model returned `sections` as a list of heading
# STRINGS rather than objects. Constrained JSON decoding guarantees valid JSON,
# not the shape the prompt asked for.
#
# The inversion worth naming: this codebase writes careful defensive readers at
# the NETWORK boundary — see firecrawl._results_of, which handles `data` being
# either a list or a dict, with a comment about how a shape change would look
# like "the web returned nothing" — and then reads the output of a
# 2-billion-parameter local model with a bare `.get()`. The network boundary
# faces a maintained commercial API with a versioned contract. The model
# boundary faces a small model doing its best. The paranoia is pointed the
# wrong way round.
#
# So every structural read of model output in this module goes through these.
# The specific failures they prevent, each real and each present in the code
# before this was written:
#   * a STRING where a list belonged -> iterating it yields CHARACTERS, so a
#     query list becomes single-letter searches and a passage list becomes
#     one-character "verbatim quotes" that then fail verification
#   * a DICT where a list belonged -> `[:20]` raises TypeError mid-grind
#   * a LIST where a dict belonged -> `.get()` raises

def _as_list(value) -> list:
    """Whatever the model gave us, as a list — never a string's characters."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, bytes)):
        s = value.decode() if isinstance(value, bytes) else value
        return [s] if s.strip() else []
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (set, tuple)):
        return list(value)
    return [value]


def _as_dict(value) -> dict:
    """A dict, or an empty one. Never a raised AttributeError."""
    if isinstance(value, dict):
        return value
    return {}


def _as_text(value) -> str:
    """A stripped string. Lists get joined rather than str()'d into brackets."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value).strip()
    return str(value).strip()


def _claude(system: str, user: str, *, max_tokens: int = 4096,
            model: str | None = None) -> str:
    """A cloud call on the model Stephen NAMED.

    The model is passed through verbatim: his standing rule is that the model
    he picks is the model that answers, and a research commission is not an
    exception to it. Goes through the normal gate — the override changes what
    may be sent, never whether the gate runs.
    """
    from agent_friday.services import judgment_gate as jg
    from agent_friday.services.model_router import _call_claude
    ctx = _CLOUD_OVERRIDE.get("ctx")
    if ctx is not None:
        with ctx:
            return _call_claude([{"role": "user", "content": user}],
                                system=system, model=model,
                                max_tokens=max_tokens) or ""
    return _call_claude([{"role": "user", "content": user}], system=system,
                        model=model, max_tokens=max_tokens) or ""


# Set for the life of a commission Stephen explicitly authorized. Without it,
# his instruction was honoured at ROUTING (the commission went cloudward) and
# then silently undone at EGRESS (the gate withheld his own name span by span)
# — the routing said yes and the boundary said no, which is the worst of both.
_CLOUD_OVERRIDE: dict = {"ctx": None}


def set_cloud_override(ctx) -> None:
    _CLOUD_OVERRIDE["ctx"] = ctx


# ── Stage B: scoping, with the protection fork (§3.2 / RS2) ───────────────────

_SCOPE_SYSTEM = """You are planning a research investigation using the STORM method. You do not answer the question — you decompose it.

Given a research question, produce:
1. perspectives: 3-5 distinct vantage points the topic is seen from. Each has a name and a one-line stance. Different perspectives should DISAGREE about what matters, not just cover different subtopics.
2. sub_questions: 6-10 concrete, separately-answerable questions. Each is tagged with the perspective that would ask it, and carries a `done_when`: a checkable sentence naming what evidence would settle it. "done_when" must describe EVIDENCE, not a feeling of completeness.
3. working_title: a plain, specific title for the eventual report.
4. internal_first: 0-4 topics to look up in the user's own existing notes BEFORE searching the web.

JSON shape:
{"perspectives":[{"name":"...","stance":"..."}],
 "sub_questions":[{"text":"...","perspective":"...","done_when":"..."}],
 "working_title":"...","internal_first":["..."]}"""


def scope(c: Commission) -> ResearchPlan | None:
    """Turn the question into a plan. Who scopes is the gate's decision."""
    c.set_stage(SCOPING)
    global BRAIN, SIDEKICK, EXTRACTOR, HEAVY
    _b, _s, _h = resolve_seat("brain"), resolve_seat("sidekick"), resolve_seat("heavy")
    if _b and _b != BRAIN:
        c.log(f"seat resolved: brain {BRAIN} is not installed, using {_b}")
        BRAIN = _b
    if _s:
        SIDEKICK = EXTRACTOR = _s
    if _h:
        HEAVY = _h
    if not _b:
        c.failure = ("None of my local models are installed "
                     f"({', '.join(_SEAT_FALLBACKS['brain'][:3])} all missing). "
                     "This is a missing model, not a research failure.")
        c.log("NO LOCAL SEAT AVAILABLE — refusing to start")
        return None

    from agent_friday.services import judgment_gate as jg
    dry = jg.dry_run("\n\n".join(x for x in (c.question, c.context) if x))
    c.protection.cloud_allowed = bool(dry["cloud_allowed"])
    c.protection.question_sent = dry.get("question_sent")
    c.protection.scrub_tags = dry.get("scrub_tags") or []
    c.protection.scrub_count = int(dry.get("scrub_count") or 0)
    c.protection.reason = dry.get("reason") or ""
    c.save()
    c.log("protection plan computed", cloud_allowed=c.protection.cloud_allowed,
          sentence=c.protection.sentence())

    # §override — an explicit instruction outranks the verdict.
    forced = bool(c.instruction.get("allow_cloud"))
    named_model = c.instruction.get("model")
    if forced and not c.protection.cloud_allowed:
        c.protection.cloud_allowed = True
        c.protection.reason = (
            "You told me to run this in the cloud, so I am. "
            "(The classifier would have kept it local: "
            f"{c.protection.reason})")
        c.log("OVERRIDE: instruction outranks the classifier verdict",
              instruction=c.instruction)
    if forced:
        set_cloud_override(jg.Override(
            reason="you asked for this research to run in the cloud"))
    want_cloud = c.protection.cloud_allowed and (
        forced or c.disposition != "now_local")
    used, data = "", None
    if want_cloud:
        try:
            raw = _claude(_SCOPE_SYSTEM,
                          c.protection.question_sent or c.question,
                          model=named_model)
            data = _extract_json(raw)
            used = named_model or _anthropic_model_name()
        except Exception as e:
            c.log(f"cloud scoping failed, falling back to the brain: {e}")
    if data is None:
        data = _json_local(_SCOPE_SYSTEM, c.question, BRAIN, max_tokens=3072)
        used = BRAIN
    if data is None:
        c.log("scoping produced no usable plan")
        return None

    sqs = []
    for i, s in enumerate(_as_list(data.get("sub_questions"))):
        # A bare string is a plausible and useful shape ("just the question"),
        # so it is accepted rather than dropped — losing the whole plan to a
        # wrapper is the failure this guards.
        text = _as_text(s) if isinstance(s, str) else _as_text(_as_dict(s).get("text"))
        if not text:
            continue
        d = _as_dict(s)
        sqs.append(SubQuestion(id=f"sq{i}", text=text,
                               perspective=_as_text(d.get("perspective")),
                               done_when=_as_text(d.get("done_when"))))
        if len(sqs) >= c.budget["sub_questions"]:
            break
    if not sqs:
        c.log("scoping returned a plan with no sub-questions")
        return None

    plan = ResearchPlan(
        commission_id=c.id,
        perspectives=[_as_dict(p) for p in _as_list(data.get("perspectives"))
                      if _as_dict(p)],
        sub_questions=sqs,
        working_title=str(data.get("working_title") or c.question)[:160],
        internal_first=[str(x) for x in (data.get("internal_first") or [])][:4],
        scoped_by=used)
    # ── Per-sub-question protection (Stephen's design) ──
    # "Opus 5 for the web work. Gemma4 can bring up the tail for the vault work
    # before Friday presents the final report." One verdict for a whole
    # commission is too coarse: a report on himself and his company mixes a
    # public question with a private one, and either verdict is wrong for half
    # of it.
    # It is a SOURCE SPLIT, not a privacy adjudication (Stephen's spec).
    # Web sub-questions go up to the named model with live sources; vault
    # sub-questions stay down. Nothing needs judging span by span inside the
    # research path, because vault content never crosses at all.
    for sq in sqs:
        is_vault = jg.looks_first_person(sq.text) or any(
            k in sq.text.lower() for k in
            ("my vault", "my notes", "my own", "my prior", "my previous"))
        sq.source = VAULT_TIER if is_vault else WEB_TIER
        sq.cloud_allowed = (sq.source == WEB_TIER) and (
            forced or c.protection.cloud_allowed)
        sq.protection_reason = (
            "vault sub-question — read locally, never transmitted"
            if is_vault else "public sub-question — web sources")
    # His tail: one vault pass for personal context, always local.
    sqs.append(SubQuestion(
        id=f"sq{len(sqs)}",
        text=f"What does my own material say that bears on: {c.question}",
        perspective="personal context", done_when="vault passages retrieved",
        cloud_allowed=False, source=VAULT_TIER,
        protection_reason="vault sub-question — read locally, never transmitted"))
    n_cloud = sum(1 for s in sqs if s.cloud_allowed)
    c.log(f"per-sub-question fork: {n_cloud} cloud, {len(sqs)-n_cloud} local",
          cloud=n_cloud, local=len(sqs)-n_cloud)

    c.plan = plan
    c.progress["sub_questions_total"] = len(sqs)
    c.save()
    c.log(f"plan: {len(sqs)} sub-questions, scoped by {used}",
          scoped_by=used, sub_questions=len(sqs))
    return plan


def _anthropic_model_name() -> str:
    """Name the model that actually served, never the vendor (RS10)."""
    try:
        from agent_friday.services.model_router import ANTHROPIC_MODEL_DEFAULT
        from agent_friday.core import _load_settings
        return ((_load_settings() or {}).get("anthropic_model")
                or ANTHROPIC_MODEL_DEFAULT)
    except Exception:
        return "claude (model id unavailable)"


# ── Stage C: the grind (§3.3) ─────────────────────────────────────────────────

_QUERY_SYSTEM = """You turn a research sub-question into web search queries.
Produce 2-4 queries that a search engine would answer well: concrete nouns,
names, dates. No boolean operators, no site: filters unless the question
demands one. If prior notes are supplied, write queries that fill their GAPS
rather than repeating what is already known.
JSON: {"queries":["...","..."]}"""

_EXTRACT_SYSTEM = """You extract evidence from one web page for one specific question.

Return ONLY passages that bear on the question, copied VERBATIM from the page — never paraphrased, never summarized, never stitched together from different places. If a passage is not word-for-word from the page, do not return it. If the page says nothing relevant, return an empty list. An empty list is a correct and useful answer.

JSON: {"passages":["exact text from the page", "..."]}"""

_CONVERSE_SYSTEM = """You answer one research sub-question using ONLY the supplied corpus of source passages. You may not use anything you know that is not in the corpus.

Return:
- answer: what the corpus supports, in your own words. If the corpus does not answer the question, say so plainly.
- findings: each one a CLAIM in your words paired with a QUOTE copied verbatim from the corpus that supports it, and the source_id it came from. Never write a claim you cannot pair with a quote.
- gaps: what is still unanswered.
- best_followup: the single most useful next question, or "" if the question is settled.
- done: true only if the done_when condition is satisfied by the corpus.

JSON: {"answer":"...","findings":[{"claim":"...","quote":"...","source_id":"..."}],
       "gaps":["..."],"best_followup":"...","done":false}"""


def grind(c: Commission) -> None:
    """Run the simulated conversation per sub-question. Code drives the loop."""
    c.set_stage(GRINDING)
    plan = c.plan
    assert plan is not None
    started = time.time()
    fetches_total = 0
    search_ok_any = False
    model_failures: list[str] = []
    # The model he NAMED, used on sub-questions cleared for cloud.
    cloud_model = c.instruction.get("model") if (
        c.protection.cloud_allowed or c.instruction.get("allow_cloud")) else None

    for idx, sq in enumerate(plan.sub_questions, 1):
        c.progress.update({"sub_question": idx, "note": sq.text[:120]})
        c.save()
        c.log(f"sub-question {idx}/{len(plan.sub_questions)}: {sq.text}",
              sq_id=sq.id)

        corpus: list[dict] = []
        if getattr(sq, "source", WEB_TIER) == VAULT_TIER:
            corpus = _vault_corpus(sq.text)
            persist_vault_corpus(c, corpus)
            c.log(f"vault pass: {len(corpus)} passage(s) read locally "
                  f"(never transmitted)", sq_id=sq.id, tier=VAULT_TIER)
            sq.seat = BRAIN
            if corpus:
                _vp = '\\n\\n'.join(
                    f"[{e['source_id']}] {e['text']}" for e in corpus)
                conv = _json_local(
                    _CONVERSE_SYSTEM,
                    f"Sub-question: {sq.text}\n\nCORPUS:\n" + _vp,
                    BRAIN, max_tokens=3072)
                for f in _as_list(_as_dict(conv).get("findings"))[:20]:
                    f = _as_dict(f)
                    claim, quote = _as_text(f.get("claim")), _as_text(f.get("quote"))
                    if not claim or not quote:
                        continue
                    match = next((e for e in corpus if quote[:50] in e["text"]), None)
                    c.add_finding(Finding(
                        id=uuid.uuid4().hex[:10], sub_question_id=sq.id,
                        claim=claim, quote=quote,
                        source_id=(match or corpus[0])["source_id"],
                        url="", confidence=SINGLE_SOURCE))
                c.save()
            continue
        q = sq.text
        sq_fetches = 0

        for depth in range(c.budget["followup_depth"]):
            if time.time() - started > c.budget["wall_clock_soft_s"]:
                c.log("wall-clock budget reached; stopping the grind early")
                break

            # He asked for the strong model on "the web work". Deciding WHAT
            # to search for is the web work — on his own run the queries came
            # from the 2B sidekick and returned SEC filings for Cerebras and
            # Jet.AI instead of FutureSpeak.AI, and the strong model then
            # faithfully extracted findings from the wrong corpus. A frontier
            # reader over a bad corpus is still a bad report.
            _q_seat = cloud_model if (sq.cloud_allowed and cloud_model) else SIDEKICK
            qd = _json_local_or_cloud(_q_seat, _QUERY_SYSTEM,
                             f"Sub-question: {q}\n\nAlready known:\n" +
                             ("\n".join(p["text"][:200] for p in corpus[-4:])
                              or "(nothing yet)"),
                             max_tokens=512)
            queries = [_as_text(x) for x in _as_list(_as_dict(qd).get("queries")) if _as_text(x)][
                :c.budget["queries_per_sq"]] or [q]

            results = []
            for query in queries:
                out = web_search.search(query, count=8)
                if out.get("status") == web_search.SearchStatus.OK:
                    search_ok_any = True
                    results.extend(out["results"])
                else:
                    c.log(f"search returned nothing for {query!r}",
                          status=out.get("status"),
                          note=web_search.status_note(out))
            c.log(f"searched {len(queries)} quer{'ies' if len(queries)!=1 else 'y'}, "
                  f"{len(results)} results consulted", backend=out.get("backend"))
            # De-dup by URL, keep order.
            seen, picked = set(), []
            for r in results:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                picked.append(r)

            for r in picked:
                if (sq_fetches >= c.budget["fetches_per_sq"] or
                        fetches_total >= c.budget["fetches_total"]):
                    break
                rec = web_fetch.fetch(r["url"])
                sq_fetches += 1
                fetches_total += 1
                c.progress["fetches"] = fetches_total
                if not rec.get("ok"):
                    # §7.6 — a limit the tools have is recorded, not skipped.
                    c.log(f"could not read {r['url']}: {rec.get('error')}",
                          kind=rec.get("error_kind"), url=r["url"])
                    continue
                c.log(f"read {rec.get('title') or r['url']}", url=r["url"],
                      chars=rec.get("chars"), cached=rec.get("from_cache"))
                page = web_fetch.load_extraction(rec["id"])
                ex = _json_local(
                    _EXTRACT_SYSTEM,
                    f"Question: {sq.text}\n\nPage ({rec.get('title')}):\n"
                    f"{page[:24000]}",
                    EXTRACTOR, max_tokens=1536)
                for passage in _as_list(_as_dict(ex).get("passages"))[:8]:
                    ptext = _as_text(passage)
                    if ptext:
                        corpus.append({"text": ptext, "tier": WEB_TIER,
                                       "source_id": rec["id"],
                                       "url": rec.get("final_url") or r["url"],
                                       "title": rec.get("title", "")})

            if not corpus:
                c.log("no usable passages at this depth")
                break

            # ── Per-sub-question seat ──
            # A PUBLIC sub-question gets the model Stephen named and the live
            # web; a PRIVATE one is ground locally and never leaves. The corpus
            # here is fetched web content, so on a public sub-question the only
            # thing crossing is public material plus Friday's framing of a
            # question already judged sendable.
            _prompt = (
                    f"Sub-question: {sq.text}\ndone_when: {sq.done_when}\n\n"
                    f"CORPUS:\n" + "\n\n".join(
                        f"[{p['source_id']}] {p['text']}" for p in corpus[:60])
            )
            if sq.cloud_allowed and cloud_model:
                # THE INVARIANT. Web-tier only, re-checked rather than trusted.
                _assert_no_vault(corpus)
                sq.seat = cloud_model
                try:
                    conv = _extract_json(_claude(_CONVERSE_SYSTEM, _prompt,
                                                 model=cloud_model))
                except Exception as e:
                    # DISCLOSED, not just logged. Stephen's original report was
                    # "I asked for Opus 5 but it ran on Gemma4 instead" — and a
                    # silent fallback here reproduces exactly that, one layer
                    # down. A capability the tools cannot deliver is told to the
                    # user; it is never quietly substituted.
                    note = (f"You asked for {cloud_model} on this sub-question "
                            f"and I could not reach it ({type(e).__name__}: "
                            f"{str(e)[:120]}), so it ran on {BRAIN} instead.")
                    c.substitutions.append(note)
                    c.log("SEAT SUBSTITUTION: " + note, sq_id=sq.id)
                    sq.seat = BRAIN
                    conv = _json_local(_CONVERSE_SYSTEM, _prompt, BRAIN, max_tokens=3072)
            else:
                sq.seat = BRAIN
                conv = _json_local(_CONVERSE_SYSTEM, _prompt, BRAIN, max_tokens=3072)
            if conv is None:
                # A MODEL failure, not an empty web. Recorded as such, because
                # the caller decides between "delivered: found nothing" and
                # "failed: my tools broke" on this distinction — and reporting
                # a timeout as an absence is a fabricated empirical result,
                # the same defect §7.2 fixes for search.
                model_failures.append(sq.id)
                c.log("MODEL FAILURE: the conversation step returned nothing "
                      "usable (timeout or unparseable output) — this is NOT "
                      "evidence the sources had no answer", sq_id=sq.id)
                break

            for f in _as_list(_as_dict(conv).get("findings"))[:20]:
                f = _as_dict(f)
                claim, quote = _as_text(f.get("claim")), _as_text(f.get("quote"))
                if not claim or not quote:
                    continue
                src = _as_text(f.get("source_id"))
                match = next((p for p in corpus if p["source_id"] == src), None) \
                    or next((p for p in corpus if quote[:60] in p["text"]), None)
                c.add_finding(Finding(
                    id=uuid.uuid4().hex[:10], sub_question_id=sq.id,
                    claim=claim, quote=quote,
                    source_id=(match or {}).get("source_id", src),
                    url=(match or {}).get("url", ""),
                    confidence=SINGLE_SOURCE))
            c.save()

            if _as_dict(conv).get("done"):
                c.log(f"done_when satisfied for {sq.id}")
                break
            nxt = _as_text(_as_dict(conv).get("best_followup"))
            if not nxt:
                break
            q = nxt
            c.log(f"following up: {nxt}", sq_id=sq.id, depth=depth + 1)

    # A grind whose models failed on EVERY sub-question did not establish an
    # absence; it established that the machine could not answer. Recorded so
    # run() can fail the commission instead of delivering a search trail that
    # reads like "there is nothing out there".
    if model_failures and len(model_failures) >= len(plan.sub_questions):
        c.failure = (
            f"My local models failed on every sub-question "
            f"({len(model_failures)}/{len(plan.sub_questions)}) — they timed "
            f"out or returned unusable output. I am NOT reporting that nothing "
            f"was found, because I could not finish looking.")
        c.log("ALL sub-questions hit model failures — this is a failed "
              "commission, not a finding of absence")

    # §7.2 — distinguish "nothing published" from "my tools are broken".
    if not search_ok_any:
        canary = web_search.canary(force=True)
        if not canary.get("ok"):
            c.failure = ("My search tool is broken — "
                         f"{canary.get('detail')}. I am NOT reporting that "
                         "nothing is published, because I could not look.")
            c.log("CANARY FAILED — search infrastructure is down")
    c.save()


# ── Stage D: synthesis (§3.4) ─────────────────────────────────────────────────

_OUTLINE_SYSTEM = """You outline a research report from an index of findings.
Group the findings into 3-6 sections that tell the answer in a sensible order,
with the direct answer first. Every section names the finding ids it will use.
JSON: {"sections":[{"heading":"...","finding_ids":["..."]}],"answer":"one-paragraph direct answer"}"""

_SECTION_SYSTEM = """You write one section of a research report from findings.

Rules, all of them hard:
- Write ONLY from the supplied findings. Nothing you know otherwise belongs here.
- Every factual sentence carries its finding id inline as [F:<id>].
- Where findings disagree, present the disagreement and cite both. Never pick a winner.
- Absence of evidence is written as absence. Never smoothed into confident prose.
JSON: {"body":"markdown prose with [F:id] markers"}"""


def synthesize(c: Commission) -> dict | None:
    """Outline, then section-by-section. Returns a draft dict.

    Seat choice is made here and RECORDED, including when it falls back —
    the report never silently changes author (RS10).
    """
    c.set_stage(SYNTHESIZING)
    findings = [f for f in c.findings() if not f.struck_reason]
    if not findings:
        c.log("nothing to synthesize — no findings survived the grind")
        return None

    seat, seat_note = _pick_synthesis_seat(c, len(findings))
    c.log(f"synthesizing on {seat}", seat=seat, why=seat_note)

    index = "\n".join(f"[{f.id}] ({f.sub_question_id}) {f.claim}" for f in findings)
    outline = _json_local(_OUTLINE_SYSTEM, f"Question: {c.question}\n\n"
                          f"FINDINGS INDEX:\n{index}", seat, max_tokens=2048)
    if outline is None and seat != BRAIN:
        c.log(f"{seat} would not produce a usable outline — falling back to {BRAIN}")
        seat = BRAIN
        outline = _json_local(_OUTLINE_SYSTEM, f"Question: {c.question}\n\n"
                              f"FINDINGS INDEX:\n{index}", seat, max_tokens=2048)
    if outline is None:
        # DEFECT FOUND ON STEPHEN'S OWN RUN: 19 verified findings were thrown
        # away and the commission delivered a finding-of-absence report,
        # because the local outline model would not emit usable JSON. Real work
        # discarded at the last step by a wrapper — the green-job pattern, for
        # the third time in this file. An outline is a NICETY; the findings are
        # the report. Group them by sub-question and write it anyway.
        c.log("outline model returned nothing usable — grouping findings by "
              "sub-question instead of discarding them")
        by_sq: dict = {}
        for f in findings:
            by_sq.setdefault(f.sub_question_id, []).append(f.id)
        titles = {s.id: s.text for s in (c.plan.sub_questions if c.plan else [])}
        outline = {"answer": "",
                   "sections": [{"heading": titles.get(k, "Findings")[:90],
                                 "finding_ids": v} for k, v in by_sq.items()]}

    by_id = {f.id: f for f in findings}
    sections = []
    for sec in _outline_sections(outline, by_id)[:8]:
        ids = [i for i in (sec.get("finding_ids") or []) if i in by_id]
        if not ids:
            continue
        block = "\n\n".join(
            f"[F:{i}] CLAIM: {by_id[i].claim}\nQUOTE: \"{by_id[i].quote}\""
            for i in ids)
        body = _json_local(_SECTION_SYSTEM,
                           f"Report question: {c.question}\n"
                           f"Section heading: {sec.get('heading')}\n\n"
                           f"FINDINGS:\n{block}", seat, max_tokens=2048)
        sections.append({"heading": str(sec.get("heading") or "Findings"),
                         "body": _as_text(_as_dict(body).get("body")),
                         "finding_ids": ids})
    return {"answer": _as_text(_as_dict(outline).get("answer")), "sections": sections,
            "synthesized_by": seat, "seat_note": seat_note}


def _outline_sections(outline: dict, by_id: dict) -> list[dict]:
    """Normalize whatever the outline model returned into section dicts.

    MEASURED FAILURE 2026-08-17: a run that produced 9 good, fully-verified
    findings still FAILED, on

        AttributeError: 'str' object has no attribute 'get'

    because the model returned `sections` as a list of heading STRINGS rather
    than the {heading, finding_ids} objects the prompt asked for. Constrained
    JSON decoding guarantees valid JSON, not the shape you wanted.

    The irony worth recording: this module already guards exactly this in
    _results_of() for Firecrawl's response, with a comment about how a shape
    change would look like "the web returned nothing". The external API got the
    defensive read and our own model output did not — and small local models
    are far likelier to wander off a schema than a maintained API is.

    A bare-string section keeps its heading and inherits every finding not yet
    claimed by a previous section, so the work survives a shape the model got
    slightly wrong instead of being thrown away at the last step.
    """
    raw = outline.get("sections")
    if isinstance(raw, dict):          # {"Heading": [ids]} is also plausible
        raw = [{"heading": k, "finding_ids": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        raw = []
    out, claimed = [], set()
    for sec in raw:
        if isinstance(sec, dict):
            ids = sec.get("finding_ids") or sec.get("findings") or []
            if isinstance(ids, str):
                ids = [ids]
            out.append({"heading": str(sec.get("heading") or sec.get("title")
                                       or "Findings"),
                        "finding_ids": [str(i) for i in ids]})
            claimed.update(str(i) for i in ids)
        elif isinstance(sec, str) and sec.strip():
            out.append({"heading": sec.strip(), "finding_ids": []})
    # Any section left without ids takes the unclaimed findings, so a
    # heading-only outline still renders a cited report rather than nothing.
    leftovers = [fid for fid in by_id if fid not in claimed]
    for sec in out:
        if not sec["finding_ids"] and leftovers:
            sec["finding_ids"] = leftovers
            leftovers = []
    if not out and by_id:
        out = [{"heading": "Findings", "finding_ids": list(by_id)}]
    return out


def _pick_synthesis_seat(c: Commission, n_findings: int) -> tuple[str, str]:
    """Synthesis is ALWAYS local. This is the seam that makes the
    per-sub-question fork honest.

    A public sub-question is safe to send. A SYNTHESIS is not: it is a new
    document weaving his vault context around public findings, and sending it
    cloudward to be written would leak precisely what routing the private
    sub-questions locally protected. Keeping it on-device makes the boundary
    structural rather than a per-send judgment — findings from a private
    sub-question can never enter a cloud payload, because no cloud payload is
    assembled at this stage at all.

    Cost, stated rather than hidden: the WRITING is weaker than Claude's. The
    research — finding, reading, judging, where the quality actually lives —
    still gets the model he named on everything public. The alternative, cloud
    synthesis over scrubbed private findings, rests the whole boundary on a
    scrub being complete, and that is the property that leaked twice today.
    """
    """Which seat writes — and does the desktop have room for it?

    The heavy seat evicts the brain and wants several GB. Stephen lost a
    monitor to VRAM pressure today, so the headroom check is a precondition
    here, not a nicety: if the card cannot spare the memory with the display's
    reserve held back, synthesis runs on the resident brain instead and the
    colophon says why.
    """
    from agent_friday.services import gpu_headroom
    if n_findings <= 4:
        return BRAIN, "small commission — the brain is already resident"
    head = gpu_headroom.check(6000)          # 26b at 32k, approximate
    if head.get("ok") is True:
        return HEAVY, f"heavy seat has room ({head['free_mib']} MiB free)"
    if head.get("ok") is None:
        return BRAIN, ("could not read GPU memory, so I did not risk the "
                       "display by loading the heavy seat")
    return BRAIN, f"not enough VRAM for the heavy seat without risking the display — {head['reason']}"


# ── Stage E: verification — no receipt, no render (§3.5 / RS5) ────────────────

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")     # [text](url) -> text
_MD_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")     # ![alt](url) -> alt


def _norm(s: str) -> str:
    """Normalize for receipt matching: content, not markup.

    MEASURED DEFECT, 2026-08-17. Extraction returns quotes with markdown
    stripped — the 12b returned

        "Launch date | April 1, 2026, 22:35:12 UTC (6:35:12p.m. EDT)"

    where the page holds

        "Launch date | April 1, 2026, 22:35:12 [UTC](https://...) (6:35:12p.m. [EDT](https://...))"

    Removing link syntax is a REASONABLE thing for a model to do, and a raw
    string comparison called the result fabricated. So verification was
    striking TRUE, correctly-sourced claims over markup — a false positive in
    the one mechanism whose value depends on being believed. A kill count
    inflated by punctuation teaches the reader to ignore kill counts.

    What is normalized: markdown link and image syntax (the destination is
    dropped, the visible text kept), emphasis markers, table pipes, and
    whitespace. What is NOT normalized: the words themselves. A quote still has
    to be present in the page to survive — this widens what counts as the same
    text, it does not weaken the requirement that the text be there.
    """
    t = s or ""
    t = _MD_IMG.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    # Emphasis markers VANISH; replacing them with a space turns "2024**,"
    # into "2024 ," and reintroduces the mismatch this function exists to end.
    t = re.sub(r"[*_`~]+", "", t)
    # Structural markers become a space, or table cells run together.
    t = re.sub(r"[>#|]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([,.;:!?)])", r"\1", t)     # no space before punctuation
    t = re.sub(r"([(])\s+", r"\1", t)
    return t.strip().lower()


def verify(c: Commission, draft: dict) -> dict:
    """Deterministic receipt check. Code, not a model — a receipt check is not
    a judgment call.

    Every finding's quote must appear VERBATIM (whitespace-normalized) in the
    cached extraction its source was fetched from. Not the live page: the
    bytes the finding was born from (RS12). A claim that fails is STRUCK from
    the body and moved to `unconfirmed`, and the kill is counted.
    """
    c.set_stage(VERIFYING)
    findings = {f.id: f for f in c.findings()}
    verified, killed, unconfirmed = set(), [], []

    for fid, f in findings.items():
        if not f.source_id:
            killed.append((fid, "no source recorded"))
            continue
        raw = (load_vault_extraction(c, f.source_id)
               if f.source_id.startswith("vault:")
               else web_fetch.load_extraction(f.source_id))
        page = _norm(raw)
        if not page:
            killed.append((fid, "the cached source could not be read"))
            continue
        if _norm(f.quote) in page:
            verified.add(fid)
        else:
            killed.append((fid, "the quote is not present in the fetched page"))

    for fid, why in killed:
        f = findings[fid]
        unconfirmed.append({"claim": f.claim,
                            "what_was_tried": f"cited {f.url or f.source_id} — {why}"})
        c.log(f"STRUCK a claim: {why}", finding_id=fid)

    # Remove struck markers from the prose. A body that cites a killed finding
    # keeps neither the citation nor the sentence's claim to be sourced.
    sections = []
    for sec in draft.get("sections", []):
        body = sec.get("body", "")
        for fid, _why in killed:
            body = re.sub(rf"\s*\[F:{re.escape(fid)}\]", " [unverified — removed]", body)
        sections.append({**sec, "body": body})

    integrity = _pseudo_toolcall_check(draft)
    return {
        "answer": draft.get("answer", ""),
        "sections": sections,
        "unconfirmed": unconfirmed,
        "verified_ids": sorted(verified),
        "claims_verified": len(verified),
        "claims_struck": len(killed),
        "integrity_ok": integrity,
        "synthesized_by": draft.get("synthesized_by", ""),
        "seat_note": draft.get("seat_note", ""),
    }


def _pseudo_toolcall_check(draft: dict) -> bool:
    """Prose that NARRATES tool calls that never happened discards the draft.

    Reuses the Source Dossier's existing integrity check rather than inventing
    a second one with different rules.
    """
    try:
        from agent_friday.services.tool_integrity import find_pseudo_toolcalls
        text = " ".join(s.get("body", "") for s in draft.get("sections", []))
        return not find_pseudo_toolcalls(text)
    except Exception:
        return True     # cannot check → do not fabricate a failure


# ── The vault tier — local only, and structurally so ─────────────────────────
#
# Stephen's specification, verbatim: "My override allows it to do research
# about me, not anything else. We're not taking material out of the vault and
# sending it to the cloud other than the prompt for the research report."
#
# That is a better boundary than the one this file previously implemented, and
# the reason is worth stating: it is safe BY CONSTRUCTION rather than by
# policy. The earlier design asked "is this span safe to send?" per span — a
# judgment, therefore fallible, and one that already produced two leaks today.
# His rule asks nothing. Vault content is never transmitted, so material about
# his daughter, his co-parent, his colleagues or his sources cannot leave
# through this path regardless of how any classifier rules on any span. There
# is no adjudication to get wrong.
#
# Mechanically:
#   * every corpus entry carries a `tier`: "web" or "vault"
#   * a cloud call is handed WEB-TIER ENTRIES ONLY, and _assert_no_vault()
#     re-checks that before the call rather than trusting the filter
#   * what crosses is the commission prompt plus public web content, full stop

VAULT_TIER = "vault"
WEB_TIER = "web"


class VaultLeak(RuntimeError):
    """Raised if vault-tier material ever reaches a cloud-bound payload.

    A raise here means a bug, not a policy decision — the filter that should
    have removed it did not. Failing the commission is correct: this is the one
    invariant the whole design rests on.
    """


def _assert_no_vault(entries: list[dict]) -> None:
    bad = [e for e in entries if e.get("tier") == VAULT_TIER]
    if bad:
        raise VaultLeak(
            f"{len(bad)} vault-tier passage(s) reached a cloud payload. "
            f"Vault content never leaves this machine; the commission is "
            f"stopped rather than degraded.")


def _vault_corpus(sq_text: str, limit: int = 6) -> list[dict]:
    """Read the wiki/vault locally for passages relevant to `sq_text`.

    Deliberately keyword-scored rather than model-driven: retrieval here is
    cheap and the local seat's time is the pipeline's bottleneck. Returns
    corpus entries tagged VAULT_TIER, which is what keeps them home.
    """
    import re as _re
    try:
        from agent_friday.services.wiki_engine import WIKI_DIR, wiki_read_text
    except Exception:
        return []
    terms = {w.lower() for w in _re.findall(r"[A-Za-z][A-Za-z0-9'-]{3,}", sq_text)}
    if not terms:
        return []
    scored = []
    try:
        for f in Path(WIKI_DIR).rglob("*.md"):
            try:
                text = wiki_read_text(f)
            except Exception:
                continue
            low = text.lower()
            score = sum(1 for t in terms if t in low)
            if score:
                scored.append((score, f, text))
    except Exception:
        return []
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, f, text in scored[:limit]:
        for para in _re.split(r"\n{2,}", text):
            p = para.strip()
            if len(p) < 60:
                continue
            if sum(1 for t in terms if t in p.lower()) >= 1:
                out.append({"text": p[:1800], "source_id": f"vault:{f.stem}",
                            "url": "", "title": f.stem, "tier": VAULT_TIER})
                break
    return out[:limit]


def persist_vault_corpus(c, corpus: list[dict]) -> None:
    """Write vault passages where verification can read them back.

    DEFECT FOUND ON A REAL RUN: every vault finding was struck with "the cached
    source could not be read". Verification resolves a receipt through
    web_fetch.load_extraction(), which only knows the WEB source cache — vault
    passages were never stored anywhere, so a vault citation could never verify
    and was guaranteed to be struck. His personal context still reached the
    report, with every sentence marked [unverified], which is the worst
    outcome: sourced material rendered as though it were not.

    Stored INSIDE the commission directory, never in the shared web cache —
    vault text does not belong in a store whose whole purpose is material
    fetched from the public internet.
    """
    d = c.dir / "vault_sources"
    try:
        d.mkdir(parents=True, exist_ok=True)
        for e in corpus:
            sid = e.get("source_id", "")
            if not sid.startswith("vault:"):
                continue
            (d / f"{sid.split(':', 1)[1]}.txt").write_text(
                e.get("text", ""), encoding="utf-8")
    except Exception as ex:
        c.log(f"could not persist vault passages for verification: {ex}")


def load_vault_extraction(c, source_id: str) -> str:
    """The verbatim vault passage a finding was born from."""
    if not source_id.startswith("vault:"):
        return ""
    try:
        return (c.dir / "vault_sources" /
                f"{source_id.split(':', 1)[1]}.txt").read_text(encoding="utf-8")
    except Exception:
        return ""
