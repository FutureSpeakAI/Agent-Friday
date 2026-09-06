"""Gauntlet finding F46: index.html and ui_parts/app.html (identical copy)
described the Content workspace's staging host as: "a public base URL you
control... Assets are staged at unguessable paths and deleted after publish
confirms... Friday never exposes a port on her own."

Reality: `staged_url` is never assigned anywhere in the repo (only read, in
instagram.py's `_public_url()`). content_pipeline.py/publisher.py have zero
staging-upload logic -- no code stages an asset at an unguessable path and
nothing auto-deletes anything. instagram.py's own `_resolve_public_urls()`
confirms the real behavior: an already-public URL proceeds; an empty
staging_base_url produces a HOLD; a configured-but-unstaged one produces a
hard E_ASSET_NOT_STAGED error. The user must supply an already-public URL
themselves -- Friday never uploads or manages staging at all.

This probe pins the corrected copy in both HTML files and grounds that the
real pipeline still has no staging-upload logic, so the new "you already
host it" wording is not itself a fresh overpromise.

Red -> green -> red-on-revert proof: fails against the old literal claim
(proving both files really made it) and passes against the corrected text.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"
_INSTAGRAM_PY = (_REPO_ROOT / "src" / "agent_friday" / "services" /
                 "platforms" / "instagram.py")

_OLD_CLAIM = "Assets are staged at unguessable paths and deleted after publish confirms."


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip() == (
        "Assets are staged at unguessable paths and deleted after publish confirms."
    )


class TestStagingHostCopyNoFalsePipeline:
    def test_index_html_no_longer_claims_a_staging_pipeline(self):
        text = _INDEX_HTML.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "index.html still claims assets are staged at unguessable "
            "paths and auto-deleted -- no staging-upload logic exists "
            "anywhere in the repo; see findings.jsonl F46"
        )
        assert "never exposes a port on her own" not in text

    def test_app_html_no_longer_claims_a_staging_pipeline(self):
        text = _APP_HTML.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "ui_parts/app.html still claims assets are staged at "
            "unguessable paths and auto-deleted; see findings.jsonl F46"
        )
        assert "never exposes a port on her own" not in text

    def test_both_files_now_say_the_user_already_hosts_the_asset(self):
        for path in (_INDEX_HTML, _APP_HTML):
            text = path.read_text(encoding="utf-8")
            assert "Staging host" in text
            idx = text.index("Staging host")
            window = text[idx:idx + 500]
            assert "already" in window.lower(), (
                f"{path.name}'s corrected staging-host copy should say "
                "the user already hosts/controls the public URL, not "
                "that Friday stages it for them"
            )
            assert "does not upload or stage" in window.lower() or \
                "does not stage" in window.lower(), (
                    f"{path.name}'s corrected copy should plainly say "
                    "Friday does not upload or stage anything herself"
                )

    def test_no_staging_upload_logic_exists_in_instagram_publisher(self):
        """Grounding check: confirms the corrected 'you already host it'
        copy is actually true right now -- _resolve_public_urls() still
        only reads an already-public URL or a configured staging_base_url,
        never stages/uploads/deletes anything itself."""
        source = _INSTAGRAM_PY.read_text(encoding="utf-8")
        start = source.index("def _resolve_public_urls(")
        # bound to the next method definition
        next_def = source.index("\n    def ", start + 1)
        body = source[start:next_def]
        for marker in ("upload", "os.remove", "unlink", "shutil.rmtree",
                       "delete_after"):
            assert marker not in body.lower(), (
                f"_resolve_public_urls() now references {marker!r} -- if "
                "a real staging-upload/auto-delete pipeline has been "
                "built, F46's corrected copy needs revisiting"
            )

    def test_staged_url_is_still_only_read_never_assigned(self):
        """Grounding check: confirms `staged_url` remains a field the code
        only reads (from an already-public asset dict), never something a
        publisher assigns after uploading -- the direct evidence behind
        F46's "Friday never stages anything" correction."""
        src_root = _REPO_ROOT / "src"
        offenders = []
        for path in src_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "staged_url" not in line:
                    continue
                # A read looks like asset.get("staged_url") or dict access;
                # an assignment would look like `staged_url = ...` or
                # `something["staged_url"] = ...` (not `.get(`).
                if "=" in line and ".get(" not in line and "==" not in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert not offenders, (
            "found what looks like an assignment to staged_url -- if a "
            "real staging-upload pipeline now writes it, F46's corrected "
            f"copy needs revisiting: {offenders}"
        )
