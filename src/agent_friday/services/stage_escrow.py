"""Escrow and sale reservations on the test-credit issuer journal (roadmap section 40,
NS-40.4-7 and NS-40.4-8). TEST CREDITS ONLY.

This module changes no journal. It names the accounts a stage uses, builds the zero-sum
commands `IssuerJournal.execute` already accepts, and reads a balances dict back into the
separate shown parts: available, escrow per stage, reserved for sale per exchange.

Account names (one string each, the journal sees only strings):
  avail:<party>               spendable test credit
  escrow:<stage>:<party>      credit reserved for one stage by its payer
  sale:<exchange>:<party>     credit reserved for one sell order
  held:<stage>:<payee>        a disputed share, kept out of both sides until reconciled

Rules, each with the test that enforces it (tests/unit/test_stage_escrow.py):
  countersigning terms moves nothing:
      test_countersigning_terms_builds_no_command
  reserving moves credit from available to the stage's escrow, once per stage and payer:
      test_reserve_moves_available_into_escrow_once
  a reserve the payer cannot cover is refused by the journal:
      test_a_reserve_beyond_available_is_refused
  an earned share is credited once at the gate, from escrow:
      test_an_earned_share_is_credited_once_from_escrow
  a disputed share is held, not paid:
      test_a_disputed_share_is_held_not_paid
  unused escrow returns to available on cancellation:
      test_cancel_releases_the_unused_escrow
  the view keeps available, escrow and sale reservations apart:
      test_the_view_separates_available_escrow_and_sale
  every command is zero-sum for every small amount:
      test_every_command_is_zero_sum_for_small_amounts
  imports only the standard library:
      test_the_module_imports_only_the_standard_library
"""
from __future__ import annotations

from dataclasses import dataclass


def avail(party: str) -> str:
    return f"avail:{party}"


def escrow(stage: str, party: str) -> str:
    return f"escrow:{stage}:{party}"


def sale(exchange: str, party: str) -> str:
    return f"sale:{exchange}:{party}"


def held(stage: str, payee: str) -> str:
    return f"held:{stage}:{payee}"


def _command(issuer: str, effect: str, action: str, postings: dict) -> dict:
    return {"issuer": issuer, "effect": effect, "action": action, "postings": postings}


def countersign(_terms: dict) -> None:
    """Agreeing to terms is not a payment: no command."""
    return None


def reserve(issuer: str, stage: str, payer: str, amount: int, action: str) -> dict:
    postings = {avail(payer): -amount, escrow(stage, payer): amount}
    return _command(issuer, f"reserve:{stage}:{payer}", action, postings)


def pay_out(issuer: str, stage: str, payer: str, payee: str, amount: int, action: str) -> dict:
    postings = {escrow(stage, payer): -amount, avail(payee): amount}
    return _command(issuer, f"payout:{stage}:{payee}", action, postings)


def hold_disputed(issuer: str, stage: str, payer: str, payee: str, amount: int,
                  action: str) -> dict:
    postings = {escrow(stage, payer): -amount, held(stage, payee): amount}
    return _command(issuer, f"hold:{stage}:{payee}", action, postings)


def cancel(issuer: str, stage: str, payer: str, unused: int, action: str) -> dict:
    postings = {escrow(stage, payer): -unused, avail(payer): unused}
    return _command(issuer, f"cancel:{stage}:{payer}", action, postings)


def reserve_for_sale(issuer: str, exchange: str, seller: str, amount: int, action: str) -> dict:
    postings = {avail(seller): -amount, sale(exchange, seller): amount}
    return _command(issuer, f"salereserve:{exchange}:{seller}", action, postings)


@dataclass(frozen=True)
class PartyView:
    available: int
    escrow_by_stage: tuple
    sale_by_exchange: tuple
    held_by_stage: tuple


def view(balances: dict, party: str) -> PartyView:
    """The party's balances in separate parts. Accounts of other parties are ignored."""
    parts = {"escrow": {}, "sale": {}, "held": {}}
    available = 0
    for account, amount in balances.items():
        kind, _, rest = account.partition(":")
        if kind == "avail" and rest == party:
            available = amount
        elif kind in parts:
            owner_ref, _, owner = rest.rpartition(":")
            if owner == party:
                parts[kind][owner_ref] = amount
    return PartyView(available, tuple(sorted(parts["escrow"].items())),
                     tuple(sorted(parts["sale"].items())), tuple(sorted(parts["held"].items())))
