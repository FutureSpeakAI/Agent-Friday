# CodeQL: the residual alerts and what each one is

Status: current for branch `security/codeql-residual`. Line numbers are the
ones CodeQL reported against the pull-request merge commit; the code named in
each row is the statement at or next to that line.

Every alert below is in one of five states:

- **FIXED**: the code changed so the flagged flow no longer carries what the
  rule is about, with a test that fails before the change and passes after.
- **SANITISED**: the flow already passes through a check or a replacement
  that CodeQL does not model; the row names the call and where it is.
- **LINEAR**: a regular expression CodeQL suspects of polynomial backtracking,
  timed on the input the alert describes and found linear.
- **DELIBERATE**: the text is shown on purpose, reduced to a safe form.
- **FALSE_POSITIVE**: the flagged value is not what the rule takes it for.

## How exception text is kept out of responses

A service result has two readers. The model reads it through agent tools and
needs the real error; the browser reads it through a route and must not see
internals. A service writes exception text as `user_errors.exception_text(e)`
(an `ExceptionText`, a `str` subclass), and `routes/_errors.public_result`
replaces every marked value with "<what> (error <id>)" and logs the original
under that id. CodeQL does not model `public_result` as a sanitiser, so every
route that returns a service result through it is reported.

Formatting, slicing or `str()` of a marked value returns a plain `str` and
drops the mark. Most of the real gaps found here were exactly that: a marked
value shortened for a health record, or quoted inside a new sentence. Two
helpers carry the mark across those steps: `user_errors.clip(value, n)` and
`user_errors.keep_mark(parts, text)`. `user_errors.result_text(e)` gives a
service the message of a `UserFacingError` as written and marks anything else.
`paths.contained` and `paths.safe_name` now raise `UserFacingValueError`
(still a `ValueError`), so a route can show "invalid event id" and hide
everything else.

The tests are `tests/api/test_route_errors_hidden_everywhere.py` (24 cases; 21
fail on the code before these changes, the other 3 hold behaviour that must
not change) and the additions to `tests/unit/test_regex_linear_time.py`.

## The table

