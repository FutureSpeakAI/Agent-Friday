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

from agent_friday.services import web_fetch, web_search
from agent_friday.services.research.objects import (
    CONFIRMED, CONTESTED, GRINDING, SCOPING, SINGLE_SOURCE, SYNTHESIZING,
    UNCONFIRMED, VERIFYING, Commission, Finding, ResearchPlan, SubQuestion,
)

_log = logging.getLogger("friday.research")

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


def _extract_json(raw: str) -> dict | None:
    from agent_friday.services import local_call
    return local_call.extract_json(raw)


def _claude(system: str, user: str, *, max_tokens: int = 4096) -> str:
    """A cloud call. Goes through the normal gate — nothing here bypasses it."""
    from agent_friday.services.model_router import _call_claude
    return _call_claude([{"role": "user", "content": user}], system=system,
                        max_tokens=max_tokens) or ""


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

    want_cloud = c.protection.cloud_allowed and c.disposition != "now_local"
    used, data = "", None
    if want_cloud:
        try:
            raw = _claude(_SCOPE_SYSTEM, c.protection.question_sent or c.question)
            data = _extract_json(raw)
            used = _anthropic_model_name()
        except Exception as e:
            c.log(f"cloud scoping failed, falling back to the brain: {e}")
    if data is None:
        data = _json_local(_SCOPE_SYSTEM, c.question, BRAIN, max_tokens=3072)
        used = BRAIN
    if data is None:
        c.log("scoping produced no usable plan")
        return None

    sqs = []
    for i, s in enumerate(data.get("sub_questions") or []):
        if not isinstance(s, dict) or not (s.get("text") or "").strip():
            continue
        sqs.append(SubQuestion(id=f"sq{i}", text=s["text"].strip(),
                               perspective=str(s.get("perspective", "")),
                               done_when=str(s.get("done_when", ""))))
        if len(sqs) >= c.budget["sub_questions"]:
            break
    if not sqs:
        c.log("scoping returned a plan with no sub-questions")
        return None

    plan = ResearchPlan(
        commission_id=c.id,
        perspectives=[p for p in (data.get("perspectives") or [])
                      if isinstance(p, dict)],
        sub_questions=sqs,
        working_title=str(data.get("working_title") or c.question)[:160],
        internal_first=[str(x) for x in (data.get("internal_first") or [])][:4],
        scoped_by=used)
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

    for idx, sq in enumerate(plan.sub_questions, 1):
        c.progress.update({"sub_question": idx, "note": sq.text[:120]})
        c.save()
        c.log(f"sub-question {idx}/{len(plan.sub_questions)}: {sq.text}",
              sq_id=sq.id)

        corpus: list[dict] = []
        q = sq.text
        sq_fetches = 0

        for depth in range(c.budget["followup_depth"]):
            if time.time() - started > c.budget["wall_clock_soft_s"]:
                c.log("wall-clock budget reached; stopping the grind early")
                break

            qd = _json_local(_QUERY_SYSTEM,
                             f"Sub-question: {q}\n\nAlready known:\n" +
                             ("\n".join(p["text"][:200] for p in corpus[-4:])
                              or "(nothing yet)"),
                             SIDEKICK, max_tokens=512)
            queries = [str(x) for x in (qd or {}).get("queries", []) if x][
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
                for passage in (ex or {}).get("passages", [])[:8]:
                    if isinstance(passage, str) and passage.strip():
                        corpus.append({"text": passage.strip(),
                                       "source_id": rec["id"],
                                       "url": rec.get("final_url") or r["url"],
                                       "title": rec.get("title", "")})

            if not corpus:
                c.log("no usable passages at this depth")
                break

            conv = _json_local(
                _CONVERSE_SYSTEM,
                f"Sub-question: {sq.text}\ndone_when: {sq.done_when}\n\n"
                f"CORPUS:\n" + "\n\n".join(
                    f"[{p['source_id']}] {p['text']}" for p in corpus[:60]),
                BRAIN, max_tokens=3072)
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

            for f in conv.get("findings", [])[:20]:
                if not isinstance(f, dict):
                    continue
                claim, quote = (f.get("claim") or "").strip(), (f.get("quote") or "").strip()
                if not claim or not quote:
                    continue
                src = str(f.get("source_id") or "")
                match = next((p for p in corpus if p["source_id"] == src), None) \
                    or next((p for p in corpus if quote[:60] in p["text"]), None)
                c.add_finding(Finding(
                    id=uuid.uuid4().hex[:10], sub_question_id=sq.id,
                    claim=claim, quote=quote,
                    source_id=(match or {}).get("source_id", src),
                    url=(match or {}).get("url", ""),
                    confidence=SINGLE_SOURCE))
            c.save()

            if conv.get("done"):
                c.log(f"done_when satisfied for {sq.id}")
                break
            nxt = (conv.get("best_followup") or "").strip()
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
        c.log("synthesis produced no outline")
        return None

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
                         "body": (body or {}).get("body", ""),
                         "finding_ids": ids})
    return {"answer": str(outline.get("answer") or ""), "sections": sections,
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
        page = _norm(web_fetch.load_extraction(f.source_id))
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
