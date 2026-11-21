"""The exchange of test credits for synthetic cash (NS-40.4-11, -12, -13; design:
program/specs/DESIGN_economy_issuer_exchange_resource_2026-11-16.md, Part D).

TEST CREDITS AND SYNTHETIC CASH ONLY. No provider is chosen, no real money moves and no network is
reached: nothing in this package imports a network module (held by scripts/check_exchange_imports.py
and tests/unit/test_exchange_guards.py). A real provider adapter, if one is ever chosen, lives in
its own directory and is the only place that may.
"""
