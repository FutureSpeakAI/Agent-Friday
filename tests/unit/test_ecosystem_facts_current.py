"""The facts snapshots match the real apps, and the extractor reads only what it says it reads.

Where FRIDAY_ECOSYSTEM_REPOS names the folder holding the app repos (the
release gate on a machine that has them), the checked-in snapshots must equal
a fresh extraction, and a missing repo fails. Elsewhere that one test is
skipped by name; the rest run on a synthetic repo.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENV = "FRIDAY_ECOSYSTEM_REPOS"

spec = importlib.util.spec_from_file_location("ecosystem_facts", REPO / "scripts" / "ecosystem_facts.py")
facts = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = facts      # dataclasses look the module up by name
spec.loader.exec_module(facts)

KOTLIN_GRADLE = '''
android {
    defaultConfig {
        applicationId = "org.example.companion"
        minSdk = 31          // a comment with versionCode = 999 in it
        targetSdk = 35
        versionCode = 7
        versionName = "1.2.3"
        val form = "phone"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        buildConfigField("String", "HELP", "\"https://example.org/help\"")
    }
    buildTypes {
        debug { applicationIdSuffix = ".debug" }
        release { isMinifyEnabled = true }
    }
}
'''
GROOVY_GRADLE = '''
android {
    defaultConfig {
        applicationId 'org.example.tv'
        minSdk 28
        targetSdk 34
        versionCode 3
        versionName '0.4'
    }
    buildTypes {
        release {
            signingConfig signingConfigs.debug   // sideload
        }
    }
}
'''
MANIFEST = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <!-- <uses-permission android:name="android.permission.READ_SMS" /> -->
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-feature android:name="android.hardware.camera" android:required="false" />
    <uses-feature android:name="android.hardware.type.watch" />
    <application>
        <service android:name=".Tile" android:permission="android.permission.BIND_QUICK_SETTINGS_TILE" />
    </application>
</manifest>
'''
PAIRING_SOURCE = '''
class Pairing {
    static final String KIND = "phone";
    void start() { call("POST", "/tv/v1/pair/start", new JSONObject().put("name", n).put("kind", KIND)); }
}
'''


def _write(root: Path, rel: str, text: str) -> None:
    file = root / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    """A folder of app repos holding one synthetic app, and an empty snapshot folder."""
    repo = tmp_path / "repos" / "example-app"
    _write(repo, "app/build.gradle.kts", KOTLIN_GRADLE)
    _write(repo, "app/src/main/AndroidManifest.xml", MANIFEST)
    _write(repo, "app/src/main/assets/capabilities/talk.json",
           json.dumps({"capability": "talk", "version": 2, "owner_only": False}))
    _write(repo, "app/src/main/assets/capabilities/actions.json",
           json.dumps({"capability": "actions", "version": 1, "owner_only": True,
                       "verbs": [{"verb": "timer.start", "gate": "A0"}, {"verb": "call.place", "gate": "A2"}]}))
    _write(repo, "app/src/main/java/org/example/Pairing.java", PAIRING_SOURCE)
    _write(repo, "app/src/test/java/org/example/Fake.java", 'String s = "/v1/pair/complete";')
    _write(repo, "README.md", "Install: adb connect 192.168.50.7:5555 on the living room device.")
    source = facts.AppSource("phone", "example-app", None, "app/build.gradle.kts",
                             "app/src/main/AndroidManifest.xml", "app/src/main/assets/capabilities")
    snapshots = tmp_path / "facts"
    snapshots.mkdir()
    return tmp_path / "repos", source, snapshots


def _checked_in(world):
    repos, source, snapshots = world
    (snapshots / "phone.json").write_text(facts.dump(facts.extract(source, repos)), encoding="utf-8")
    return repos, source, snapshots


def test_the_extractor_reads_identity_permissions_capabilities_and_pairing(world):
    repos, source, _ = world
    got = facts.extract(source, repos)["facts"]
    assert (got["application_id"], got["version_name"], got["version_code"]) == ("org.example.companion", "1.2.3", 7)
    assert (got["min_sdk"], got["target_sdk"], got["abis"], got["form"]) == (31, 35, ["arm64-v8a", "x86_64"], "phone")
    assert got["debug_suffix"] == ".debug" and got["release_signing"] == "none"
    assert got["permissions"] == ["android.permission.INTERNET", "android.permission.RECORD_AUDIO"]
    assert got["features_required"] == ["android.hardware.type.watch"]
    assert got["bound_services"] == ["BIND_QUICK_SETTINGS_TILE"]
    assert got["capabilities"]["actions"] == {"version": 1, "owner_only": True,
                                              "verbs": {"call.place": "A2", "timer.start": "A0"}}
    assert got["capabilities"]["talk"]["version"] == 2
    assert got["pairing"] == {"gateway": True, "relay": False, "discovery": False,
                              "declares_kind": "phone", "reports_app_version": False}
    assert got["routes"] == {"gateway": ["pair"], "phone_link": []}


def test_a_new_surface_in_the_apps_sources_changes_its_facts(world):
    repos, source, snapshots = _checked_in(world)
    assert facts.differences(repos, snapshots, apps=(source,)) == []
    _write(repos / source.repo, "app/src/main/java/org/example/Chats.kt",
           'val list = get("/tv/v1/chats"); val one = get("/tv/v1/chats/$id/messages"); link("/af/v1/item")')
    assert facts.extract(source, repos)["facts"]["routes"] == {"gateway": ["chats", "pair"], "phone_link": ["item"]}
    assert facts.differences(repos, snapshots, apps=(source,)) == [
        "phone: facts changed (routes); run with --write and review ecosystem.json"]


def test_groovy_build_files_and_debug_key_releases_are_read():
    got = facts.gradle_facts(GROOVY_GRADLE)
    assert (got["application_id"], got["version_name"], got["version_code"], got["min_sdk"]) == (
        "org.example.tv", "0.4", 3, 28)
    assert got["release_signing"] == "debug_key" and got["abis"] == [] and got["form"] is None


def test_a_verb_with_no_name_is_refused_rather_than_recorded_blank(world):
    repos, source, _ = world
    file = repos / source.repo / "app/src/main/assets/capabilities/actions.json"
    for verbs in ([{"id": "timer.start", "gate": "A0"}], [{"verb": "timer.start"}], ["timer.start"], [{"verb": ""}]):
        file.write_text(json.dumps({"capability": "actions", "verbs": verbs}), encoding="utf-8")
        with pytest.raises(facts.MissingRepo, match="no name or no gate"):
            facts.extract(source, repos)


def test_a_url_in_the_build_file_is_not_mistaken_for_a_comment():
    assert "example.org/help" in facts._strip_comments(KOTLIN_GRADLE)
    assert "versionCode = 999" not in facts._strip_comments(KOTLIN_GRADLE)


def test_a_build_file_with_no_version_is_refused():
    with pytest.raises(facts.MissingRepo, match="version_code"):
        facts.gradle_facts(KOTLIN_GRADLE.replace("versionCode = 7", ""))


def test_nothing_from_the_apps_documents_reaches_a_snapshot(world):
    repos, source, _ = world
    text = facts.dump(facts.extract(source, repos))
    assert "192.168" not in text and "living room" not in text


def test_current_facts_report_no_difference(world):
    repos, source, snapshots = _checked_in(world)
    assert facts.differences(repos, snapshots, (source,)) == []


@pytest.mark.parametrize("path,old,new,field", [
    ("app/build.gradle.kts", "versionCode = 7", "versionCode = 8", "version_code"),
    ("app/build.gradle.kts", 'versionName = "1.2.3"', 'versionName = "1.3.0"', "version_name"),
    ("app/build.gradle.kts", "minSdk = 31", "minSdk = 33", "min_sdk"),
    ("app/src/main/AndroidManifest.xml", "RECORD_AUDIO", "READ_CONTACTS", "permissions"),
    ("app/src/main/assets/capabilities/talk.json", '"version": 2', '"version": 3', "capabilities"),
    ("app/src/main/assets/capabilities/actions.json", "call.place", "door.unlock", "capabilities"),
    ("app/src/main/assets/capabilities/actions.json", '"A2"', '"N"', "capabilities"),
    ("app/src/main/java/org/example/Pairing.java", "/tv/v1/pair/start", "/v1/pair/complete", "pairing"),
])
def test_an_app_that_changed_since_its_snapshot_is_reported_by_name(world, path, old, new, field):
    repos, source, snapshots = _checked_in(world)
    file = repos / source.repo / path
    text = file.read_text(encoding="utf-8")
    assert old in text
    file.write_text(text.replace(old, new), encoding="utf-8")
    lines = facts.differences(repos, snapshots, (source,))
    assert len(lines) == 1 and lines[0].startswith("phone: facts changed") and field in lines[0]


def test_a_missing_repo_or_snapshot_is_a_difference_not_a_pass(world):
    repos, source, snapshots = world
    assert facts.differences(repos, snapshots, (source,)) == ["phone: no checked-in snapshot"]
    gone = facts.AppSource("phone", "not-here", None, source.gradle, source.manifest)
    assert "cannot read the app repo" in facts.differences(repos, snapshots, (gone,))[0]


def test_the_command_line_exits_non_zero_when_the_real_snapshots_would_be_stale(tmp_path, capsys):
    assert facts.main(["--repos", str(tmp_path)]) == 1
    assert "cannot read the app repo" in capsys.readouterr().out


def test_the_checked_in_snapshots_cover_every_app_the_extractor_knows():
    assert {p.stem for p in facts.FACTS_DIR.glob("*.json")} == {a.app for a in facts.APPS}
    for source in facts.APPS:
        snapshot = json.loads((facts.FACTS_DIR / f"{source.app}.json").read_text(encoding="utf-8"))
        assert snapshot["app"] == source.app and snapshot["schema"] == facts.SCHEMA
        assert snapshot["fingerprint"] == facts.fingerprint(snapshot["facts"])


def test_the_checked_in_snapshots_match_the_real_app_repos():
    where = os.environ.get(ENV)
    if not where:
        pytest.skip(f"{ENV} is not set: the app repos are not on this machine")
    assert facts.differences(Path(where)) == []
