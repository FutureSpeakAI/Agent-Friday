"""No two endpoints may claim the same URL and method.

Flask accepts a duplicate rule without complaint and serves whichever
blueprint registered it first, so the other feature's handler is dead code
that its own tests cannot reach. The chat sidebar's projects and the creative
project manager once both claimed /api/projects this way.
"""
from collections import defaultdict


def test_every_url_and_method_has_exactly_one_endpoint(app):
    owners = defaultdict(set)
    for rule in app.url_map.iter_rules():
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            owners[(rule.rule, method)].add(rule.endpoint)
    clashes = {"%s %s" % (m, r): sorted(eps)
               for (r, m), eps in owners.items() if len(eps) > 1}
    assert not clashes, "URL claimed by more than one endpoint: %s" % clashes
