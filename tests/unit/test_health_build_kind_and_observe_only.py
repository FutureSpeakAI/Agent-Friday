"""The health line says which build this is, and does not call design a fault.

From the 5.14.2 clean-install walkthrough. `/api/health` carried:

    Sensitivity classifier: 3/4 layers active (source checkout). DEGRADED -
    not running: presidio - Name and entity detection (not installed in this
    build).

Two things wrong in one sentence.

**"source checkout" on an installed build.** `where` was a two-way choice
between a PyInstaller bundle and "source checkout", so a `pip install` -- which
is neither -- was described as somebody's working copy. Three states exist and
all three should be nameable.

**"DEGRADED" for a layer that is off on purpose.** Presidio is INSTALLED by the
Windows installer's recommended tier and deliberately left observe-only:
`classify()` consults it only under FRIDAY_PRESIDIO_ENFORCE=1. This module is
right not to count it as active -- a layer that cannot change an outcome is not
a protection, which is the whole point of the file -- but "DEGRADED" says
something is broken, and nothing is. A layer that genuinely cannot load still
has to read as degraded, and that contract is asserted here too.
"""

import pytest

from agent_friday.services import privacy_layers as pl


# ── which build is this ────────────────────────────────────────────────────

def test_a_source_checkout_is_named_a_source_checkout(monkeypatch, tmp_path):
    pkg = tmp_path / "src" / "agent_friday"
    (pkg / "services").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(pl, "is_frozen", lambda: False)
    monkeypatch.setattr(pl, "_package_dir", lambda: pkg)
    assert pl.build_kind() == "source checkout"


def test_an_installed_build_is_not_called_a_source_checkout(monkeypatch, tmp_path):
    """The reported defect: a pip install is neither frozen nor a checkout."""
    pkg = tmp_path / "Lib" / "site-packages" / "agent_friday"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(pl, "is_frozen", lambda: False)
    monkeypatch.setattr(pl, "_package_dir", lambda: pkg)
    kind = pl.build_kind()
    assert kind != "source checkout", "an installed build is still called a checkout"
    assert kind == "installed build", kind


def test_a_frozen_bundle_still_says_frozen(monkeypatch):
    monkeypatch.setattr(pl, "is_frozen", lambda: True)
    assert pl.build_kind() == "frozen build"


def test_a_src_dir_without_a_pyproject_is_not_a_checkout(monkeypatch, tmp_path):
    """Fail towards 'installed': claiming a checkout implies a developer's
    working copy, which is the more misleading of the two."""
    pkg = tmp_path / "src" / "agent_friday"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(pl, "is_frozen", lambda: False)
    monkeypatch.setattr(pl, "_package_dir", lambda: pkg)
    assert pl.build_kind() == "installed build"


def test_the_headline_names_the_build(monkeypatch):
    monkeypatch.setattr(pl, "build_kind", lambda: "installed build")
    assert "installed build" in pl.describe()


def test_an_unknowable_layout_never_raises(monkeypatch):
    def boom():
        raise OSError("no filesystem")
    monkeypatch.setattr(pl, "is_frozen", lambda: False)
    monkeypatch.setattr(pl, "_package_dir", boom)
    assert pl.build_kind() in ("installed build", "source checkout", "frozen build")


# ── observe-only is intended, not degraded ─────────────────────────────────

def _presidio_installed_observe_only(monkeypatch):
    """A HEALTHY install where presidio is simply not enforced.

    Everything importable and Layer 3 actually running, so the only inactive
    layer is the deliberately observe-only one. Leaving sentence_transformers
    unavailable here would make the run genuinely degraded and the test would be
    asserting the wrong thing.
    """
    monkeypatch.delenv("FRIDAY_PRESIDIO_ENFORCE", raising=False)
    monkeypatch.setattr(pl, "_module_available", lambda m: True)
    monkeypatch.setattr(pl, "_embedding_runtime",
                        lambda mod: (True, "%s loaded" % mod))


def test_observe_only_presidio_is_not_degraded(monkeypatch):
    """The reported defect. Not counted as a layer -- correctly -- but not a
    fault either."""
    _presidio_installed_observe_only(monkeypatch)
    text = pl.describe()
    assert "DEGRADED" not in text, text
    assert pl.self_check()["ok"] is True, pl.self_check()


def test_observe_only_presidio_is_reported_as_by_design(monkeypatch):
    _presidio_installed_observe_only(monkeypatch)
    chk = pl.self_check()
    assert "presidio" in chk["by_design"], chk
    assert "presidio" not in chk["missing"], chk
    assert "presidio" in pl.describe()      # still SAID, just not as a fault


