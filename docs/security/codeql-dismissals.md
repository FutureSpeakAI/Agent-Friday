# CodeQL alert dispositions

**Status:** the record behind every CodeQL alert that is dismissed rather than
closed by a code change, for the backlog cleared on 26 September 2026 (706 open
alerts). An alert is dismissed on GitHub only if it is listed here with a
disposition other than FIX, and the dismissal comment links to this file.

## How to read this file
- **FIX**: the code changed. The alert closes when CodeQL no longer finds the flow. If it stays open because CodeQL does not model the guard that now runs (for example `web_safety.safe_get`, `paths.contained` or `routes/_errors.public_result`), it is dismissed as a false positive, and the reason names the guard.
- **FALSE_POSITIVE**: the flagged flow cannot carry attacker-controlled or sensitive data, or the behaviour is intended and safe. The reason says why, in terms a reviewer can check against the code.
- **TEST_ONLY**: test code, not shipped behaviour. Most were fixed anyway; the rest are dismissed as "used in tests".
- Line numbers are those CodeQL reported on main at 5a8cfa43; later commits shift some lines.

## Deliberate: errors that reach the user or the model on purpose

| n | rule | path | disposition | reason |
|---|---|---|---|---|
| 625 | py/stack-trace-exposure | routes/goals.py (Grants screen) | DELIBERATE | The governance gate's explanation of why a tool is outward is meant for the owner. Only `user_errors.message_only(why)` travels: one line, no traceback, file paths replaced, at most 200 characters. Tested in `tests/api/test_deliberate_error_messages.py`. |
| 527 | py/stack-trace-exposure | routes/voice_context.py (Start My Day) | DELIBERATE | The model must be able to say that the news or calendar failed. The prompt carries only `message_only(e)`. Same test file. |

Stack-trace alerts are otherwise fixed, never dismissed as "won't fix". Any that stay open after the fixes are listed with the sanitising call in [codeql-residual.md](codeql-residual.md).

## Sensitive data (clear-text logging and storage, weak hashing)

| n | rule | path:line | disposition | reason |
|---|---|---|---|---|
| 746 | clear-text-logging | services/nemo_voice.py:578 | FALSE_POSITIVE | Logs NeMo model name, a cached bool and the model directory; no secret. |
| 745 | clear-text-storage | services/voice_installer.py:198 | FALSE_POSITIVE | Install log holds fixed allowlisted pip args, pip output and model-download progress; no credential enters it. |
| 744 | clear-text-logging | services/model_router.py:1749 | FALSE_POSITIVE | Logs model id and character/tool counts (debug). |
| 743 | clear-text-storage | services/model_router.py:658 | FALSE_POSITIVE | payload_trace row is request shape: model, counts, and the payload's top-level key names; no content, no auth headers. |
| 733 | clear-text-logging | services/tool_budget.py:716 | FALSE_POSITIVE | model id plus a sentence of token counts; "token" here is a size estimate from `_tokens()`. |
| 732 | clear-text-logging | services/tool_budget.py:833 | FALSE_POSITIVE | model id and tool names. |
| 731 | clear-text-logging | services/tool_budget.py:739 | FALSE_POSITIVE | model id and token-count estimates. |
| 730 | clear-text-logging | services/tool_budget.py:829 | FALSE_POSITIVE | model id, counts, dropped tool names. |
| 729 | clear-text-logging | services/tool_budget.py:737 | FALSE_POSITIVE | model id and token-count estimates. |
| 728 | clear-text-logging | services/tool_budget.py:836 | FALSE_POSITIVE | model id and token-count estimates (print). |
| 727 | clear-text-logging | services/tool_budget.py:716 | FALSE_POSITIVE | duplicate location of 733. |
| 726 | clear-text-logging | services/tool_budget.py:795 | FALSE_POSITIVE | model id, fixed floor-tool names, counts. |
| 725 | clear-text-logging | services/news_engine.py:1752 | FALSE_POSITIVE | Front-page degradation: slot, reason, model id, and an editor reply snippet about public news. |
| 724 | clear-text-logging | services/model_router.py:1491 | FALSE_POSITIVE | provider name, model id, tool count. |
| 723 | clear-text-logging | services/model_router.py:1485 | FALSE_POSITIVE | provider, model id, count, schema-conversion exception. |
| 722 | clear-text-logging | services/machine_probe.py:130 | FALSE_POSITIVE | `key` is a stale-while-revalidate cache name, logged with the probe exception. |
| 721 | clear-text-logging | services/local_only_guard.py:145 | FALSE_POSITIVE | Refusal message names the provider and model id only. |
| 720 | clear-text-logging | routes/chat.py:466 | FALSE_POSITIVE | Seat notice text names the chosen and actual model. |
| 719 | clear-text-storage | services/model_router.py:1793 | FALSE_POSITIVE | Opt-in prompt-cache forensics: written only when the owner creates runtime/diag/payload-dump; documented as off by default and as containing the prompt. The body carries no auth headers. |
| 712 | clear-text-logging | services/local_call.py:320 | FALSE_POSITIVE | Local model id and a requests exception for a loopback seat. |
| 711 | clear-text-logging | services/local_call.py:315 | FALSE_POSITIVE | Status, local model id, local server's error body. |
| 710 | clear-text-logging | services/local_call.py:288 | FALSE_POSITIVE | Local model id and exception. |
| 709 | clear-text-logging | services/local_call.py:283 | FALSE_POSITIVE | Status, local model id, local error body. |
| 708 | clear-text-logging | services/local_call.py:148 | FALSE_POSITIVE | Local model id and loopback URL; the "key" source is a seat-name dict key. |
| 707 | clear-text-storage | services/soul.py:251 | FALSE_POSITIVE | Writes SOUL.md, the user-editable personality file. |
| 706 | clear-text-storage | services/soul.py:246 | FALSE_POSITIVE | Same, temp-file write. |
| 705 | clear-text-storage | notifications_engine.py:65 | FALSE_POSITIVE | Notification queue, ordinary local app state. |
| 651 | clear-text-storage | phone/service.py:86 | FALSE_POSITIVE | Phone state: owner cell (already in phone config), salted code hashes, timestamps; no credential. |
| 635 | weak-sensitive-data-hashing | services/local_ca.py:267 | FALSE_POSITIVE (marked) | SHA-1 is the Windows certificate thumbprint of public root certs; now `usedforsecurity=False`. |
| 587 | clear-text-storage | services/decisions.py:172 | FALSE_POSITIVE | Decision record stores PII-scrubbed, clipped state by design. |
| 564 | clear-text-storage | services/local_video.py:729 | FALSE_POSITIVE | Creations manifest: filename, path, prompt of the user's own generation. |
| 563 | clear-text-storage | services/creative_store.py:228 | FALSE_POSITIVE | Same manifest for downloaded creations. |
| 562 | clear-text-storage | services/creative_store.py:205 | FALSE_POSITIVE | Metadata sidecar for a creation. |
| 561 | clear-text-storage | services/creative_engine.py:461 | FALSE_POSITIVE | Writes the generated media bytes themselves. |
| 560 | clear-text-storage | services/creative_engine.py:441 | FALSE_POSITIVE | Creation metadata sidecar. |
| 233 | weak-sensitive-data-hashing | services/voice_engine.py:1174 | FIX | In-memory cache id was an unkeyed SHA-256 of the Gemini key; now HMAC-SHA256 under a per-process random secret. |
| 18 | clear-text-logging | services/platforms/reddit.py:124 | FALSE_POSITIVE | URL is a fixed API endpoint (named TOKEN_URL etc.); tokens travel in headers/body, never the URL. |
| 17 | clear-text-logging | services/platforms/mastodon.py:116 | FALSE_POSITIVE | Instance base + API path; no token in any URL. |
| 16 | clear-text-logging | services/egress_gate.py:558 | FIX | `reason` could carry never-send hits (watchlist entries / deny-marked paragraphs) via ScrubVerdict.hits; hits are now labels. |
| 15 | clear-text-logging | services/egress_gate.py:558 | FIX | Same flow as 16 (fixed in judgment_gate.verify_outgoing). |
| 14 | clear-text-logging | services/platforms/bluesky.py:138 | FALSE_POSITIVE | PDS base + XRPC path with handle/uri query; access token is a header. |
| 12 | clear-text-storage | tests/security/test_kg_egress_adversarial.py:70 | TEST_ONLY | Synthetic canary SSN/bank/health strings planted in the isolated test home. |
| 11 | clear-text-storage | services/retrieval_ledger.py:121 | FALSE_POSITIVE | Row holds section name, tier, char count, flags; never section text. |
| 10 | clear-text-storage | mcp_oauth.py:264 | FIX | ImportError fallback wrote OAuth tokens as plain JSON; removed (write via credential_store or raise). |
| 9 | clear-text-storage | services/judgment_gate.py:521 | FALSE_POSITIVE | Overturn ledger stores span hash/length, verdict, reason, provider; span text is never written (see owner note on `reason`). |
| 8 | clear-text-storage | services/egress_gate.py:565 | FIX | Same entry as 15/16; the never-send text no longer reaches `reason`. |
| 7 | clear-text-storage | services/credential_store.py:276 | FALSE_POSITIVE | Writes the output of protect() (keystore/vault/DPAPI ciphertext). The documented, warned plaintext fallback exists only when all three are unavailable and not in OS mode (owner note). Not modified. |

