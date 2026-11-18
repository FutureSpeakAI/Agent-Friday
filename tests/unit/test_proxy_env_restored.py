"""One test's write to NO_PROXY / no_proxy never reaches the next test (the conftest fixture holds this).

The key gateway writes both spellings when a system proxy is set. The two tests below run in file order: the first
writes the lowercase spelling the way the gateway does, the second proves it is gone.
"""
import os

MARKER = "leaked-by-the-previous-test.invalid"


def test_a_test_may_write_both_spellings_the_way_the_gateway_does():
    os.environ["no_proxy"] = MARKER
    os.environ["NO_PROXY"] = MARKER
    assert os.environ["no_proxy"] == MARKER


def test_the_next_test_does_not_see_that_write():
    assert os.environ.get("no_proxy") != MARKER
    assert os.environ.get("NO_PROXY") != MARKER
