#!/usr/bin/env python3
"""Live smoke test of the key gateway (K2): one cheap call per provider that already has a key on this PC.

What it proves, which no unit test can: the gateway's loopback hop reaches the real provider over real TLS from this
machine, the provider accepts the header the gateway writes, and a streamed chat answer arrives as events.
It never prints a key: callers hold only the stand-in, and the output is a status code and a count.

Run on the PC where the keys are saved (Settings -> Connections):

    py -3.14 scripts/k2_gateway_smoke.py            # models/credit/search calls only (free or near free)
    py -3.14 scripts/k2_gateway_smoke.py --chat     # also one 1-token streamed chat call per configured chat key

Exit 0 when every configured provider passed; 1 when one failed; providers with no key are SKIP, not failures.
A SKIP for a provider you did save a key for usually means the credential store is locked or the key sits in a place
the gateway does not read; say so in the LEDGER. Not covered: Gemini (its upload and Live voice are not on the gateway).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def report(name: str, outcome: str, detail: str = "") -> bool:
    print("%-28s %-5s %s" % (name, outcome, detail))
    return outcome != "FAIL"


def judge(name: str, response, key_gateway) -> bool:
    if key_gateway.no_key(response):
        return report(name, "SKIP", "the gateway found no key")
    if response.status_code == 200:
        return report(name, "PASS", "HTTP 200")
    return report(name, "FAIL", "HTTP %d (401/403 usually means the saved key is wrong or the header is not accepted)"
                  % response.status_code)


def fixed_rows(requests, key_gateway) -> list:
    results = []
    calls = {"brave": ("/res/v1/web/search?q=test&count=1", {}),
             "firecrawl": ("/v1/team/credit-usage", {}),
             "elevenlabs": ("/v1/models", {})}
    for name, (path, headers) in calls.items():
        if not key_gateway.available(name):
            results.append(report("gateway " + name, "SKIP", "no key on this PC"))
            continue
        try:
            response = requests.get(key_gateway.url(name) + path, headers=headers, timeout=30)
        except Exception as error:   # a network or TLS failure is the thing this test exists to find
            results.append(report("gateway " + name, "FAIL", type(error).__name__))
            continue
        results.append(judge("gateway " + name, response, key_gateway))
    return results


def chat_rows(requests, key_gateway, with_chat: bool) -> list:
    from agent_friday.services import model_router
    results = []
    base = (model_router._load_settings().get("model_routing") or {}).get("openai_base_url") or "https://api.openai.com/v1"
    if not model_router._legacy_openai_key():
        return [report("gateway chat (legacy slot)", "SKIP", "no OpenAI/OpenRouter key on this PC")]
    descriptor = {"name": "legacy-openrouter" if "openrouter.ai" in base else "legacy-openai",
                  "base_url": base, "auth": {"type": "env_var", "key": "OPENAI_API_KEY"}}
    try:
        gateway_base = key_gateway.descriptor_url(descriptor, key_source=model_router._legacy_openai_key)
    except Exception as error:
        return [report("gateway chat (legacy slot)", "FAIL", "the gateway could not start: " + type(error).__name__)]
    if not gateway_base:
        return [report("gateway chat (legacy slot)", "SKIP", "base URL is not a public https host (called directly)")]
    try:
        response = requests.get(gateway_base + "/models", headers={"Authorization": "Bearer " + key_gateway.DUMMY_KEY},
                                timeout=30)
    except Exception as error:
        return [report("gateway models (" + descriptor["name"] + ")", "FAIL", type(error).__name__)]
    results.append(judge("gateway models (%s)" % descriptor["name"], response, key_gateway))
    if not with_chat or not results[-1] or response.status_code != 200:
        return results
    started = time.time()
    try:
        answer = model_router._call_openai([{"role": "user", "content": "Reply with the single word: ok"}],
                                           tools=None, max_tokens=5, stream=True)
        text = answer[0] if isinstance(answer, tuple) else answer
        results.append(report("chat through gateway (stream)", "PASS" if str(text).strip() else "FAIL",
                              "%.1fs, %d characters" % (time.time() - started, len(str(text)))))
    except Exception as error:
        results.append(report("chat through gateway (stream)", "FAIL", type(error).__name__))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--chat", action="store_true", help="also make one 1-token streamed chat call")
    arguments = parser.parse_args()
    import requests
    from agent_friday.services import key_gateway
    results = fixed_rows(requests, key_gateway) + chat_rows(requests, key_gateway, arguments.chat)
    key_gateway.stop_all()
    print("\n%s" % ("ALL CONFIGURED PROVIDERS PASSED" if all(results) else "AT LEAST ONE PROVIDER FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
