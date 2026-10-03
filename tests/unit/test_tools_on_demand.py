"""The tools that load on demand stay out of the always-on catalogue and still run.

The always-on catalogue has a token ceiling (tests/unit/test_latency_budget.py).
Each tool in agent.ON_DEMAND_TOOLS that this build registers is reached by name
or query through the loader and by voice, and still runs wherever it is named.
"""
from agent_friday.services import agent
from agent_friday.services import tool_catalogue as tc


def _always_on():
    return {t["name"] for t in agent.CLAUDE_TOOLS if isinstance(t, dict)}


def test_on_demand_tools_are_not_always_on_and_still_run():
    present = [n for n in agent.ON_DEMAND_TOOLS if n in agent.CLAUDE_TOOL_HANDLERS]
    on_demand = {t["name"] for t in agent.WORKSPACE_TOOLS.get("on_demand", [])}
    for n in present:
        assert n not in _always_on(), n + " is in the always-on catalogue"
        assert n in on_demand, n + " has no schema anywhere the loader looks"
        assert n in agent.TOOL_RINGS, n + " lost its ring"


def test_the_loader_hands_an_on_demand_tool_over_by_name():
    for n in agent.ON_DEMAND_TOOLS:
        if n not in agent.CLAUDE_TOOL_HANDLERS:
            continue
        new, _msg = tc.expand(list(agent.CLAUDE_TOOLS), [n], [])
        assert [t["name"] for t in new] == [n], n
