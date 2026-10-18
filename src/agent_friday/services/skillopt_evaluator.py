"""SkillOpt's scorer, wired to practice tasks (harness2 adoption HA4).

SkillOptEngine.run_epoch compares a candidate version of a skill with the
current best on eval cases, but nothing ever gave it an evaluator, so it could
only compare logged averages. This module supplies one, from the services layer
(skillopt_engine is a leaf module and must not import upward):

* ``make_evaluator`` runs one practice episode per case with the version's text
  as the skill in the child's scratch home, and returns
  {"accuracy": pass, "completeness": score, "latency_ms": wall, "cost_usd": 0.0,
   "safety_fail": bool}. A case that could not be graded RAISES (EvalMissing),
  so run_epoch counts it as missing, never as a zero for both sides.
* ``recommend`` turns a "recommended" epoch into the owner's trial card
  (services/trials); the champion changes only when that card is approved.
* ``queue_finding`` turns an auto-research edit into a queued trial instead of
  a registered version.

Eval cases are the development practice tasks tagged with the skill's name.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

RULE = "skillopt-paired-1 (2026-10-18): >= 6 graded cases, paired per case, a tie keeps the baseline, any safety mistake rejects"


class EvalMissing(RuntimeError):
    """The case could not be graded; not the skill's fault."""


def cases_for(skill_name: str, pack: Optional[List[dict]] = None) -> List[dict]:
    from agent_friday.task_eval import pack as P
    tasks = pack if pack is not None else P.load_pack("dev")
    return [{"task": t} for t in P.tasks_touching(tasks, [skill_name])]


def make_evaluator(run_one: Optional[Callable[..., dict]] = None, *, endpoint: str = "",
                   model: str = "") -> Callable[[str, str, dict], Dict[str, float]]:
    if run_one is None:
        from agent_friday.task_eval.runner import run_episode

        def run_one(**kw):
            return run_episode(endpoint=endpoint, model=model, **kw)
    counter = {"n": 0}

    def evaluator(skill_name: str, content: str, case: dict) -> Dict[str, float]:
        task = case.get("task")
        if not isinstance(task, dict):
            raise EvalMissing("the eval case names no practice task")
        counter["n"] += 1
        overlay = {"files": {f"skills/{skill_name}/SKILL.md": content}}
        with tempfile.TemporaryDirectory(prefix="friday-skillopt-") as d:
            res = run_one(task=task, arm="v", repeat=counter["n"], overlay=overlay, out_dir=Path(d))
        g = (res or {}).get("grade") or {}
        if g.get("status") != "graded":
            raise EvalMissing(f"missing: {g.get('reason') or 'not graded'}")
        wall = float((res or {}).get("wall_seconds") or 0.0)
        return {"accuracy": 1.0 if g.get("pass") else 0.0,
                "completeness": float(g.get("score") or 0.0),
                "latency_ms": wall * 1000.0, "cost_usd": 0.0,
                "safety_fail": bool(g.get("safety_fail"))}
    return evaluator


def recommend(skill_name: str, candidate, epoch) -> Optional[dict]:
    """A recommended epoch becomes a screen-only trial card. Approving it calls
    SkillOptEngine.promote_from_card; denying it keeps the champion."""
    from agent_friday.services import trials
    import agent_friday.skillopt_engine as sopt
    best = sopt.get_engine().storage(skill_name).best_version()
    rel = f"skills/{skill_name}/SKILL.md"
    current = {"files": {rel: best.content}} if best else {"files": {}}
    cand = trials.propose(
        {"kind": "skill", "id": skill_name, "title": f"SkillOpt: a better version of '{skill_name}'"},
        current=current, candidate={"files": {rel: candidate.content}}, origin="skillopt",
        apply={"skillopt": {"skill": skill_name, "version_id": candidate.version_id,
                            "score": float(epoch.candidate_score)}},
        reason=epoch.reason, diff=candidate.diff_against(best) if best else "", tags=[skill_name])
    cand = trials.load(cand["id"]) or cand
    cand["status"] = "decided"
    cand["result"] = {"rule": RULE, "recommend": "candidate", "label": epoch.reason,
                      "subject_tally": {}, "passes": {}, "missing": {}, "regression_tally": {},
                      "new_safety_failures": [], "skillopt": {
                          "candidate_score": epoch.candidate_score,
                          "baseline_score": epoch.baseline_score,
                          "batch_size": epoch.batch_size}}
    trials.save(cand)
    return trials.raise_card(cand)


def queue_finding(skill_name: str, new_content: str, parent_version: str, summary: str, finding) -> str:
    """An auto-research edit becomes a queued practice-task trial."""
    from agent_friday.services import trials
    import agent_friday.skillopt_engine as sopt
    parent = sopt.get_engine().storage(skill_name).read_version(parent_version)
    rel = f"skills/{skill_name}/SKILL.md"
    current = {"files": {rel: parent.content}} if parent else {"files": {}}
    cand = trials.propose(
        {"kind": "skill", "id": skill_name, "title": f"SkillOpt edit: {summary[:80]}"},
        current=current, candidate={"files": {rel: new_content}}, origin="skillopt_finding",
        apply={"skillopt_new_version": {"skill": skill_name, "content": new_content,
                                        "parent": parent_version, "summary": summary}},
        reason=summary, tags=[skill_name])
    return cand["id"]


def install(*, endpoint: str = "", model: str = "") -> None:
    """Wire all three into the engine singleton (called before a SkillOpt run)."""
    import agent_friday.skillopt_engine as sopt
    sopt.set_evaluator(make_evaluator(endpoint=endpoint, model=model))
    sopt.set_recommender(recommend)
    sopt.set_candidate_sink(queue_finding)
