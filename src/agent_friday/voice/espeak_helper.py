"""espeak-ng pronunciation helper: a separate program Friday talks to over a pipe.

Run by FILE PATH (``python espeak_helper.py``), never imported by Friday: it
loads espeak-ng through ``phonemizer`` and ``espeakng-loader`` (GPL-3.0,
an optional install) in its own process, so those never enter Friday's.
It imports nothing from Friday either.

Protocol, one JSON object per line:
  start:   {"ready": true} | {"ready": false, "error": "..."}
  request: {"text": "Nguyen", "british": false}
  answer:  {"ps": "<misaki phonemes>", "rating": 2} | {"ps": null}

The phoneme mapping is misaki's own ``EspeakFallback`` (Apache-2.0), used
here, in this process, exactly as Kokoro would use it in-process.
"""
import json
import sys


def _out(obj) -> None:
    # ASCII-escaped JSON: the pipe's console encoding (cp1252 on Windows)
    # cannot carry IPA, and an escaped line reads the same on any encoding.
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        try:
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        except Exception:
            pass
        from misaki.espeak import EspeakFallback
    except BaseException as e:  # noqa: BLE001
        _out({"ready": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
        return 2
    fallbacks = {}
    _out({"ready": True})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            british = bool(req.get("british"))
            fb = fallbacks.get(british)
            if fb is None:
                fb = fallbacks[british] = EspeakFallback(british=british)
            token = type("Token", (), {"text": str(req.get("text") or "")})()
            ps, rating = fb(token)
            _out({"ps": ps, "rating": rating})
        except Exception as e:  # noqa: BLE001
            _out({"ps": None, "error": type(e).__name__})
    return 0


if __name__ == "__main__":
    sys.exit(main())