def test_it_still_does_not_count_as_an_active_layer(monkeypatch):
    """The invariant this module exists for: importable is not in force."""
    _presidio_installed_observe_only(monkeypatch)
    assert pl.probe_layers()["presidio"]["active"] is False
    chk = pl.self_check()
    assert "presidio" not in chk["active"]
    assert "%d/%d" % (len(chk["active"]), len(chk["declared"])) in pl.describe()


def test_a_layer_that_really_is_down_is_still_degraded(monkeypatch):
    """The honesty contract, kept. Layer 3 installed but not running is a
    fault and must read as one."""
    monkeypatch.delenv("FRIDAY_PRESIDIO_ENFORCE", raising=False)
    monkeypatch.setattr(pl, "_module_available", lambda m: True)
    monkeypatch.setattr(pl, "_embedding_runtime",
                        lambda mod: (False, "sentence_transformers imported but "
                                            "the model is not loaded"))
    chk = pl.self_check()
    assert "embedding" in chk["missing"], chk
    assert chk["ok"] is False
    text = pl.describe()
    assert "DEGRADED" in text and "embedding" in text, text


def test_presidio_absent_from_the_build_is_intended_not_degraded(monkeypatch):
    """SUPERSEDED 2026-09-26: this asserted the opposite.

    A build that deliberately leaves Presidio out is the normal case -- the
    capability report already says "presidio_analyzer absent by design" -- and
    two parts of one product must not disagree about whether the same state is
    intended. So absent reads as a choice, like observe-only, and the line says
    which of the two it is.
    """
    monkeypatch.delenv("FRIDAY_PRESIDIO_ENFORCE", raising=False)
    monkeypatch.setattr(pl, "_module_available",
                        lambda m: m != "presidio_analyzer")
    monkeypatch.setattr(pl, "_embedding_runtime",
                        lambda mod: (True, "%s loaded" % mod))
    chk = pl.self_check()
    assert "presidio" in chk["by_design"], chk
    assert "presidio" not in chk["missing"], chk
    assert chk["ok"] is True
    text = pl.describe()
    assert "DEGRADED" not in text, text
    assert "not installed in this build" in text, text


def test_presidio_asked_for_and_missing_is_a_fault(monkeypatch):
    """Enforcement is somebody saying they want it deciding. Then absent is a
    fault, whatever the default would have been."""
    monkeypatch.setenv("FRIDAY_PRESIDIO_ENFORCE", "1")
    monkeypatch.setattr(pl, "_module_available",
                        lambda m: m != "presidio_analyzer")
    monkeypatch.setattr(pl, "_embedding_runtime",
                        lambda mod: (True, "%s loaded" % mod))
    chk = pl.self_check()
    assert "presidio" in chk["missing"], chk
    assert chk["ok"] is False
    assert "DEGRADED" in pl.describe()


def test_the_semantic_layer_absent_is_still_degraded(monkeypatch):
    """Layer 3 is shipped by the installer by default, so its absence means an
    install that meant to have it went wrong. Still a fault."""
    monkeypatch.delenv("FRIDAY_PRESIDIO_ENFORCE", raising=False)
    monkeypatch.setattr(pl, "_module_available",
                        lambda m: m != "sentence_transformers")
    chk = pl.self_check()
    assert "embedding" in chk["missing"], chk
    assert "embedding" not in chk["by_design"], chk
    assert chk["ok"] is False
    assert "DEGRADED" in pl.describe()


def test_observe_only_still_says_observe_only(monkeypatch):
    """The two by-design states are distinguishable in the line."""
    _presidio_installed_observe_only(monkeypatch)
    text = pl.describe()
    assert "observe-only" in text, text
    assert "not installed in this build" not in text, text


def test_enforced_presidio_is_an_active_layer(monkeypatch):
    monkeypatch.setenv("FRIDAY_PRESIDIO_ENFORCE", "1")
    monkeypatch.setattr(pl, "_module_available", lambda m: True)
    assert pl.probe_layers()["presidio"]["active"] is True
    assert pl.self_check()["ok"] is True


def test_the_startup_warning_still_tracks_real_faults(monkeypatch, caplog):
    """A warning at boot means something is wrong. Design does not warn."""
    _presidio_installed_observe_only(monkeypatch)
    with caplog.at_level("INFO"):
        chk = pl.report_at_startup()
    assert not chk["missing"]
    assert "WARNING" not in {r.levelname for r in caplog.records}


def test_the_headline_stays_ascii_safe(monkeypatch):
    _presidio_installed_observe_only(monkeypatch)
    pl.describe().encode("cp1252")
