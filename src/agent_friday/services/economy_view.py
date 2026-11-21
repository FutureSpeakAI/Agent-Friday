"""Read-only test-credit views for generated surfaces (roadmap section 41, NS-41.5-4).

Turns a party's balances (stage_escrow.PartyView) into composer blocks. A view only shows; it
holds no button, no verb and no approval slot, so it can never start a spend. TEST CREDITS ONLY.

Rules, each with the test that enforces it (tests/unit/test_economy_view.py):
  the blocks compose into a document the profile accepts:
      test_the_blocks_compose_and_render
  no block can act, link out, or open the approval slot:
      test_the_view_has_only_text_and_status_blocks
  the figures shown equal the balances, in test credit units, nothing else:
      test_the_figures_equal_the_balances
  an empty party shows zero and no stage rows:
      test_an_empty_party_shows_zero_only
  postings show one status block per entry, signed, in test credits:
      test_postings_show_one_status_block_per_entry_with_signed_figures_in_test_credits
  postings and fees add no button, link or slot:
      test_postings_view_has_only_text_and_status_blocks_and_never_an_action
  fees show each line and their total:
      test_fees_show_each_line_and_their_total
  nothing yet says so, never a blank view:
      test_empty_postings_and_fees_say_so_instead_of_showing_a_blank_view
  card-like or control-like text is refused by the profile, not shown:
      test_text_that_looks_like_a_card_or_a_control_is_refused_by_the_profile_not_shown
  the views never change what they were given:
      test_a_view_never_changes_what_it_was_given
  imports only the standard library and sibling services:
      test_the_module_imports_only_the_standard_library_and_sibling_services
"""
from __future__ import annotations

from agent_friday.services.stage_escrow import PartyView

UNIT = "TEST P"


def _line(label: str, amount: int) -> dict:
    return {"kind": "text", "text": f"{label}: {amount} {UNIT}"}


def _rows(label: str, pairs: tuple) -> list[dict]:
    return [_line(f"{label} {name}", amount) for name, amount in pairs]


def balance_blocks(view: PartyView) -> list[dict]:
    rows = [_line("Available", view.available)]
    rows += _rows("In escrow for stage", view.escrow_by_stage)
    rows += _rows("Reserved for sale", view.sale_by_exchange)
    rows += _rows("Held in dispute for stage", view.held_by_stage)
    return [{"kind": "heading", "text": "Test credits"},
            {"kind": "status", "title": "Balances (test credit only)", "children": rows}]


def _signed(amount: int) -> str:
    return f"{amount:+d}"


def posting_blocks(entries: list[dict]) -> list[dict]:
    """Journal entries ({"seq", "effect", "postings": {account: amount}}) as one status block each, newest as given. Read-only:
    the caller reads the journal, this only shows what it was handed."""
    blocks = [{"kind": "heading", "text": "Postings (test credit only)"}]
    if not entries:
        return blocks + [{"kind": "text", "text": "No postings yet."}]
    for entry in entries:
        rows = [{"kind": "text", "text": f"{account}: {_signed(amount)} {UNIT}"}
                for account, amount in sorted(entry["postings"].items())]
        blocks.append({"kind": "status", "title": f"#{entry['seq']} {entry['effect']}", "children": rows})
    return blocks


def fee_blocks(lines: list[dict]) -> list[dict]:
    """Fee lines ({"label", "amount"}) and their total, as text. Read-only."""
    blocks = [{"kind": "heading", "text": "Fees (test credit only)"}]
    if not lines:
        return blocks + [{"kind": "text", "text": "No fees yet."}]
    rows = [_line(line["label"], line["amount"]) for line in lines]
    rows.append(_line("Total fees", sum(line["amount"] for line in lines)))
    return blocks + [{"kind": "status", "title": "Fees", "children": rows}]
