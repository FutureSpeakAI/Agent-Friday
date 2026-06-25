"""Unit tests for the tool lifecycle-hook registry (Part B)."""
import pytest

from services import tool_hooks as h


@pytest.fixture(autouse=True)
def _clean_registry():
    # Remove any test hooks before/after so tests don't bleed into each other.
    names = [r["name"] for r in h.list_hooks() if r["name"].startswith("t_")]
    for n in names:
        h.unregister_hook(n)
    h.reset_rate_limiter()
    yield
    for n in [r["name"] for r in h.list_hooks() if r["name"].startswith("t_")]:
        h.unregister_hook(n)


def _ctx(name="read_file", inp=None):
    return h.HookContext(tool_name=name, input=inp or {})


def test_pre_hook_ordering_by_priority():
    order = []
    h.register_pre_hook(lambda c: order.append("b") or h.ALLOW, name="t_b", priority=20)
    h.register_pre_hook(lambda c: order.append("a") or h.ALLOW, name="t_a", priority=10)
    h.run_pre_hooks(_ctx())
    assert order == ["a", "b"]


def test_deny_short_circuits():
    seen = []
    h.register_pre_hook(lambda c: h.DENY("nope"), name="t_deny", priority=10)
    h.register_pre_hook(lambda c: seen.append("ran") or h.ALLOW, name="t_after", priority=20)
    verdict = h.run_pre_hooks(_ctx())
    assert verdict.action == "deny"
    assert verdict.reason == "nope"
    assert verdict.hook == "t_deny"
    assert seen == []   # later hook never ran


def test_modify_rewrites_input():
    h.register_pre_hook(lambda c: h.MODIFY({"path": "/safe"}), name="t_mod", priority=10)
    captured = {}
    h.register_pre_hook(lambda c: captured.update(c.input) or h.ALLOW, name="t_cap", priority=20)
    ctx = _ctx(inp={"path": "/danger"})
    h.run_pre_hooks(ctx)
    assert ctx.input == {"path": "/safe"}
    assert captured == {"path": "/safe"}


def test_non_critical_hook_fails_open():
    def boom(c):
        raise RuntimeError("buggy hook")
    h.register_pre_hook(boom, name="t_boom", priority=10, critical=False)
    verdict = h.run_pre_hooks(_ctx())
    assert verdict.action == "allow"   # buggy hook treated as ALLOW


def test_critical_hook_fails_closed():
    def boom(c):
        raise RuntimeError("crash in gate")
    h.register_pre_hook(boom, name="t_crit", priority=10, critical=True)
    verdict = h.run_pre_hooks(_ctx())
    assert verdict.action == "deny"    # crash in a security gate denies


def test_post_hook_transforms_result():
    h.register_post_hook(lambda c, r: r + "!", name="t_post", priority=10)
    out = h.run_post_hooks(_ctx(), "result")
    assert out == "result!"


def test_post_hook_exception_passthrough():
    def boom(c, r):
        raise ValueError("post boom")
    h.register_post_hook(boom, name="t_pboom", priority=10)
    out = h.run_post_hooks(_ctx(), "kept")
    assert out == "kept"


def test_tool_scoping():
    seen = []
    h.register_pre_hook(lambda c: seen.append(c.tool_name) or h.ALLOW,
                        name="t_scoped", priority=10, tools={"write_file"})
    h.run_pre_hooks(_ctx(name="read_file"))   # not scoped → skipped
    assert seen == []
    h.run_pre_hooks(_ctx(name="write_file"))  # scoped → runs
    assert seen == ["write_file"]


def test_rate_limiter_bucket():
    # 3/min bucket: first 3 allowed, 4th denied.
    assert h.rate_limit_check("t_key", 3)
    assert h.rate_limit_check("t_key", 3)
    assert h.rate_limit_check("t_key", 3)
    assert not h.rate_limit_check("t_key", 3)
    # 0 disables limiting.
    assert h.rate_limit_check("t_unlimited", 0)
