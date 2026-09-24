# Which gate refused?

> **Status:** engineering guidance · **Last verified against the code:** 2026-09-24

Friday has three independent gates that can refuse a tool call. To the user they
produce almost identical experiences — Friday says she can't do the thing — and
Friday herself, handed a refusal string, paraphrases it. **Her paraphrase is not
evidence of which gate fired.** Read the log lines.

## The three gates

| Gate | Where | Asks | Log line |
|---|---|---|---|
| **Vault / zero-trust** | `privacy/vault_access.py` → `check_action()`, called from `services/agent.py` | "Can THIS provider see data of THIS sensitivity?" | `[VAULT]` / `[VAULT-ZT]` |
| **Governance rings** | `services/agent.py` → `_governance_check()` | "Is this ring permitted for this turn?" Rings 0–1 always; **ring 2 = every network tool, requires an authenticated session** (or a background task); ring 3 = OS control, requires Computer Control enabled; a turn that arrived by phone may only use ring 0 | `[GOV]` |
| **Egress** | `services/egress_gate.py` | "May these bytes leave the machine for this provider?" | `friday.egress` logger: `ALLOW` / `BLOCK provider=... field=... tier=...` in `~/.friday/friday.log`, and one record per decision in `~/.friday/vault/egress-log.jsonl` |

They are orthogonal. A call can pass two and fail the third, which is what makes
the symptom confusing: a refusal the user describes as "the vault blocked it" is
often a governance denial with two vault `ALLOW` lines right above it.

```
[VAULT] ALLOW provider=cloud tier=TIER_1 (check_action:search_news)
[VAULT-ZT] ALLOW provider=cloud action=search_news tier=TIER_1
[GOV] DENY  search_news (ring=2): ring-2 network op requires authenticated session
```

## Rules

1. **Read the log lines before forming a hypothesis.** Every gate announces
   itself with a distinct prefix and a reason. Grep for `[GOV]`, `[VAULT]`, and
   `BLOCK` on the failing turn before reasoning about policy.
2. **Never infer the gate from Friday's wording.** She is relaying a string. A
   governance denial and a vault denial both come back to her as "denied", and
   she renders either as "I'm not allowed to".
3. **Failure messages lead with the cause, not the policy.** Users stop reading
   at the first clause. A message that opens by naming the vault is remembered
   as a vault problem no matter what the rest of the sentence says.
4. **Name the right remediation.** "Check that Ollama is running" is wrong for
   Friday's own seats — those are served by her llama-server on `127.0.0.1:8090+`,
   a different process whose health Ollama's status does not report.
5. **A gate that reads context must be given context.** Governance evaluates the
   `session_ctx` it is handed; an empty one has no `authenticated` flag, so every
   network tool is denied. The zero-trust vault check falls back to its
   `provider="cloud"` default when no provider is passed, so a *local* brain is
   evaluated as cloud. Any surface that calls `_generate_agent` with tools must
   pass `{"authenticated": ..., "provider": ...}`.

## Checklist for "Friday says she can't"

- [ ] Grep the turn for `[GOV]`, `[VAULT]`, `[VAULT-ZT]`, `BLOCK`.
- [ ] `[GOV] DENY ... ring=2` → the surface is not passing an authenticated
      `session_ctx`. Not a permissions decision; a plumbing one.
- [ ] `[GOV] DENY ... ring=3` → Computer Control is off. Expected.
- [ ] `[VAULT] DENY` → genuinely sensitive data heading for a cloud provider.
      Check which provider the call was evaluated against; a missing
      `session_ctx` makes a local brain look like cloud.
- [ ] `BLOCK` from `friday.egress` → content-level; see the
      [threat model](../security/threat-model.md).
- [ ] **Nothing** denied and she still says she can't → she is describing a
      tool result, or she has been told about a tool she was never handed.
