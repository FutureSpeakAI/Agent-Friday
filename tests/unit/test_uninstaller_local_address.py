"""The uninstaller undoes Friday's local address: the marked hosts-file block
and the certificate authority Friday asked Windows to trust.

Nothing here changes the system. The PowerShell helpers are exercised on
temporary files only; the elevated and certutil paths are checked by reading
the script, not by running them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WIN = ROOT / "packaging" / "windows"
LIB = WIN / "lib" / "LocalAddress.ps1"
UNINSTALL = WIN / "uninstall.ps1"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")


def _ps(script: str) -> str:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _parse_errors(path: Path) -> str:
    return _ps(
        "$e=$null; $t=$null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$t, [ref]$e); "
        "($e | ForEach-Object { $_.Message }) -join '|'")


def test_the_scripts_parse():
    assert _parse_errors(LIB) == ""
    assert _parse_errors(UNINSTALL) == ""


def test_markers_match_the_app():
    from agent_friday.services import local_address as la
    text = LIB.read_text(encoding="utf-8-sig")
    assert f"'{la.MARK_BEGIN}'" in text and f"'{la.MARK_END}'" in text


def test_only_the_marked_block_is_removed(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_bytes(
        b"# comment\r\n127.0.0.1 other.example\r\n"
        b"# >>> Agent Friday local address >>>\r\n127.0.0.1\tagent.friday\r\n"
        b"::1\t\tagent.friday\r\n# <<< Agent Friday local address <<<\r\n"
        b"10.0.0.1 kept.example\r\n")
    out = _ps(
        f". '{LIB}'; "
        f"$p='{hosts}'; "
        "$before = Test-FridayHostsBlock -HostsPath $p; "
        "$new = Remove-FridayHostsBlockText ([IO.File]::ReadAllText($p)); "
        "[IO.File]::WriteAllText($p, $new); "
        "$after = Test-FridayHostsBlock -HostsPath $p; "
        "\"$before,$after\"")
    assert out == "True,False"
    assert hosts.read_bytes() == (b"# comment\r\n127.0.0.1 other.example\r\n"
                                  b"10.0.0.1 kept.example\r\n")


def _make_ca(path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Agent Friday local address CA test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()


def test_thumbprints_come_from_fridays_own_folder(tmp_path):
    current = _make_ca(tmp_path / "ca.pem")
    old = "AB" * 20
    (tmp_path / "state.json").write_text(json.dumps({
        "ca_sha1": ":".join(current[i:i + 2] for i in range(0, 40, 2)),
        "previous_cas": [{"sha1": ":".join(old[i:i + 2] for i in range(0, 40, 2))}],
    }), encoding="utf-8")
    out = _ps(f". '{LIB}'; (Get-FridayCaThumbprints -StateDir '{tmp_path}') -join ','")
    assert set(out.split(",")) == {current, old}


def test_uninstaller_undoes_the_address_before_it_can_delete_the_record():
    text = UNINSTALL.read_text(encoding="utf-8-sig")
    assert ". (Join-Path $LibDir 'LocalAddress.ps1')" in text
    step = text.index("Undoing Friday's local address")
    data = text.index("Deleting your notes")
    assert step < data, "the thumbprints live in ~/.friday; read them before it can go"
    block = text[step:data]
    assert "Remove-FridayTrustedCertificates" in block
    assert "Invoke-FridayHostsRemovalElevated" in block
    # Unattended: the elevated edit is skipped and reported, the cert attempted.
    unattended = block[block.index("if ($Unattended)"):block.index("} else {")]
    assert "Invoke-FridayHostsRemovalElevated" not in unattended
    assert "Add-InstallWarning" in unattended
    lib = LIB.read_text(encoding="utf-8-sig")
    assert "'-user', '-delstore', 'Root'" in lib
    assert "-Verb RunAs" in lib
