"""Finding figures in source text stays linear on hostile input.

Source text is untrusted (a fetched article, a feed item). A long run of digits
that ends against a letter made the figure pattern retry every shorter length
from every start, which is quadratic in the run's length.
"""
import time

from agent_friday.services import podcast_quality as pq


def test_a_long_digit_run_against_a_letter_is_scanned_quickly():
    hostile = "0" * 20000 + "a"
    t0 = time.perf_counter()
    pq._FIGURE_RE.findall(hostile)
    assert time.perf_counter() - t0 < 0.5


def test_ordinary_figures_are_still_found():
    found = pq._FIGURE_RE.findall("Sales rose 12% to $4.2B, about 1,200 stores, in 2026.")
    assert {pq._figure_core(f) for f in found} >= {"12", "4.2", "1200", "2026"}


def test_a_figure_is_not_also_found_from_inside_itself():
    assert pq._FIGURE_RE.findall("1,200") == ["1,200"]