| n | rule | path:line | state | evidence |
|---|------|-----------|-------|----------|
| 778 | py/stack-trace-exposure | src/agent_friday/routes/voice.py:1881 | FIXED | `voice_setup_status` put `str(_e)` in the deps step; now `exception_text(_e)`. Google's rejection body (marked by `validate_gemini_key`) was quoted inside the key step's sentence, which dropped the mark; the route now passes it through `public_result` first, so the step still says the key was rejected, with an error id. Kokoro readiness details (`kokoro_voice.py`) that reach the GPU and deps steps are marked. |
| 777 | py/stack-trace-exposure | src/agent_friday/routes/voice.py:1761 | SANITISED | `msg` comes from `voice_manifest.plain_language_refusal`: every recognised failure is a fixed sentence, and the fallback returns `ExceptionText` (voice_manifest.py, end of the function), which `public_result` at voice.py:1761 replaces. If that call itself fails, `error_text(e, ...)` at voice.py:1759 is used. The one parsed value shown is a missing module's name from an import error. |
| 776 | py/stack-trace-exposure | src/agent_friday/routes/research.py:160 | FIXED | `web_search._note_backend_health` stored `str(detail)[:160]` in the Brave and Firecrawl health records, dropping the mark, and `search()` appended the setup note to a marked detail with `+`. Both now keep it (`clip`, `keep_mark`), so the health, key-status and canary fields are swapped by `public_result`. |
| 775 | py/stack-trace-exposure | src/agent_friday/routes/platform.py:845 | FIXED | `/api/health/full` built eight blocks as `{"error": str(e)}`; each is now `exception_text(e)`. The local-voice block it includes carried unmarked Kokoro details and a GPU reason that quoted NeMo's marked detail; both are marked now. |
| 774 | py/stack-trace-exposure | src/agent_friday/routes/platform.py:679 | FIXED | Saving a distribution answered `str(e)` for any `ValueError`; it now answers through `api_error`. A name that is not a file name still gets "invalid distribution name" (a `UserFacingValueError` from `paths.safe_name`). |
| 773 | py/stack-trace-exposure | src/agent_friday/routes/platform.py:304 | FIXED | The Ollama provider test put `str(e)[:200]` in `detail`, and the generic probe put `f"{type(e).__name__}: {e}"`; both are `exception_text` now. |
| 772 | py/stack-trace-exposure | src/agent_friday/routes/platform.py:265 | FIXED | Provider health reaches `OllamaManager.probe_generate`, whose error was `"%s: %s" % (type(e).__name__, e)`, and kie.ai's credential check, whose detail was the same shape; both are `ExceptionText` now. |
| 771 | py/stack-trace-exposure | src/agent_friday/routes/phone.py:86 | FIXED | `phone.service.apply_enabled_state` returned the port's `OSError` text unmarked; marked now. A failed text's row in the call log (shown in the phone panel) quoted Twilio's exception; it now carries the error id and the text stays in the local log. |
| 770 | py/stack-trace-exposure | src/agent_friday/routes/phone.py:71 | FIXED | Same two changes: the ingress result and the call-log rows in `service.status()`. |
| 769 | py/stack-trace-exposure | src/agent_friday/routes/intelligence.py:1405 | FIXED | `_vault_policy_status` built its summary with `"...: %s" % exc`; now `exception_text(exc, "...: %s")`. |
| 768 | py/stack-trace-exposure | src/agent_friday/routes/jobs.py:114 | SANITISED | `_public_scan` (routes/jobs.py, just below the route) replaces the scanner's only exception-derived lines, `fetcher error: ...` and `process error: ...` (seed/skills/job_scanner/scanner.py:386 and :430), with the kind and an error id via `log_text`. Every other field of the scan result is a count or a literal. |
| 767 | py/stack-trace-exposure | src/agent_friday/routes/federation.py:267 | FIXED | `web_safety.check_peer_endpoint` put the URL parser's exception text in its reason, which `send_to_peer` quotes as `refused_endpoint: ...`; the reason is a fixed sentence now. Transport failures were already `exception_text` (federation_transport.py:454). |
| 766 | py/stack-trace-exposure | src/agent_friday/routes/federation.py:240 | FIXED | Same change; the per-peer error is `error_text` in the route. |
| 765 | py/stack-trace-exposure | src/agent_friday/routes/creative_pipeline.py:219 | FIXED | Take comparison calls `creative_engine.generate_image`, which returns the ComfyUI, Higgsfield and kie.ai results as they are; their failure reasons and download-failure lists were built from exceptions unmarked (`local_image.py`, `higgsfield_generate.py`, `kie_generate.py`, `creative_store.download_output`). All are marked, and a joined list keeps the mark. |
| 764 | py/stack-trace-exposure | src/agent_friday/routes/creative_pipeline.py:35 | FIXED | `register_pipeline` answered `str(e)`; now `result_text(e)` ("invalid pipeline id" for a bad id, marked text otherwise), and the route passes the result through `public_result`. |
| 763 | py/stack-trace-exposure | src/agent_friday/routes/core_routes.py:516 | FIXED | `/api/health` includes the boot health report, whose ten subsystem checks (`health_check.py`) returned f-strings quoting the exception; the ML preload error (`ml_imports.py`); Layer 3's load error (`sensitivity_classifier.py`); and the inference sweep's probe errors (`machine_probe.py`, `ollama_manager.py`). All are marked now. |
| 762 | py/stack-trace-exposure | src/agent_friday/routes/creations.py:752 | FIXED | Video goes through the same Higgsfield and kie.ai generators and the same download store as 765; those are marked now. The Gemini video errors were already marked. |
| 761 | py/stack-trace-exposure | src/agent_friday/routes/creations.py:434 | FIXED | The music demo message quoted why cloud music is unavailable, which is marked when it is an import error; the sentence now keeps the mark (`keep_mark`). |
| 760 | py/stack-trace-exposure | src/agent_friday/routes/creations.py:409 | FIXED | As 765 for the image path. A cancelled ComfyUI job and an egress-gate refusal still show their messages: both are sentences Friday writes (`local_image.Cancelled`, `higgsfield_generate.EgressBlocked`), not exception text from a library. |
| 759 | py/stack-trace-exposure | src/agent_friday/routes/context.py:60 | FIXED | Two `{"error": str(e)}` blocks are now `exception_text(e)`. |
| 758 | py/stack-trace-exposure | src/agent_friday/routes/content_pipeline.py:445 | FIXED | `publisher.kick` returned `{"reason": str(e)}`; now `exception_text(e)`. The content store's own errors were already marked. |
| 757 | py/stack-trace-exposure | src/agent_friday/routes/content_pipeline.py:424 | FIXED | Same change as 758. |
| 756 | py/stack-trace-exposure | src/agent_friday/routes/chat.py:2640 | FIXED | The fallback chain came from `f"{leg}: {e}"` in `model_router` and `agent`, then `attribution.note_fallback` stored `str(step)[:300]`. The legs are `ExceptionText`, `note_fallback` keeps the mark, and the route passes the chain through `public_result` when it reads it, so the persisted message is clean too. A tool that raises returns `ExceptionText("Tool error (...)")` from `_execute_tool`, and the tool trace keeps the mark when it shortens the result. |
| 755 | py/stack-trace-exposure | src/agent_friday/routes/chat.py:2143 | FIXED | As 756, plus: the local-fallback notice's `why` was `str(_ole)[:200]` (now `exception_text`); the local-vision refusal quoted the local seat's marked reason inside the event text (now passed through `public_result` first, while the model and the egress ledger keep the words); the streaming wrapper sent `str(e)` as its error event (now `error_text`). |
| 754 | py/stack-trace-exposure | src/agent_friday/routes/calendar.py:329 | FIXED | Answered `str(e)` for any `ValueError`; now `api_error(e, ..., 400)`. An event id that is not a file name still gets "invalid event id". |
| 625 | py/stack-trace-exposure | src/agent_friday/routes/goals.py:376 | DELIBERATE | The grant list shows the action gate's explanation for why a tool counts as outward, reduced by `user_errors.message_only(why)` to one line with no traceback lines and no file paths, capped at 200 characters. The explanation is written for the owner. |
| 784 | py/polynomial-redos | src/agent_friday/services/workflow_overview.py:263 | LINEAR | Through `_strip`, prefixes "", "a", "a,", repeated " ", "\t", " ," and ten failing suffixes: worst 0.014 s at 50,000 characters, 0.024 s at 100,000. |
| 783 | py/polynomial-redos | tests/unit/test_compaction_mechanics.py:155 | LINEAR | Test code with a fixed input. On repeated "0", "0:", "0 " with failing suffixes: worst 0.004 s at 50,000, 0.007 s at 100,000. |
| 782 | py/polynomial-redos | src/agent_friday/services/style_guard.py:88 | LINEAR | Through `style_guard.sanitize` (the whole call, not only the match), repeated " ", "\t", " -", "1." after "", "-", "1.", " -": worst 0.054 s at 50,000, 0.109 s at 100,000. The pattern is anchored and `.*` runs to the end of a line that has no newline in it. |
| 781 | py/polynomial-redos | src/agent_friday/services/agent.py:2683 | FIXED | The trailing "tab/page/..." strip is `_drop_trailing(..., once=True)`, an index scan. It agrees with the old pattern on 200,000 generated inputs. |
| 780 | py/polynomial-redos | src/agent_friday/services/agent.py:2372 | FIXED | The trailing "folder/dir/file" strip in `_resolve_open_target` is `_drop_trailing(..., once=True)`. The old pattern timed linear (0.002 s at 50,000); it shares the helper that 573 needed. |
| 779 | py/polynomial-redos | src/agent_friday/governance/behavioral_monitor.py:259 | LINEAR | Through `extract_remit`, repeated "-", ".", "a.", "/", "\\": worst 0.006 s at 50,000, 0.013 s at 100,000. The lookbehind starts a match only at the start of a run. |
| 573 | py/polynomial-redos | src/agent_friday/services/agent.py:2672 | FIXED | Real: the loop stripped one trailing "please" per pass and rescanned the whole string each time. "news" plus 7,142 " please" took 6.4 s; `_drop_trailing` moves an index and takes 0.015 s. Test added with that input and with " right now" repeated. |
| 259 | py/polynomial-redos | src/agent_friday/services/message_triage.py:409 | LINEAR | `_EMAIL_RE.search`, repeated "'", "a", ".", "+", "'@" after "", "a", "a@": worst 0.004 s at 50,000, 0.007 s at 100,000. |
| 258 | py/polynomial-redos | src/agent_friday/services/message_triage.py:375 | LINEAR | The same pattern as 259, same timings. |
| 255 | py/polynomial-redos | src/agent_friday/services/agent.py:2698 | LINEAR | `_OPEN_VERB_RE` through `_maybe_handle_navigate_intent`, "open ", "open a", "friday ", "open up " then repeated " ", "  ", "\t", "a\t", "!\t", "friday ": worst 0.018 s at 50,000, 0.030 s at 100,000. |
| 253 | py/polynomial-redos | src/agent_friday/services/agent.py:2559 | LINEAR | The same pattern matched directly (the open intent has side effects): worst 0.027 s at 50,000, 0.031 s at 100,000. |
| 753 | py/path-injection | src/agent_friday/services/seed_images.py:86 | FIXED | `resolve` ran `realpath` and `is_file` on the argument before any check, and on Windows a `\\host\share` path makes that stat connect to the host and offer the user's credentials. A UNC or device path is refused before the filesystem is touched (`seed_images.is_network_path`). A local path is still only read after `check_running_call`. |
| 752 | py/path-injection | src/agent_friday/services/creative_engine.py:1187 | FIXED | Same `resolve`, so a network path never reaches `is_file`. The bytes are read only after `seed_images.check_running_call` (creative_engine.py:1189) and returned only when they are an image (`_image_mime`). |
| 751 | py/path-injection | src/agent_friday/routes/creations.py:331 | FALSE_POSITIVE | `/api/computer/open` is login-required and is the owner's own click; the path is only tested for existence, then judged by `open_safety.judge` (creations.py:335), and anything that could run raises an approval card before it opens. |
| 750 | py/full-ssrf | src/agent_friday/services/web_safety.py:190 | SANITISED | `safe_get` calls `assert_safe` (`check_url`) before the first request (web_safety.py:188) and `check_url` on every redirect target before following it (:198), with redirects off in `requests`. `check_url` refuses non-http schemes, embedded credentials and any host that resolves to a loopback, private, link-local or reserved address. The DNS-rebinding window between that check and the request is stated as a known limit in the module docstring. |
| 785 | py/clear-text-logging-sensitive-data | src/agent_friday/user_errors.py:137 | FIXED | `log_text` writes exception text to the local log, and an exception can quote a request URL with `?key=`. It now passes the text through `redact_credentials`: every shape in `services/secret_shapes.py` and every secret-looking query parameter is replaced before the line is written. |
| 787 | py/weak-sensitive-data-hashing | src/agent_friday/services/voice_engine.py:1178 | FALSE_POSITIVE | Not password storage. The value is an HMAC-SHA256 of a high-entropy API key under a 32-byte random per-process secret (voice_engine.py:1160), used only as an in-memory cache index; nothing is stored or compared across processes. |
| 786 | py/weak-sensitive-data-hashing | src/agent_friday/services/local_ca.py:267 | FALSE_POSITIVE | The input is a public root certificate from the Windows store, and SHA-1 is the thumbprint format Windows shows for it, used only to tell whether Friday's own CA is installed. `usedforsecurity=False` is set. |
| 789 | py/incomplete-url-substring-sanitization | tests/unit/test_open_path_allow_list.py:296 | FIXED | The assertion compares the whole source label: `== "a web page on shop.example.com"`. |
| 788 | py/incomplete-url-substring-sanitization | tests/unit/test_google_oauth_client.py:196 | FIXED | The set of parsed hostnames is checked by set intersection instead of `in`, which CodeQL read as a substring test. |

## Counts

| state | alerts |
|-------|--------|
| FIXED | 31 |
| SANITISED | 3 |
| LINEAR | 8 |
| DELIBERATE | 1 |
| FALSE_POSITIVE | 3 |
| total | 46 |

## How the timings were taken

Each pattern was run through its real call path where that has no side effect
(the direct match otherwise), on every combination of the prefixes and repeated
units named in its row with the suffixes "", "x", "!", "\n", "\nx", "@", ".",
"a!", "\t!" and "?", each with and without a leading "x", at 50,000 and 100,000
characters. A backtracking pattern shows up as a time that grows about four
times when the input doubles; every LINEAR row roughly doubles.