## Network (SSRF, URL checks, insecure protocol, redirects)

| n | rule | path:line | disposition | reason |
|---|---|---|---|---|
| 237 | py/full-ssrf | src/agent_friday/services/news_engine.py:3110 | FIX | Deep-dive article URL comes from the page, feeds, or the voice model; now fetched through web_safety.safe_get (check_url on the URL and every redirect hop, redirects followed manually). |
| 236 | py/full-ssrf | src/agent_friday/services/model_discovery.py:317 | FALSE_POSITIVE | The route supplies only a provider NAME; base_url and endpoint come from the owner-configured provider registry (often a local model server, which the SSRF guard would wrongly refuse). |
| 235 | py/full-ssrf | src/agent_friday/services/federation_transport.py:433 | FIX | Peer endpoints (owner-supplied or from a peer's card) are LAN-legitimate, so check_url does not fit; web_safety.check_peer_endpoint now refuses non-http(s) schemes (urllib would open file:// and ftp://), credentials in the netloc, and a missing host. |
| 234 | py/full-ssrf | src/agent_friday/services/compute_client.py:193 | FIX | Same check_peer_endpoint rule in request_job (before anything is recorded or spent), await_result and find_providers; /api/compute/send answers 400. |
| 713 | py/incomplete-url-substring-sanitization | src/agent_friday/services/relationship_memory.py:421 | FIX | An email address, not a URL; now compares the mail domain exactly (existing _domain helper) instead of an endswith on the whole address. |
| 244 | py/incomplete-url-substring-sanitization | src/agent_friday/services/web_search.py:451 | FIX | DDG redirector unwrapping now uses web_safety.hostname_matches on the parsed hostname. |
| 238 | py/incomplete-url-substring-sanitization | src/agent_friday/services/news_engine.py:491 | FIX | Google News snippet rule now uses web_safety.url_host_matches. |
| 2 | js/client-side-unvalidated-url-redirection | index.html:6313 | FIX | open_url and open_last_source now go through fridayHttpUrl (http/https only); javascript:/data:/file: are not opened. |
| 1 | js/client-side-unvalidated-url-redirection | ui_parts/app.html:435 | FIX | Identical change in the mirror. |
| 637 | py/insecure-protocol | tests/unit/test_local_proxy.py:87 | TEST_ONLY (fixed) | Test client context now sets minimum_version = TLSv1_2. |
| 636 | py/insecure-protocol | tests/unit/test_local_ca.py:122 | TEST_ONLY (fixed) | Same. |
| 604 | py/incomplete-url-substring-sanitization | tests/unit/test_brutalist_scraper.py:77 | TEST_ONLY (fixed) | Now asserts source == "example-tech.com". |
| 242 | py/incomplete-url-substring-sanitization | tests/unit/test_response_provenance.py:22 | TEST_ONLY (fixed) | Now asserts the returned set equals {"https://reddit.com"}. |
| 239 | py/incomplete-url-substring-sanitization | tests/unit/test_google_oauth_client.py:195 | TEST_ONLY (fixed) | Now compares parsed hostnames of the step URLs. |
| 714 | py/incomplete-url-substring-sanitization | tests/api/test_page_load_contacts_no_cdn.py:50 | TEST_ONLY | Asserts whether served HTML mentions the Google Fonts host; a text assertion, not a sanitizer. |
| 591 | py/incomplete-url-substring-sanitization | tests/unit/test_search_row_shape.py:107 | TEST_ONLY | Asserts the rendered tool output contains the URL; text assertion. |
| 590 | py/incomplete-url-substring-sanitization | tests/unit/test_search_row_shape.py:90 | TEST_ONLY | Same. |
| 243 | py/incomplete-url-substring-sanitization | tests/unit/test_source_trust_graph.py:293 | TEST_ONLY | Dict-key membership (`in data["sources"]`), not a substring test. |
| 241 | py/incomplete-url-substring-sanitization | tests/api/test_news_feed_routes.py:559 | TEST_ONLY | List membership in the boosted list. |
| 240 | py/incomplete-url-substring-sanitization | tests/api/test_news_feed_routes.py:517 | TEST_ONLY | List membership in the banned list. |

## Path injection

| n | rule | path:line | disposition | reason |
|---|---|---|---|---|
| 25 | py/path-injection | core/__init__.py:3261 | FALSE_POSITIVE | queue id reduced by re.sub to [0-9a-zA-Z_-] |
| 26 | py/path-injection | core/__init__.py:3287 | FALSE_POSITIVE | queue id reduced by re.sub to [0-9a-zA-Z_-] |
| 27 | py/path-injection | core/__init__.py:3288 | FALSE_POSITIVE | queue id reduced by re.sub to [0-9a-zA-Z_-] |
| 28 | py/path-injection | services/agent.py:2386 | FALSE_POSITIVE | intended: _resolve_open_target opens/reveals a path the owner named (open_path); reads nothing (see owner note) |
| 29 | py/path-injection | services/agent.py:4358 | FALSE_POSITIVE | _chain_slug reduces the name to [a-z0-9_-] |
| 32 | py/path-injection | services/agent.py:9233 | FALSE_POSITIVE | _mcp_vault_conflict only resolves the string to compare it with vault dirs; no file is opened |
| 33 | py/path-injection | services/code_engine.py:297 | FALSE_POSITIVE | already contained: _repo_path -> _safe_project_path (realpath + startswith(~/Projects)) |
| 34 | py/path-injection | services/code_engine.py:299 | FALSE_POSITIVE | already contained: _repo_path -> _safe_project_path (realpath + startswith(~/Projects)) |
| 35 | py/path-injection | services/code_engine.py:299 | FALSE_POSITIVE | already contained: _repo_path -> _safe_project_path (realpath + startswith(~/Projects)) |
| 36 | py/path-injection | services/code_engine.py:400 | FALSE_POSITIVE | already contained: _repo_path -> _safe_project_path (realpath + startswith(~/Projects)) |
| 37 | py/path-injection | routes/code.py:464 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 38 | py/path-injection | routes/code.py:468 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 39 | py/path-injection | routes/code.py:498 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 40 | py/path-injection | routes/code.py:501 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 41 | py/path-injection | routes/code.py:504 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 42 | py/path-injection | routes/code.py:544 | FALSE_POSITIVE | rel comes from _repo_tree's own listing of the contained repo |
| 43 | py/path-injection | routes/code.py:545 | FALSE_POSITIVE | rel comes from _repo_tree's own listing of the contained repo |
| 44 | py/path-injection | routes/code.py:613 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 45 | py/path-injection | routes/code.py:616 | FALSE_POSITIVE | already contained: _safe_project_path is realpath + startswith(~/Projects); now returns the root itself on equality so the guard reads |
| 46 | py/path-injection | routes/code.py:675 | FALSE_POSITIVE | plan id reduced by re.sub to [\w-]; now also joined with contained |
| 47 | py/path-injection | routes/code.py:678 | FALSE_POSITIVE | plan id reduced by re.sub to [\w-]; now also joined with contained |
| 48 | py/path-injection | routes/code.py:689 | FALSE_POSITIVE | plan id reduced by re.sub to [\w-]; now also joined with contained |
| 49 | py/path-injection | routes/code.py:692 | FALSE_POSITIVE | plan id reduced by re.sub to [\w-]; now also joined with contained |
| 50 | py/path-injection | routes/code.py:772 | FALSE_POSITIVE | plan id reduced by re.sub to [\w-]; now also joined with contained |
| 51 | py/path-injection | routes/contacts.py:403 | FIX | research note name: safe_name + contained; POST name '../x' wrote a stub outside the folder before |
| 52 | py/path-injection | routes/contacts.py:403 | FIX | research note name: safe_name + contained; POST name '../x' wrote a stub outside the folder before |
| 53 | py/path-injection | routes/contacts.py:418 | FIX | research note name: safe_name + contained; POST name '../x' wrote a stub outside the folder before |
| 54 | py/path-injection | routes/contacts.py:419 | FIX | research note name: safe_name + contained; POST name '../x' wrote a stub outside the folder before |
| 55 | py/path-injection | services/content_composer.py:606 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 56 | py/path-injection | services/content_composer.py:608 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 57 | py/path-injection | services/content_composer.py:611 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 58 | py/path-injection | services/content_composer.py:627 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 59 | py/path-injection | services/content_composer.py:628 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 60 | py/path-injection | services/content_composer.py:631 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 61 | py/path-injection | services/content_composer.py:632 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 62 | py/path-injection | services/content_composer.py:634 | FALSE_POSITIVE | platform must be a member of _store.PLATFORMS before the join |
| 63 | py/path-injection | routes/control.py:168 | FALSE_POSITIVE | intended: the owner's consent dialog scans a file they chose (login_required, read-only classification) |
| 64 | py/path-injection | services/conversations.py:70 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 65 | py/path-injection | services/conversations.py:72 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 66 | py/path-injection | services/conversations.py:74 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 67 | py/path-injection | services/conversations.py:78 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 68 | py/path-injection | services/conversations.py:78 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 69 | py/path-injection | services/conversations.py:116 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 70 | py/path-injection | services/conversations.py:119 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 71 | py/path-injection | services/conversations.py:139 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 72 | py/path-injection | services/conversations.py:140 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 73 | py/path-injection | services/conversations.py:265 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 74 | py/path-injection | services/conversations.py:282 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 75 | py/path-injection | services/conversations.py:286 | FIX | conversation id: safe_name + contained; GET /api/conversations/..%5Cx read another folder's conversation.json before |
| 76 | py/path-injection | routes/creations.py:140 | FIX | framed viewer name is safe_name + contained in CREATIONS_DIR |
| 77 | py/path-injection | routes/creations.py:140 | FIX | framed viewer name is safe_name + contained in CREATIONS_DIR |
| 78 | py/path-injection | routes/creations.py:161 | FIX | framed viewer name is safe_name + contained in CREATIONS_DIR |
| 79 | py/path-injection | routes/creations.py:282 | FALSE_POSITIVE | date must re.fullmatch \d{4}-\d{2}-\d{2} before _daily_creation_path |
| 80 | py/path-injection | routes/creations.py:285 | FALSE_POSITIVE | date must re.fullmatch \d{4}-\d{2}-\d{2} before _daily_creation_path |
| 81 | py/path-injection | routes/creations.py:306 | FALSE_POSITIVE | intended: /api/computer/open (login_required) reveals an owner-named path; opens only (see owner note on startfile) |
| 82 | py/path-injection | routes/creations.py:554 | FIX | _creation_file: last component, safe_name, contained, must be a file ('..' named the parent dir before) |
| 83 | py/path-injection | routes/creations.py:574 | FIX | _creation_file: last component, safe_name, contained, must be a file |
| 84 | py/path-injection | services/creative_memory.py:76 | FIX | project id: safe_name + contained; DELETE /api/creative/projects/..%5Cx rmtree'd a sibling folder before |
| 85 | py/path-injection | services/creative_memory.py:77 | FIX | project id: safe_name + contained; DELETE /api/creative/projects/..%5Cx rmtree'd a sibling folder before |
| 86 | py/path-injection | services/creative_memory.py:84 | FIX | project id: safe_name + contained; DELETE /api/creative/projects/..%5Cx rmtree'd a sibling folder before |
| 87 | py/path-injection | services/creative_memory.py:85 | FIX | project id: safe_name + contained; DELETE /api/creative/projects/..%5Cx rmtree'd a sibling folder before |
| 88 | py/path-injection | services/creative_memory.py:180 | FIX | project id: safe_name + contained; DELETE /api/creative/projects/..%5Cx rmtree'd a sibling folder before |
| 89 | py/path-injection | services/creative_memory.py:184 | FIX | project id: safe_name + contained; DELETE /api/creative/projects/..%5Cx rmtree'd a sibling folder before |
| 90 | py/path-injection | services/creative_pipeline.py:357 | FIX | pipeline and run ids: safe_name + contained; register_pipeline wrote a definition anywhere before |
| 91 | py/path-injection | services/creative_pipeline.py:358 | FIX | pipeline and run ids: safe_name + contained; register_pipeline wrote a definition anywhere before |
| 92 | py/path-injection | services/creative_pipeline.py:365 | FIX | pipeline and run ids: safe_name + contained; register_pipeline wrote a definition anywhere before |
| 93 | py/path-injection | services/creative_pipeline.py:366 | FIX | pipeline and run ids: safe_name + contained; register_pipeline wrote a definition anywhere before |
| 94 | py/path-injection | services/creative_engine.py:1154 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded, bare-name fallback contained |
| 95 | py/path-injection | services/creative_engine.py:1157 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded, bare-name fallback contained |
| 96 | py/path-injection | services/creative_engine.py:1159 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded, bare-name fallback contained |
| 97 | py/path-injection | services/creative_engine.py:1159 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded, bare-name fallback contained |
| 98 | py/path-injection | services/creative_engine.py:1161 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded, bare-name fallback contained |
| 99 | py/path-injection | services/credential_store.py:274 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 100 | py/path-injection | services/credential_store.py:276 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 101 | py/path-injection | services/credential_store.py:278 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 102 | py/path-injection | services/credential_store.py:278 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 103 | py/path-injection | services/credential_store.py:285 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 104 | py/path-injection | services/credential_store.py:296 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 105 | py/path-injection | services/credential_store.py:596 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 106 | py/path-injection | services/credential_store.py:618 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 107 | py/path-injection | services/credential_store.py:629 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 108 | py/path-injection | services/credential_store.py:631 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 109 | py/path-injection | services/credential_store.py:835 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 110 | py/path-injection | services/credential_store.py:864 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 111 | py/path-injection | services/credential_store.py:845 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 112 | py/path-injection | services/credential_store.py:853 | FALSE_POSITIVE | every flagged path comes from _provider_key_path (alnum, '-' and '_' only) or the phone SECRET_NAMES allowlist |
| 113 | py/path-injection | services/distributions.py:94 | FIX | _distro_path = contained(DISTROS_DIR, safe_name(name.yaml)); save wrote and /apply loaded a persona from outside before |
| 114 | py/path-injection | services/distributions.py:95 | FIX | _distro_path = contained(DISTROS_DIR, safe_name(name.yaml)); save wrote and /apply loaded a persona from outside before |
| 115 | py/path-injection | services/distributions.py:129 | FIX | _distro_path = contained(DISTROS_DIR, safe_name(name.yaml)); save wrote and /apply loaded a persona from outside before |
| 118 | py/path-injection | services/file_grants.py:353 | FALSE_POSITIVE | intended: the owner names the file/folder to grant or deny in the login_required consent routes |
| 119 | py/path-injection | services/file_grants.py:385 | FALSE_POSITIVE | intended: the owner names the file/folder to grant or deny in the login_required consent routes |
| 120 | py/path-injection | services/file_grants.py:387 | FALSE_POSITIVE | intended: the owner names the file/folder to grant or deny in the login_required consent routes |
| 121 | py/path-injection | services/file_grants.py:404 | FALSE_POSITIVE | intended: the owner names the file/folder to grant or deny in the login_required consent routes |
| 122 | py/path-injection | services/file_grants.py:406 | FALSE_POSITIVE | intended: the owner names the file/folder to grant or deny in the login_required consent routes |
| 123 | py/path-injection | services/futurespeak.py:127 | FALSE_POSITIVE | repo_path/repo come from the owner's saved FutureSpeak project records; exists() checks only |
| 124 | py/path-injection | services/futurespeak.py:132 | FALSE_POSITIVE | repo_path/repo come from the owner's saved FutureSpeak project records; exists() checks only |
| 125 | py/path-injection | services/futurespeak.py:138 | FALSE_POSITIVE | repo_path/repo come from the owner's saved FutureSpeak project records; exists() checks only |
| 126 | py/path-injection | services/futurespeak.py:138 | FALSE_POSITIVE | repo_path/repo come from the owner's saved FutureSpeak project records; exists() checks only |
| 127 | py/path-injection | services/goals.py:272 | FIX | goal id: safe_name + contained in GOALS_DIR |
| 128 | py/path-injection | services/goals.py:275 | FIX | goal id: safe_name + contained in GOALS_DIR |
| 129 | py/path-injection | services/google_accounts.py:532 | FALSE_POSITIVE | account_id must match a record in the index Friday's own OAuth flow writes before the token file is touched |
| 130 | py/path-injection | services/google_accounts.py:564 | FALSE_POSITIVE | account_id must match a record in the index Friday's own OAuth flow writes before the token file is touched |
| 131 | py/path-injection | services/hints.py:44 | FALSE_POSITIVE | intended: hints for an owner-named workspace path; only files named .fridayhints are read walking up |
| 132 | py/path-injection | services/hints.py:54 | FALSE_POSITIVE | intended: hints for an owner-named workspace path; only files named .fridayhints are read walking up |
| 133 | py/path-injection | services/hints.py:57 | FALSE_POSITIVE | intended: hints for an owner-named workspace path; only files named .fridayhints are read walking up |
| 134 | py/path-injection | services/memory_dreaming.py:356 | FALSE_POSITIVE | day must match ^\d{4}-\d{2}-\d{2}$ before the join |
| 135 | py/path-injection | services/misc_engine.py:466 | FIX | event_id: safe_name + contained in FLOW_QUEUE_DIR (route answers 400); a '/../' id wrote JSON anywhere before |
| 136 | py/path-injection | services/model_discovery.py:197 | FALSE_POSITIVE | _cache_path keeps only alnum, '-' and '_' of the provider name before joining under CACHE_DIR |
| 137 | py/path-injection | services/model_discovery.py:200 | FALSE_POSITIVE | _cache_path keeps only alnum, '-' and '_' of the provider name before joining under CACHE_DIR |
| 140 | py/path-injection | services/model_discovery.py:264 | FALSE_POSITIVE | _cache_path keeps only alnum, '-' and '_' of the provider name before joining under CACHE_DIR |
| 141 | py/path-injection | services/model_discovery.py:266 | FALSE_POSITIVE | _cache_path keeps only alnum, '-' and '_' of the provider name before joining under CACHE_DIR |
| 142 | py/path-injection | services/model_seat_gate.py:316 | FIX | _safe_name now also replaces backslash and the verdict path is contained in GATE_DIR |
| 143 | py/path-injection | services/model_seat_gate.py:319 | FIX | _safe_name now also replaces backslash and the verdict path is contained in GATE_DIR |
| 144 | py/path-injection | services/moderation.py:250 | FALSE_POSITIVE | scan(content_path) is the owner's login_required /api/moderation/scan on a file they chose; returns a verdict |
| 145 | py/path-injection | services/moderation.py:250 | FALSE_POSITIVE | scan(content_path) is the owner's login_required /api/moderation/scan on a file they chose; returns a verdict |
| 146 | py/path-injection | services/moderation.py:251 | FALSE_POSITIVE | scan(content_path) is the owner's login_required /api/moderation/scan on a file they chose; returns a verdict |
| 147 | py/path-injection | services/music_engine.py:512 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded (load_local_image) |
| 148 | py/path-injection | services/music_engine.py:515 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded (load_local_image) |
| 149 | py/path-injection | services/music_engine.py:517 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded (load_local_image) |
| 150 | py/path-injection | services/music_engine.py:517 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded (load_local_image) |
| 151 | py/path-injection | services/music_engine.py:518 | FALSE_POSITIVE | intended owner/model-named seed image; hardened: only image bytes are read and uploaded (load_local_image) |
| 152 | py/path-injection | routes/news.py:144 | FIX | path from _find_briefing_path, now contained |
| 153 | py/path-injection | routes/news.py:355 | FIX | path from _find_briefing_path, now contained |
| 154 | py/path-injection | routes/news.py:659 | FALSE_POSITIVE | draft id reduced by re.sub to [0-9a-zA-Z] |
| 155 | py/path-injection | routes/news.py:663 | FALSE_POSITIVE | draft id reduced by re.sub to [0-9a-zA-Z] |
| 156 | py/path-injection | services/news_engine.py:66 | FIX | briefing name: safe_name + contained in both briefing folders; GET /api/briefing/..%5C..%5Cx read any file under the home before |
| 157 | py/path-injection | services/news_engine.py:70 | FIX | briefing name: safe_name + contained in both briefing folders |
| 159 | py/path-injection | services/news_engine.py:2211 | FALSE_POSITIVE | edition id reduced by re.sub to [0-9a-zA-Z-] before the join |
| 160 | py/path-injection | services/news_engine.py:2214 | FALSE_POSITIVE | edition id reduced by re.sub to [0-9a-zA-Z-] before the join |
| 161 | py/path-injection | services/news_engine.py:2634 | FALSE_POSITIVE | _editorial_summary gets EDITORIALS_DIR/<id> with id reduced to [0-9A-Za-z-] |
| 162 | py/path-injection | services/news_engine.py:2674 | FALSE_POSITIVE | week id reduced by re.sub to [0-9A-Za-z-] before the join |
| 163 | py/path-injection | services/model_router.py:3597 | FALSE_POSITIVE | intended: the owner's message names a project dir; only .friday-context.md/AGENTS.md there are read (3 KB) as code context |
| 164 | py/path-injection | services/research/objects.py:215 | FIX | commission_dir = contained(research_root, safe_name(id)) |
| 165 | py/path-injection | services/ownership.py:412 | FALSE_POSITIVE | verify(file_path) is the owner's login_required /api/ownership/verify on a file they chose; hashes and reads its sidecar only |
| 166 | py/path-injection | services/ownership.py:424 | FALSE_POSITIVE | verify(file_path) is the owner's login_required /api/ownership/verify on a file they chose; hashes and reads its sidecar only |
| 167 | py/path-injection | routes/platform.py:32 | FIX | _find_recipe uses recipes.recipe_path (contained); /api/recipes/..%5Cx/run ran a recipe from outside the folder before |
| 168 | py/path-injection | services/provenance.py:115 | FIX | hash_file now receives contained creation paths from the routes (other caller: owner-chosen /api/ownership/verify) |
| 169 | py/path-injection | services/provenance.py:201 | FIX | build_manifest path comes from contained creation paths |
| 170 | py/path-injection | services/provenance.py:338 | FIX | _sidecar_path is safe_name + contained in PROVENANCE_DIR; /api/provenance/%2E%2E/x read any .jsonld before |
| 171 | py/path-injection | services/provenance.py:339 | FIX | _sidecar_path is safe_name + contained in PROVENANCE_DIR |
| 172 | py/path-injection | services/provenance.py:439 | FALSE_POSITIVE | verify_manifest(path) is reached from the owner's login_required /api/ownership/verify with a file they chose; read-only |
| 173 | py/path-injection | services/provenance.py:467 | FIX | manifest filename is safe_name + contained in CREATIONS_DIR and must be a file |
| 174 | py/path-injection | services/provider_registry.py:772 | FALSE_POSITIVE | provider name must match ^[a-z0-9][a-z0-9-]{0,63}$ (validate_descriptor); remove requires a registered name |
| 175 | py/path-injection | services/provider_registry.py:792 | FALSE_POSITIVE | provider name must match ^[a-z0-9][a-z0-9-]{0,63}$ (validate_descriptor); remove requires a registered name |
| 176 | py/path-injection | services/provider_registry.py:806 | FALSE_POSITIVE | provider name must match ^[a-z0-9][a-z0-9-]{0,63}$ (validate_descriptor); remove requires a registered name |
| 177 | py/path-injection | services/provider_registry.py:808 | FALSE_POSITIVE | provider name must match ^[a-z0-9][a-z0-9-]{0,63}$ (validate_descriptor); remove requires a registered name |
| 178 | py/path-injection | services/qa_gates.py:197 | FALSE_POSITIVE | intended owner/pipeline-named image; hardened: only bytes that are an image are read and sent (load_local_image) |
| 179 | py/path-injection | services/qa_gates.py:200 | FALSE_POSITIVE | intended owner/pipeline-named image; hardened: only bytes that are an image are read and sent (load_local_image) |
| 180 | py/path-injection | services/qa_gates.py:201 | FALSE_POSITIVE | intended owner/pipeline-named image; hardened: only bytes that are an image are read and sent (load_local_image) |
| 181 | py/path-injection | services/qa_gates.py:204 | FALSE_POSITIVE | intended owner/pipeline-named image; hardened: only bytes that are an image are read and sent (load_local_image) |
| 182 | py/path-injection | services/recipes.py:89 | FIX | recipe_path = contained(RECIPES_DIR, safe_name(name.yaml)); save wrote and /run executed a YAML outside the folder before |
| 183 | py/path-injection | services/recipes.py:109 | FIX | recipe_path = contained(RECIPES_DIR, safe_name(name.yaml)); save wrote and /run executed a YAML outside the folder before |
| 184 | py/path-injection | skill_registry.py:174 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 185 | py/path-injection | skill_registry.py:197 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 186 | py/path-injection | skill_registry.py:316 | FALSE_POSITIVE | folder name is _safe_name (re.sub to [\w-]) |
| 187 | py/path-injection | skill_registry.py:323 | FALSE_POSITIVE | folder name is _safe_name (re.sub to [\w-]) |
| 188 | py/path-injection | skill_registry.py:336 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 189 | py/path-injection | skill_registry.py:342 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 190 | py/path-injection | skill_registry.py:354 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 191 | py/path-injection | skill_registry.py:361 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 192 | py/path-injection | skill_registry.py:366 | FALSE_POSITIVE | import_skill is the owner importing a folder/zip they chose (login_required route); upload path is fixed in routes/skills.py |
| 193 | py/path-injection | skill_registry.py:412 | FALSE_POSITIVE | staging folder under mkdtemp named by _safe_name; items come from iterating the skill's own folder |
| 194 | py/path-injection | skill_registry.py:417 | FALSE_POSITIVE | staging folder under mkdtemp named by _safe_name; items come from iterating the skill's own folder |
| 195 | py/path-injection | skill_registry.py:419 | FALSE_POSITIVE | staging folder under mkdtemp named by _safe_name; items come from iterating the skill's own folder |
| 196 | py/path-injection | skill_registry.py:421 | FALSE_POSITIVE | staging folder under mkdtemp named by _safe_name |
| 197 | py/path-injection | skill_registry.py:423 | FALSE_POSITIVE | staging folder under mkdtemp named by _safe_name |
| 198 | py/path-injection | routes/skills.py:68 | FIX | upload filename reduced to its last component and contained in the temp dir; '../x.zip' wrote outside it before |
| 199 | py/path-injection | services/timeline_engine.py:164 | FALSE_POSITIVE | intended: compose_timeline clip references accept the owner's absolute media paths; local FFmpeg (ring 1), output stays local |
| 200 | py/path-injection | services/timeline_engine.py:170 | FALSE_POSITIVE | intended clip lookup under the creations roots; read locally by FFmpeg only |
| 201 | py/path-injection | services/timeline_engine.py:170 | FALSE_POSITIVE | intended clip lookup under the creations roots; read locally by FFmpeg only |
| 202 | py/path-injection | services/timeline_engine.py:570 | FIX | timeline_id must be a plain name (validate_timeline) and the record is contained in timelines/ |
| 203 | py/path-injection | services/wiki_engine.py:97 | FIX | _wiki_path_is_sensitive only compares; every caller's path is now contained upstream |
| 204 | py/path-injection | services/wiki_engine.py:116 | FIX | wiki_read_text reached only with contained paths now (routes via _safe_wiki_path, sinks via contained) |
| 205 | py/path-injection | services/wiki_engine.py:136 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 206 | py/path-injection | services/wiki_engine.py:148 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 207 | py/path-injection | services/wiki_engine.py:149 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 208 | py/path-injection | services/wiki_engine.py:149 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 209 | py/path-injection | services/wiki_engine.py:163 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 210 | py/path-injection | services/wiki_engine.py:182 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 212 | py/path-injection | services/wiki_engine.py:189 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 213 | py/path-injection | services/wiki_engine.py:192 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 214 | py/path-injection | services/wiki_engine.py:192 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 218 | py/path-injection | services/wiki_engine.py:216 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 219 | py/path-injection | services/wiki_engine.py:216 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 220 | py/path-injection | services/wiki_engine.py:217 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 221 | py/path-injection | services/wiki_engine.py:275 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 223 | py/path-injection | services/workflow_plan.py:322 | FIX | proposal id: safe_name + contained in proposals_dir |
| 224 | py/path-injection | services/workspace_studio.py:66 | FALSE_POSITIVE | _ws_path reduces the id to [a-z0-9_-]{,48} |
| 225 | py/path-injection | services/workspace_studio.py:69 | FALSE_POSITIVE | _ws_path reduces the id to [a-z0-9_-]{,48} |
| 226 | py/path-injection | services/workspace_studio.py:84 | FALSE_POSITIVE | _ws_path reduces the id to [a-z0-9_-]{,48} |
| 227 | py/path-injection | routes/workflows.py:155 | FIX | draft name: safe_name + contained in CONTENT_DRAFTS_DIR |
| 228 | py/path-injection | routes/workflows.py:155 | FIX | draft name: safe_name + contained in CONTENT_DRAFTS_DIR |
| 229 | py/path-injection | routes/workflows.py:225 | FIX | draft_id: safe_name + contained; a '/../' id rewrote any JSON on the disk (read-modify-write) before |
| 230 | py/path-injection | routes/workflows.py:229 | FIX | draft_id: safe_name + contained; a '/../' id rewrote any JSON on the disk (read-modify-write) before |
| 231 | py/path-injection | routes/workflows.py:232 | FIX | draft_id: safe_name + contained; a '/../' id rewrote any JSON on the disk (read-modify-write) before |
| 548 | py/path-injection | services/task_journal.py:188 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 549 | py/path-injection | services/task_journal.py:190 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 550 | py/path-injection | services/task_journal.py:194 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 551 | py/path-injection | services/task_journal.py:194 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 552 | py/path-injection | services/task_journal.py:222 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 553 | py/path-injection | services/task_journal.py:223 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 554 | py/path-injection | services/task_journal.py:235 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 555 | py/path-injection | services/task_journal.py:239 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 556 | py/path-injection | services/task_journal.py:282 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 557 | py/path-injection | services/task_journal.py:285 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 558 | py/path-injection | services/task_journal.py:431 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 559 | py/path-injection | services/task_journal.py:433 | FIX | task_dir = contained(tasks_dir, safe_name(id)); DELETE /api/tasks/..%5Cx rmtree'd a sibling of the tasks folder before |
| 567 | py/path-injection | services/projects.py:108 | FIX | project id: safe_name + contained; DELETE /api/projects/..%5Cx unlinked another folder's project.json before |
| 568 | py/path-injection | services/projects.py:111 | FIX | project id: safe_name + contained; DELETE /api/projects/..%5Cx unlinked another folder's project.json before |
| 569 | py/path-injection | services/projects.py:200 | FIX | project id: safe_name + contained; DELETE /api/projects/..%5Cx unlinked another folder's project.json before |
| 570 | py/path-injection | services/projects.py:201 | FIX | project id: safe_name + contained; DELETE /api/projects/..%5Cx unlinked another folder's project.json before |
| 571 | py/path-injection | services/projects.py:202 | FIX | project id: safe_name + contained; DELETE /api/projects/..%5Cx unlinked another folder's project.json before |
| 572 | py/path-injection | services/projects.py:203 | FIX | project id: safe_name + contained; DELETE /api/projects/..%5Cx unlinked another folder's project.json before |
| 588 | py/path-injection | services/agent.py:4383 | FALSE_POSITIVE | _chain_slug reduces the name to [a-z0-9_-] |
| 589 | py/path-injection | services/agent.py:4384 | FALSE_POSITIVE | _chain_slug reduces the name to [a-z0-9_-] |
| 593 | py/path-injection | services/news_engine.py:2153 | FALSE_POSITIVE | _atomic_write_json path is FRONT_PAGES_DIR/<edition_id> built internally from date+slot, never from a request |
| 594 | py/path-injection | services/news_engine.py:2154 | FALSE_POSITIVE | _atomic_write_json path is FRONT_PAGES_DIR/<edition_id> built internally from date+slot |
| 595 | py/path-injection | services/news_engine.py:2154 | FALSE_POSITIVE | _atomic_write_json path is FRONT_PAGES_DIR/<edition_id> built internally from date+slot |
| 609 | py/path-injection | services/gmail_send.py:182 | FALSE_POSITIVE | sha must re.fullmatch [0-9a-f]{64} before joining under the attachment dir |
| 610 | py/path-injection | services/gmail_send.py:185 | FALSE_POSITIVE | sha must re.fullmatch [0-9a-f]{64} before joining under the attachment dir |
| 644 | py/path-injection | phone/config.py:231 | FALSE_POSITIVE | name must be in SECRET_NAMES (get_secret/set_secret check it; the DELETE route checks before delete_secret/secret_status) |
| 645 | py/path-injection | phone/config.py:243 | FALSE_POSITIVE | name must be in SECRET_NAMES (get_secret/set_secret check it; the DELETE route checks before delete_secret/secret_status) |
| 646 | py/path-injection | phone/config.py:250 | FALSE_POSITIVE | name must be in SECRET_NAMES (get_secret/set_secret check it; the DELETE route checks before delete_secret/secret_status) |
| 647 | py/path-injection | phone/config.py:252 | FALSE_POSITIVE | name must be in SECRET_NAMES (get_secret/set_secret check it; the DELETE route checks before delete_secret/secret_status) |
| 652 | py/path-injection | routes/creations.py:116 | FIX | existence probe and office-document fallback now contained in CREATIONS_DIR / DOCUMENTS_DIR |
| 657 | py/path-injection | routes/wiki.py:56 | FIX | path comes from _safe_wiki_path, which is now contained(WIKI_DIR, rel) |
| 658 | py/path-injection | routes/wiki.py:79 | FIX | path comes from _safe_wiki_path, which is now contained(WIKI_DIR, rel) |
| 659 | py/path-injection | routes/wiki.py:93 | FIX | path comes from _safe_wiki_path, which is now contained(WIKI_DIR, rel) |
| 660 | py/path-injection | routes/wiki.py:211 | FIX | path comes from _safe_wiki_path, which is now contained(WIKI_DIR, rel) |
| 661 | py/path-injection | routes/wiki.py:217 | FIX | path comes from _safe_wiki_path, which is now contained(WIKI_DIR, rel) |
| 663 | py/path-injection | services/career_ops.py:96 | FALSE_POSITIVE | set_root is the owner choosing the career-ops folder from Settings; it only checks is_dir and stores the path |
| 664 | py/path-injection | services/career_ops.py:98 | FALSE_POSITIVE | set_root is the owner choosing the career-ops folder from Settings; it only checks is_dir and stores the path |
| 665 | py/path-injection | services/meeting_capture.py:304 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 666 | py/path-injection | services/meeting_capture.py:306 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 667 | py/path-injection | services/meeting_capture.py:772 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 668 | py/path-injection | services/meeting_capture.py:779 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 669 | py/path-injection | services/meeting_capture.py:786 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 670 | py/path-injection | services/meeting_capture.py:797 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 671 | py/path-injection | services/meeting_capture.py:800 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 672 | py/path-injection | services/meeting_capture.py:801 | FIX | id already had to match ^[0-9a-f]{12}$; now fullmatch (no trailing newline) and joined with contained |
| 673 | py/path-injection | services/model_discovery.py:215 | FALSE_POSITIVE | _cache_path keeps only alnum, '-' and '_' of the provider name before joining under CACHE_DIR |
| 674 | py/path-injection | services/model_discovery.py:217 | FALSE_POSITIVE | _cache_path keeps only alnum, '-' and '_' of the provider name before joining under CACHE_DIR |
| 675 | py/path-injection | services/wiki_engine.py:183 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 676 | py/path-injection | services/wiki_engine.py:209 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 677 | py/path-injection | services/wiki_engine.py:209 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 678 | py/path-injection | services/wiki_engine.py:210 | FIX | wiki write/delete sinks and _safe_wiki_path now build every path with contained(WIKI_DIR / mirror root); an absolute or ../ rel deleted outside the wiki before |
| 747 | py/path-injection | services/desktop_targets.py:400 | FALSE_POSITIVE | resolve_file(id) only maps the path onto Studio's roots via relative_to; it reads nothing and fails for anything outside them |

## Regular expressions (ReDoS, tag filters)

| n | rule | path:line | disposition | reason |
|---|---|---|---|---|
| 742 | py/polynomial-redos | tests/unit/test_compaction_mechanics.py:155 | TEST_ONLY (fixed) | Test summarizer stub; `(?<!\d)\d+:\d` starts only at a digit run, same matches on the test data. |
| 741 | py/polynomial-redos | src/agent_friday/services/task_ledger.py:251 | FIX | `_SECTION` drops the `\s*` before `(.*)`; group 2 was already stripped by the caller. Not reachable slow in practice (lines from splitlines), hardening only. |
| 739 | py/polynomial-redos | src/agent_friday/services/workflow_overview.py:262 | FIX | `(?<!\s)\s+([,.;:!?])`: match starts only at a whitespace run start; identical replacements. |
| 738 | py/polynomial-redos | src/agent_friday/services/workflow_overview.py:187 | FIX | `\s+(?:(\d+\|[a-z]+)\s*)?unit` — same language and groups, no shared spaces. Was 19.7 s on 50k chars, now fast. |
| 704 | py/polynomial-redos | src/agent_friday/services/style_guard.py:88 | FIX | Second `\s*` moved inside the optional marker group; same lead/body split. Hardening (not slow in practice). |
| 703 | py/polynomial-redos | src/agent_friday/services/setup_research.py:221 | FIX | Split host at the last dot (rpartition) and check head/TLD with two linear fullmatches; equivalent (fuzzed). |
| 702 | py/polynomial-redos | src/agent_friday/services/relationship_memory.py:543 | FIX | `_EMAIL` domain matches up to its FIRST inner dot: `[^..][^..,.]*\.[^..]+$`; same accepted set (fuzzed). |
| 597 | py/bad-tag-filter | tests/unit/test_ui_parses.py:81 | TEST_ONLY (fixed) | Scans our own app.html; now re.I and `</script\b[^>]*>`. |
| 596 | py/bad-tag-filter | tests/unit/test_ui_parses.py:45 | TEST_ONLY (fixed) | Scans our own index.html; end tag now `</script\b[^>]*>`. |
| 574 | py/polynomial-redos | src/agent_friday/services/gmail_send.py:121 | FIX | `_ADDR` same first-inner-dot rewrite as 702. Was 10.3 s on 50k chars. |
| 573 | py/polynomial-redos | src/agent_friday/services/agent.py:2635 | FIX | Trailing-politeness `_tail` gets `(?<!\s)`; identical leftmost match. Input is already whitespace-collapsed, so hardening. |
| 262 | py/polynomial-redos | src/agent_friday/services/knowledge_graph/structural_query.py:143 | FIX | `_PATH_PATTERNS` replaced by `_path_terms`: lead-in and separator found once each (lookahead finditer + bisect); identical groups (150k-case differential fuzz). Was 3.8 s. |
| 261 | py/polynomial-redos | src/agent_friday/services/sensitivity_classifier.py:444 | FIX | `_ISSUED_ID_RE` / `_ACCT_TAIL_RE`: each whitespace run owned by one quantifier; same spans (fuzzed with finditer spans, they feed .sub redaction). Was 103 s. Sensitive subsystem. |
| 260 | py/polynomial-redos | src/agent_friday/services/retrieval_ledger.py:65 | FIX | `_SECTION_NAME_RE` replaced by `_section_header` scan; derive_section_name output identical (fuzzed). Input was capped at 160 chars, so low impact. |
| 259 | py/polynomial-redos | src/agent_friday/services/message_triage.py:404 | FIX | `_EMAIL_RE` gets `(?<![\w.\-+'])`: leftmost match always starts at the run start; identical spans. Was 3.9 s. |
| 258 | py/polynomial-redos | src/agent_friday/services/message_triage.py:370 | FIX | Same regex as 259. |
| 257 | py/polynomial-redos | src/agent_friday/services/extension_security.py:229 | FIX | `<[^>]+>` alternative moved out of `_PLACEHOLDER` into `_has_angle_placeholder` (str.find scan, same truth value). Was 2.4 s. |
| 256 | py/polynomial-redos | src/agent_friday/services/content_composer.py:463 | FIX | `^#{1,3}\s+(\S.*)$`: resulting title identical (a whitespace-only heading gave "" and fell back before; now no match and the same fallback). Hardening. |
| 255 | py/polynomial-redos | src/agent_friday/services/agent.py:2661 | FIX | `_OPEN_VERB_RE` target is `\s+(?=\S)([^\n]*[^\s?.!]\|[?.!])[\s?.!]*$`; identical groups on stripped input (callers strip). Was 17 s. |
| 254 | py/polynomial-redos | src/agent_friday/services/agent.py:2646 | FIX | UI-noise suffix gets `(?<!\s)`; identical. Hardening (input collapsed). |
| 253 | py/polynomial-redos | src/agent_friday/services/agent.py:2535 | FIX | Same regex as 255. |
| 252 | py/polynomial-redos | src/agent_friday/services/agent.py:2365 | FIX | Folder-word suffix gets `(?<!\s)`; identical. Hardening (input collapsed). |
| 251 | py/polynomial-redos | src/agent_friday/governance/behavioral_monitor.py:255 | FIX | Path findall gets `(?<![\w./\\-])`; a run holds at most one match and it starts at the run start, so results identical. Was 5.1 s. Sensitive subsystem. |
| 248 | py/bad-tag-filter | src/agent_friday/services/voice_engine.py:1479 | FIX | `_strip_html` now uses shared linear `services/html_text.html_to_text` (any-case/attribute/whitespace end tags, comments, all entities). |
| 247 | py/bad-tag-filter | src/agent_friday/services/web_fetch.py:68 | FIX | No-bs4 fallback uses html_to_text. |
| 246 | py/bad-tag-filter | src/agent_friday/services/agent.py:3000 | FIX | Briefing HTML uses html_to_text. |
| 245 | py/bad-tag-filter | src/agent_friday/services/agent.py:758 | FIX | No-bs4 fallback uses html_to_text. |
| 4 | js/bad-tag-filter | index.html:25857 | FIX | Read-aloud text via DOMParser (scripts inert), script/style removed, text nodes joined. |
| 3 | js/bad-tag-filter | ui_parts/app.html:7453 | FIX | Identical change mirrored. |

## Web (reflected XSS, format strings)

| n | rule | path:line | disposition | reason |
|---|---|---|---|---|
| 643 | py/reflective-xss | src/agent_friday/phone/ingress.py:236 | FIX | Hardening. The returned value is always a bodiless `_plain()`, so the old code was not exploitable. `_check` now returns `(refusal, result)` so request fields never share a value with the response. |
| 642 | py/reflective-xss | src/agent_friday/phone/ingress.py:224 | FIX | Same `_check` refactor (recording route). |
| 641 | py/reflective-xss | src/agent_friday/phone/ingress.py:217 | FIX | Same `_check` refactor (voice-done route). |
| 640 | py/reflective-xss | src/agent_friday/phone/ingress.py:205 | FIX | Same `_check` refactor (voice route). |
| 639 | py/reflective-xss | src/agent_friday/phone/ingress.py:192 | FIX | Same `_check` refactor (sms-status route). |
| 638 | py/reflective-xss | src/agent_friday/phone/ingress.py:180 | FIX | Same `_check` refactor (sms route). |
| 621 | py/reflective-xss | src/agent_friday/routes/core_routes.py:159 | FIX | `/w/<ws_id>`: `match` with `$` accepted a trailing newline, so it is now `fullmatch`. The name is also HTML-escaped where it enters the page (a no-op for the allowed character set). |
| 566 | js/tainted-format-string | index.html:52372 | FIX | VOICE_DEBUG log passes `m.type` as a `'%s'` argument. This line exists only in index.html. |
| 250 | py/reflective-xss | src/agent_friday/routes/google.py:154 | FIX | Real bug: the OAuth callback echoed the `?error=` value and exception text into HTML unescaped. All three are now `html.escape`d. |
| 249 | py/reflective-xss | src/agent_friday/routes/creations.py:239 | FIX | `/creation/<file>`: the file name in src/href is now URL-quoted and escaped, and the Host-derived return link is escaped. The Markdown JSON inside `<script>` now writes `<` as `<`. |
| 6 | js/tainted-format-string | index.html:6273 | FIX | Action-bus log uses `'%s'` with `actions.length`. The same change is mirrored in ui_parts/app.html. |
| 5 | js/tainted-format-string | ui_parts/app.html:417 | FIX | Mirror of alert 6. |

