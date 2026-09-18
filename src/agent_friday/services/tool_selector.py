"""Pick the tools THIS turn needs, instead of shipping all of them.

WHY THIS EXISTS
---------------
Measured on the reference machine, 2026-09-18: the system prompt runs
~104,700 characters (~26,000 tokens) and the 75 tool schemas add 51,284
characters (~12,800 tokens, and `tool_budget` counts ~12,740). That is close
to 39,000 tokens spent before the user has said anything. On a 64K seat it
is 60% of the window.

Friday already coped, by trimming. Badly. From friday.log:

    core tools trimmed 75 -> 48 ... to fit a 32768-token
    core tools trimmed 75 -> 8  ... to fit a 32768-token
    core tools trimmed 75 -> 40 ... to fit a 32768-token

That 75 -> 8 turn handed the model 11% of its capabilities with no regard
for which 11%, because the budget trims by *fitting*, not by *relevance*.
A model that cannot see `draft_email` will tell the user it cannot send
email. The capability was there; the list was cut blind.

So: choose. Sending the 12 tools a turn plausibly needs costs about 1,700
tokens instead of 12,800, and the choice is made on what the user asked for
rather than on what happened to be early in the array.

WHY NOT AN LLM
--------------
The obvious design is a small model that reads the catalogue and picks. It
was tried and measured first, because it sounded right:

    Ternary-Bonsai 4B on CPU, 2,790-token prompt, 10 tokens out:
        23,637 ms, and the answer was correct

Correct and useless. 23 seconds added to every turn is a far worse trade
than the 11,000 tokens it saves. Tool selection is a retrieval problem
wearing a reasoning problem's clothes.

Pure embedding retrieval was tried next, and failed differently. MiniLM on
terse tool descriptions ranked no email tool at all in the top 8 for "Email
Mahesh to confirm the 3:30 interview" -- the exact capability loss this
module exists to prevent.

What works is the hybrid below: semantic similarity for recall, idf-weighted
lexical overlap for the literal words ("email" -> `draft_email`), and a core
set that is never dropped whatever the scores say. Measured at 106-267 ms
per turn against the live 75-tool catalogue, with the five probe cases all
returning their expected tool.

FAILING OPEN
------------
Every failure path here returns None, and None means "no opinion, send
everything". A selector that cannot load must never be the reason a turn
loses its tools -- that would reproduce the 75 -> 8 defect with extra steps.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time

_log = logging.getLogger("friday.tool_selector")

# Tools that ship on every turn regardless of the query. These are the ones
# whose absence changes what Friday *is* rather than what it can look up:
# the ability to hand work off, to change seat, to move the UI, and to say
# it has started something. A scoring function should not get a vote on
# whether the agent can delegate.
CORE_TOOLS = (
    "spawn_task",
    "switch_model",
    "navigate",
    "search_web",
)

# Below this fraction of the top score a tool is not worth its schema.
_SCORE_FLOOR = 0.28
_DEFAULT_K = 12

_STOP = frozenset("""
a an and are as at be by can could do does for from had has have how i if in
is it its me my of on or please should so than that the their them then there
these they this to up us was we were what when where which who why will with
would you your
""".split())

_STATE: dict = {"sig": None, "emb": None, "docs": None, "df": None,
                "names": None, "model": None}
_LOCK = threading.Lock()


def _tokens(text: str) -> list:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP]


def _signature(tools) -> str:
    """Identity of the catalogue, so the index rebuilds when tools change."""
    names = "|".join(sorted((t.get("name") or "") for t in tools))
    return hashlib.sha256(names.encode("utf-8")).hexdigest()[:16]


def _corpus(tools) -> list:
    out = []
    for t in tools:
        name = (t.get("name") or "").replace("_", " ")
        desc = (t.get("description") or "").strip().replace("\n", " ")
        # The description is truncated deliberately: the tail of a long
        # description is usage caveats, which dilute the similarity signal
        # without adding recall.
        out.append(f"{name}. {desc[:240]}")
    return out


def _ensure_index(tools) -> bool:
    """Build (or reuse) the embedding index. False means "cannot select"."""
    sig = _signature(tools)
    with _LOCK:
        if _STATE["sig"] == sig and _STATE["emb"] is not None:
            return True
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except Exception as e:
            _log.info("tool selection unavailable (%s) -- sending all tools", e)
            return False
        try:
            t0 = time.time()
            model = _STATE.get("model") or SentenceTransformer("all-MiniLM-L6-v2")
            texts = _corpus(tools)
            emb = model.encode(texts, normalize_embeddings=True,
                               show_progress_bar=False)
            docs = [set(_tokens(t)) for t in texts]
            df: dict = {}
            for d in docs:
                for w in d:
                    df[w] = df.get(w, 0) + 1
            _STATE.update(sig=sig, emb=np.asarray(emb), docs=docs, df=df,
                          model=model,
                          names=[(t.get("name") or "") for t in tools])
            _log.info("tool index built: %d tools in %.2fs", len(tools),
                      time.time() - t0)
            return True
        except Exception as e:
            _log.warning("tool index build failed (%s) -- sending all tools", e)
            return False


def _lexical(query: str):
    """idf-weighted overlap. This is the half that catches literal words.

    Semantic similarity alone ranked no email tool in the top 8 for "Email
    Mahesh to confirm the 3:30 interview", because "email" as a word carries
    more signal here than "email" as a direction in embedding space.
    """
    import numpy as np
    docs, df = _STATE["docs"], _STATE["df"]
    n = len(docs)
    qt = _tokens(query)
    out = np.zeros(n)
    for i, d in enumerate(docs):
        s = 0.0
        for w in qt:
            if w in d:
                s += math.log(1 + n / (1 + df.get(w, 0)))
        out[i] = s
    top = out.max()
    return out / top if top else out


def select(tools, query: str, k: int = _DEFAULT_K):
    """Names of the tools this turn should carry, or None for "send all".

    None is not a failure signal the caller should log loudly; it is the
    honest answer whenever the index is unavailable or the query is empty.
    """
    if not tools:
        return None
    if not (query or "").strip():
        return None
    if len(tools) <= k + len(CORE_TOOLS):
        return None          # nothing to save; don't risk a wrong cut
    if not _ensure_index(tools):
        return None
    try:
        import numpy as np
        emb = _STATE["emb"]
        names = _STATE["names"]
        qe = _STATE["model"].encode([query], normalize_embeddings=True,
                                    show_progress_bar=False)[0]
        sem = emb @ qe
        span = (sem.max() - sem.min()) or 1.0
        sem = (sem - sem.min()) / span
        score = 0.55 * sem + 0.45 * _lexical(query)
        order = np.argsort(-score)
        best = float(score[order[0]]) or 1.0
        picked = []
        for i in order[:k]:
            if float(score[i]) / best < _SCORE_FLOOR and picked:
                break
            picked.append(names[i])
        for c in CORE_TOOLS:
            if c in names and c not in picked:
                picked.append(c)
        return picked
    except Exception as e:
        _log.warning("tool selection failed (%s) -- sending all tools", e)
        return None


def rank_texts(texts, query: str):
    """Indices of `texts` ordered most-relevant-first for `query`, or None.

    Same hybrid as `select`, exposed for callers that are choosing between
    blocks of prose rather than tool schemas — the smart-context loader is
    the first. None means "no opinion", and the caller keeps its own order.
    """
    if not texts or not (query or "").strip():
        return None
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None
    try:
        with _LOCK:
            model = _STATE.get("model") or SentenceTransformer("all-MiniLM-L6-v2")
            _STATE["model"] = model
        # Only the head of each block is embedded. A 40 KB wiki dump has its
        # subject in the first lines; the tail is detail that dilutes the
        # similarity signal and costs time to encode.
        heads = [(t or "")[:600] for t in texts]
        emb = model.encode(heads, normalize_embeddings=True,
                           show_progress_bar=False)
        qe = model.encode([query], normalize_embeddings=True,
                          show_progress_bar=False)[0]
        sem = np.asarray(emb) @ qe
        span = (sem.max() - sem.min()) or 1.0
        sem = (sem - sem.min()) / span

        docs = [set(_tokens(h)) for h in heads]
        n = len(docs)
        df: dict = {}
        for d in docs:
            for w in d:
                df[w] = df.get(w, 0) + 1
        qt = _tokens(query)
        lex = np.zeros(n)
        for i, d in enumerate(docs):
            s = 0.0
            for w in qt:
                if w in d:
                    s += math.log(1 + n / (1 + df.get(w, 0)))
            lex[i] = s
        top = lex.max()
        if top:
            lex = lex / top
        score = 0.55 * sem + 0.45 * lex
        return [int(i) for i in np.argsort(-score)]
    except Exception as e:
        _log.debug("block ranking failed (%s)", e)
        return None


def subset(tools, query: str, k: int = _DEFAULT_K):
    """`tools` filtered to the selection, or `tools` unchanged.

    The caller gets a list it can pass straight through, so a selector that
    declines to choose costs nothing but the tokens it was trying to save.
    """
    chosen = select(tools, query, k=k)
    if not chosen:
        return tools
    keep = set(chosen)
    out = [t for t in tools if (t.get("name") or "") in keep]
    return out or tools
