# Which gate refused?

*Written 2026-08-25, after a day spent believing the vault was blocking the news.*

Friday has three independent gates that can refuse a tool call. To the user they
produce almost identical experiences — Friday says she can't do the thing — and
Friday herself, handed a refusal string, will paraphrase it. **Her paraphrase is
not evidence of which gate fired.** Read the log lines.

## The three gates

| Gate | Where | Asks | Log prefix |
|---|---|---|---|
| **Vault / zero-trust** | `privacy/vault_access.py` → `check_action()`, called from `services/agent.py` | "Can THIS provider see data of THIS sensitivity?" | `[VAULT]` / `[VAULT-ZT]` |
| **Governance rings** | `services/agent.py` → `_governance_check()` | "Is this ring permitted in this session?" Ring 0–1 always; **ring 2 = every network tool, requires an authenticated session**; ring 3 = OS control, requires Computer Control enabled | `[GOV]` |
| **Egress** | `services/egress_gate.py` | "May these bytes leave the machine for this provider?" | `[FRIDAY] ... BLOCK` / `WARNING BLOCK` |

They are orthogonal. A call can pass two and fail the third, which is exactly
what makes the symptom confusing.

## The worked example

Stephen asked local voice for the news and was told it was prohibited "even
though we were using local". Reasonable reading: the vault is blocking a local
session, which would be backwards, since the vault exists to keep private data
off *cloud* seats.

The log said otherwise:

```
[VAULT] ALLOW provider=cloud tier=TIER_1 (check_action:search_news)
[VAULT-ZT] ALLOW provider=cloud action=search_news tier=TIER_1
[GOV] DENY  search_news (ring=2): ring-2 network op requires authenticated session
```

**The vault allowed it, twice, and said so.** Governance refused it. The local
voice handler passed no `session_ctx`, so the ring gate evaluated `{}`, found no
`authenticated` flag, and denied every network tool. `routes/chat.py` had always
passed that context; voice was the only tool-using surface that did not — which
is why the identical question worked in text chat and failed in voice.

Two of the three gates logged ALLOW on the turn that was reported as a vault
refusal.

## Rules

1. **Read the log lines before forming a hypothesis.** Every gate announces
   itself with a distinct prefix and a reason. Grep for `[GOV]`, `[VAULT]`, and
   `BLOCK` on the failing turn before reasoning about policy.
2. **Never infer the gate from Friday's wording.** She is relaying a string. A
   governance denial and a vault denial both come back to her as "denied", and
   she will render either as "I'm not allowed to".
3. **Failure messages lead with the cause, not the policy.** Users stop reading
   at the first clause. A message that opens by naming the vault will be
   remembered as a vault problem no matter what the rest of the sentence says.
   See `agent.py`'s vault-fallback message, which was reordered for exactly this
   reason.
4. **Name the right remediation.** "Check that Ollama is running" is wrong for
   Friday's own seats — those are served by her llama-server on `127.0.0.1:8090+`,
   a different process entirely, whose health Ollama's status does not report.
5. **A gate that reads context must be given context.** Both defects here were
   one missing `session_ctx`: governance saw no `authenticated` flag, and the
   zero-trust vault check fell back to its `provider="cloud"` default and so
   evaluated a *local* brain as cloud. Any surface that calls `_generate_agent`
   with tools must pass `{"authenticated": ..., "provider": ...}`.

## Checklist for "Friday says she can't"

- [ ] Grep the turn for `[GOV]`, `[VAULT]`, `[VAULT-ZT]`, `BLOCK`.
- [ ] If `[GOV] DENY ... ring=2` → the surface is not passing an authenticated
      `session_ctx`. Not a permissions decision; a plumbing one.
- [ ] If `[GOV] DENY ... ring=3` → Computer Control is off. Expected.
- [ ] If `[VAULT] DENY` → genuinely sensitive data heading for a cloud provider.
      Check which provider the call was evaluated against; a missing
      `session_ctx` makes a local brain look like cloud.
- [ ] If `BLOCK` from the egress gate → content-level, see the privacy layer docs.
- [ ] If **nothing** denied and she still says she can't → she is describing a
      tool result, or she has been told about a tool she was never handed.
