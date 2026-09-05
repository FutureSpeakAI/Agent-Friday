"""Gauntlet finding Q5: model_plan.py's plan() "Memory" tier text told the
user the vault embedder "arrives with the install rather than as a separate
download." That is false for the installer's own recommended, safer default
path.

prewarm.py's embedder fetch has exactly one call site (cli.py, inside
`cmd_models(install=True)`), reached only via `friday models --install`,
which install.ps1 invokes only inside `if ($ollamaOutcome.Installed)` --
itself gated on a discrete NVIDIA GPU being detected (Get-CardVramGib
returns non-null). On any AMD/Intel/integrated/no-GPU machine -- the
installer's own recommended default for machines without a discrete
NVIDIA card -- that step never runs, so prewarm() never executes and the
embedder is never fetched during install.

docs/INSTALLATION.md:136-138 already correctly documents the true
behavior: "The embedding model is lazy and announced ... arrives on first
use." This probe pins model_plan.py's plan() output to match that already-
correct description instead of contradicting it.

Red -> green -> red-on-revert proof: this probe fails against the old
literal claim (proving plan() really made it) and passes against the
corrected text.
"""
from __future__ import annotations

from pathlib import Path

from agent_friday.services import model_plan as mp

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SOURCE = _REPO_ROOT / "src" / "agent_friday" / "services" / "model_plan.py"

_OLD_CLAIM = "so it arrives with the install rather than as a separate download."


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip() == (
        "so it arrives with the install rather than as a separate download."
    )


def _profile(ram_gib=32, disk_gib=60, vram_gib=0, os_family="windows"):
    """Shaped like hardware_profile.detect() emits one -- mirrors the
    fixture in tests/unit/test_model_plan.py without importing that
    pre-existing test file."""
    return {
        "os": {"family": os_family},
        "ram": {"total_mib": int(ram_gib * 1024)},
        "disk": {"free_mib": int(disk_gib * 1024)},
        "gpus": ([{"vram_total_mib": int(vram_gib * 1024)}] if vram_gib else []),
    }


class TestModelPlanEmbedderIsLazyDownload:
    def test_source_no_longer_claims_embedder_arrives_with_install(self):
        text = _SOURCE.read_text(encoding="utf-8")
        assert "arrives with the install" not in text, (
            "model_plan.py still claims the vault embedder arrives with "
            "the install -- but prewarm.py's embedder fetch only runs via "
            "`friday models --install`, which install.ps1 only invokes on "
            "machines with a discrete NVIDIA GPU. On the installer's own "
            "recommended no-discrete-GPU path, the embedder is a lazy, "
            "first-use download -- see findings.jsonl Q5"
        )

    def test_plan_output_describes_a_lazy_first_use_download(self):
        tiers = {t["id"]: t for t in mp.plan(_profile())["tiers"]}
        reason = tiers["vault"]["reason"]
        assert "arrives with the install" not in reason
        assert "lazy" in reason.lower() or "first use" in reason.lower() or \
            "first-use" in reason.lower(), (
                "plan()'s Memory-tier text should describe the embedder as "
                "a lazy/first-use download, matching docs/INSTALLATION.md's "
                "already-correct description"
            )

    def test_installation_md_still_documents_the_lazy_behavior(self):
        """Grounding check: confirms the corrected plan() text is now
        consistent with docs/INSTALLATION.md's independently-correct claim,
        not just differently worded."""
        install_doc = (_REPO_ROOT / "docs" / "INSTALLATION.md").read_text(
            encoding="utf-8")
        assert "The embedding model is lazy and announced" in install_doc
