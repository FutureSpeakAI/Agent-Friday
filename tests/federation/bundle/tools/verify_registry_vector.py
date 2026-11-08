"""Recompute the primitive registry vector. Uses only Python's standard library.

The fixture payload is ASCII and has only nonnegative integers. JSON encoding
below agrees with JCS for this fixture; it is not a general JCS implementation.
This test does not emulate consensus or verify an engine's state proof.
"""
import hashlib
import json
from pathlib import Path

def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def digest(data):
    return hashlib.sha256(data).digest()

def main():
    root = Path(__file__).resolve().parents[1]
    v = json.loads((root / "fixtures" / "registry-v1.json").read_text())
    m = v["manifest_body"]
    assert digest(bytes.fromhex(v["payload"])).hex() == m["payload_sha256"]
    net, reg = bytes.fromhex(v["network"]), bytes.fromhex(v["registry"])
    tok = digest(b"asimov-token/1\x00" + net + reg + bytes.fromhex(m["publisher"])
                 + bytes.fromhex(m["asset"]) + m["revision"].to_bytes(8, "big"))
    com = digest(b"asimov-commit/1\x00" + bytes.fromhex(v["salt"]) + digest(canonical(m)))
    assert tok.hex() == v["mint_body"]["token"] and com.hex() == v["mint_body"]["commitment"]
    assert v["qualified_id"] == "asimov:" + ":".join([net.hex(), reg.hex(), tok.hex()])
    assert (b"asimov-mint/1\x00" + canonical(v["mint_body"])).hex() == v["mint_signature_message_hex"]
    leaves = [digest(b"\x00" + canonical(e)) for e in v["entries"]]
    inner = digest(b"\x01" + leaves[0] + leaves[1])
    assert digest(b"\x01" + inner + leaves[2]).hex() == v["root"]
    i = v["leaf_index"]
    assert v["entries"][i] == {"commitment":com.hex(), "token":tok.hex()}
    assert i == 1 and v["path"] == [leaves[0].hex(),leaves[2].hex()]
    assert digest(b"\x01" + digest(b"\x01" + bytes.fromhex(v["path"][0]) + leaves[i])
                  + bytes.fromhex(v["path"][1])).hex() == v["root"]
    print("Registry primitive vector OK. Consensus, identity and policy checks are separate.")
if __name__ == "__main__":
    main()
