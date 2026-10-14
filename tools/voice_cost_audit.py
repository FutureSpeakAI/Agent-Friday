"""Audit cloud voice spend in the cost ledger (read-only, aggregates only).

Question: do the stored Gemini Live voice rows overstate the spend? Each row is
one ``usage_metadata`` message. If those messages were running session totals,
summing the rows would count a session many times over. This tool restates the
voice spend both ways and checks which reading the rows themselves support.

It reports, from ``~/.friday/costs.db`` (opened read-only, never migrated):
  * voice spend as stored (the sum of the rows);
  * voice spend under the cumulative hypothesis (per session, the LAST row, and
    the largest row, instead of the sum). Sessions are grouped by ``run_id``
    when every voice row has one, otherwise by contiguous timestamps (a gap of
    ``--gap`` seconds, or a change of model, starts a new session);
  * the share of total spend each way. "Total" is every row's ``cost_usd``;
    local seats and free tools record zero, so it is the cloud spend;
  * a signature check: within a session, how often the response and prompt
    counts rise, fall or stay level. A running total never falls. A per-turn
    figure of a growing context has a prompt that only grows and a response
    that rises and falls;
  * how many rows carry the modality split / cached / tool-use columns.

Only counts, sums and shares are printed. No row, model name, timestamp or
conversation text ever leaves the database.

    python tools/voice_cost_audit.py [--db PATH] [--gap SECONDS] [--days N] [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path.home() / ".friday" / "costs.db"
_NEW_COLS = ("prompt_audio_tokens", "prompt_text_tokens",
             "cached_prompt_tokens", "tool_use_prompt_tokens")


def _open_ro(path):
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError("no cost ledger at the given path")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = 1")
    return conn


def _sessions(rows, gap_s):
    """Group (ts, model, run_id, ...) rows into sessions."""
    if rows and all(r[2] for r in rows):
        by_run = {}
        for r in rows:
            by_run.setdefault(r[2], []).append(r)
        return list(by_run.values()), "run_id"
    out, cur = [], []
    for r in rows:
        if cur and (r[0] - cur[-1][0] > gap_s or r[1] != cur[-1][1]):
            out.append(cur)
            cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    return out, "contiguous timestamps (gap %ds, model change splits)" % gap_s


def _steps(sessions, idx):
    up = down = level = 0
    for s in sessions:
        for a, b in zip(s, s[1:]):
            if b[idx] > a[idx]:
                up += 1
            elif b[idx] < a[idx]:
                down += 1
            else:
                level += 1
    return {"up": up, "down": down, "level": level}


def audit(db_path=DEFAULT_DB, gap_s=300, days=None):
    """``days`` limits every figure to rows newer than that many days."""
    since = (time.time() - days * 86400) if days else 0.0
    conn = _open_ro(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cost_calls)")}
        total = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM cost_calls "
            "WHERE ts > ?", (since,)).fetchone()
        # ts, model, run_id, input, output, cost
        rows = conn.execute(
            "SELECT ts, model, run_id, input_tokens, output_tokens, "
            "COALESCE(cost_usd, 0) FROM cost_calls WHERE kind = 'voice' "
            "AND provider = 'gemini' AND ts > ? ORDER BY ts, id", (since,)).fetchall()
        present = [c for c in _NEW_COLS if c in cols]
        modality = {"columns_in_ledger": present, "rows_with_any": 0}
        if present:
            any_set = " OR ".join(f"{c} IS NOT NULL" for c in present)
            sums = ", ".join(f"COALESCE(SUM({c}), 0)" for c in present)
            n, *vals = conn.execute(
                f"SELECT COUNT(*), {sums} FROM cost_calls WHERE kind = 'voice' "
                f"AND provider = 'gemini' AND ts > ? AND ({any_set})", (since,)).fetchone()
            modality["rows_with_any"] = n
            modality["sums"] = dict(zip(present, vals))
    finally:
        conn.close()

    total_rows, total_usd = int(total[0]), float(total[1])
    stored = sum(r[5] for r in rows)
    sessions, grouping = _sessions(rows, gap_s)
    last = sum(s[-1][5] for s in sessions)
    peak = sum(max(r[5] for r in s) for s in sessions)

    def share(voice_usd):
        denom = total_usd - stored + voice_usd
        return (voice_usd / denom) if denom > 0 else None

    out_steps, in_steps = _steps(sessions, 4), _steps(sessions, 3)
    n_out = sum(out_steps.values()) or 1
    return {
        "window": f"last {days} days" if days else "all rows",
        "ledger_rows": total_rows,
        "total_spend_usd": round(total_usd, 2),
        "voice_rows": len(rows),
        "voice_sessions": len(sessions),
        "session_grouping": grouping,
        "mean_input_tokens_per_voice_row": (
            round(sum(r[3] for r in rows) / len(rows)) if rows else 0),
        "voice_spend_as_stored_usd": round(stored, 2),
        "voice_spend_cumulative_last_row_usd": round(last, 2),
        "voice_spend_cumulative_peak_row_usd": round(peak, 2),
        "share_of_total_as_stored": _r(share(stored)),
        "share_of_total_cumulative_last_row": _r(share(last)),
        "share_of_total_cumulative_peak_row": _r(share(peak)),
        "within_session_steps_response_tokens": out_steps,
        "within_session_steps_prompt_tokens": in_steps,
        "response_tokens_fall_rate": round(out_steps["down"] / n_out, 3),
        "reading": (
            "per-turn: response counts fall as often as they rise, which a "
            "running total cannot do; the stored sum is not a double count"
            if out_steps["down"] / n_out > 0.05 else
            "cumulative signature: response counts almost never fall; the "
            "stored sum probably over-counts"),
        "modality_columns": modality,
    }


def _r(x):
    return None if x is None else round(x, 3)


def render(report):
    lines = ["Cloud voice spend audit (read-only, aggregates only)"]
    for k, v in report.items():
        if isinstance(v, dict):
            lines.append(f"  {k}:")
            lines.extend(f"      {a}: {b}" for a, b in v.items())
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--gap", type=int, default=300,
                    help="seconds of silence that end a session when rows carry no run_id")
    ap.add_argument("--days", type=int, default=None,
                    help="only rows from the last N days (default: all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        report = audit(args.db, args.gap, args.days)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
