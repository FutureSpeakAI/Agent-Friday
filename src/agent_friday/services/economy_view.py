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
