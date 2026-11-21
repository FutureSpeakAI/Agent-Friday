"""A worker process for tests/unit/test_accepted_issuers.py: accepts one issuer into 40 lists, as another process would.

Usage: python accepted_issuers_worker.py a|b <folder>"""
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_friday.services import accepted_issuers as acc
from agent_friday.services.issuer_registry import IssuerRef

KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

if __name__ == "__main__":
    who, folder = sys.argv[1], sys.argv[2]
    ref = IssuerRef("test.one", "d" * 64, "TESTP") if who == "a" else IssuerRef("test.two", "c" * 64, "TWOP")
    for n in range(40):
        acc.AcceptedIssuers(f"{folder}/p{n}.json", lambda data: KEY.sign(data).hex(), PUBLIC).accept(ref, acc.Press(), 1)
