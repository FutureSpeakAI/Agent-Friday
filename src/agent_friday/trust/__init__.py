"""One local trust graph: people, sources, Friday herself, and a hidden agent kind.

The package wraps the two stores that exist today (`people_graph.py` and
`source_trust_graph.py`) and owns the rules that apply across them:

* people trust stays home: it is never assembled for a cloud model and never
  shown to another principal (`trust.people`);
* every score change is an event in an append-only, hash-chained log, so a
  score can always be explained and rebuilt (`trust.log`);
* the Federation agent kind exists in the schema only, disabled behind two
  switches (`trust.agents`).

Governance never imports this package: trust may add caution, and it can never
clear an approval card (`scripts/check_trust_in_governance.py`).
"""
