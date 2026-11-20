# PW-G10 (settings part): `GET` / `PATCH /tv/v1/settings`, allowlisted: design note before any code

Status: design only (BURN-DOWN, 2026-11-17). Spec: phone workspaces parity PW-G10 ("PC settings rows (`GET`/`PATCH
/tv/v1/settings`, allowlisted)") and PW-10 ("PC model and voice in Settings"). The spec does **not** say which settings
are on the allowlist; that list is the whole safety of the route, so it is decided here before any code.

## What exists (checked in the code)

* `POST /api/settings` (`routes/core_routes.py`) is the one write path. `services/settings_schema.py` +
  `resources/settings.schema.json` give every key a type and, for many, choices and ranges taken from the same constants
  the route enforces.
* `services/guarded_settings.py` keeps settings that **only the owner's own click on the PC's Settings page may widen**
  (today `laya_safe_writes`; `core._save_settings_locked` drops every other widening write). A device is by definition
  not that click.
* The chat model and the voice a device picks already have their own routes (`tv_gateway/models.py`, `voices.py`,
  `owner_turn.bind_seat`), with the PC deciding what is allowed.
* `settings.json` also holds keys, tokens, ports, paths and policy switches. None of those may be readable or writable from
  a phone.

## Why no route is built yet

A PATCH that accepts "any key the schema knows" would let a phone change egress policy, approvals, key locations or ports.
A PATCH with a hand-picked list needs the list agreed, because adding a key later is a security review, not a code change.

## Proposal

1. **The allowlist is a checked-in table, not a pattern**: `tv_gateway/pc_settings_rows.py` holds a frozen list of rows,
   each `{key, label, kind: enum|bool|int, choices|range, group, direction}` copied from the schema's own constraints (a test
   fails if a row disagrees with `settings.schema.json` or names a key that no longer exists). A key in `GUARDED`, any key
   whose name contains key, token, secret, password, port, path, url, host, email or phone, and anything under egress,
   approvals, federation or the credential store is refused by name even if someone adds it to the table (a second rule,
   Q1).
2. **First rows (proposal), all reversible and non-secret:** text size, reduce motion, quiet hours on/off, the default
   voice (read: the existing `voices` route stays the writer), the daily briefing time, notification categories on/off.
   Nothing that widens what Friday may do. No model or provider switch here (that is `models`).
3. `GET` returns the rows with the current value read from the PC's settings (never the raw file); `PATCH {key, value}`
   validates type and range from the row, writes through `POST /api/settings` with the single key, and answers the new
   value. A write that the PC's own validator refuses is passed on in its words.
4. Rights: `pc_read` for GET; `pc_act`, owner-bound phone or tablet, for PATCH; a `device_act` receipt names the key and
   outcome, never a value that could be personal (the briefing time is a time; free text rows are not allowed).
5. **Direction rule:** a row marked `narrowing_only` (anything that reduces what Friday does) may be changed from a device;
   a row that would widen needs the owner's click on the PC and answers `card` or `refused`.

## Tests (each with a red twin)

A key not in the table is refused before the desktop is asked; every row agrees with the schema; a forbidden-by-name key is
refused even when added to the table; a `GUARDED` key is refused; a value of the wrong type or outside the range is
refused; without `pc_act` or from a non-owner device nothing is written and the PC asks; the receipt carries no value for
free-text rows; the write goes through `POST /api/settings` (a spy), not around it.

## Questions for Dispatch / Stephen

* Is the first-rows list above the right one, or what does he want to change from the phone first?
* Is "narrowing only from a device, widening only on the PC" the rule he wants?
* Should the route live behind the same card as approvals when a row changes anything the household sees (quiet hours)?
