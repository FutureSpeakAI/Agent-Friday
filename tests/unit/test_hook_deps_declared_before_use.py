"""A React hook's dependency array is read while the component renders, so every
name in it must already be declared above the hook in that component. A name
declared further down with `const` is in its temporal dead zone at that moment:
the render throws ("Cannot access 'cards' before initialization") and the
whole workspace shows its error card instead of its content."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "static").glob("*.js"))

HOOK_DEPS = re.compile(r"\b(?:useCallback|useEffect|useMemo|useLayoutEffect)\(")
DECL = re.compile(r"\b(?:const|let)\s+(?:\[([^\]]+)\]|\{([^}]+)\}|([A-Za-z_$][\w$]*))\s*=")
COMPONENT = re.compile(r"(?m)^\s*function\s+([A-Z][\w$]*)\s*\(")


def _close(src: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket matching the one at `i` (quotes, comments and
    template text are skipped well enough for this code base)."""
    depth, j, n = 0, i, len(src)
    while j < n:
        ch = src[j]
        if ch in "'\"`":
            q = ch
            j += 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
        elif src.startswith("//", j):
            j = src.find("\n", j)
            if j < 0:
                return n
        elif src.startswith("/*", j):
            j = src.find("*/", j) + 1
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _names(group: str) -> set[str]:
    return {re.sub(r"[:=].*$", "", p).strip().lstrip(".") for p in group.split(",") if p.strip()}


def violations(src: str) -> list[str]:
    out = []
    for m in COMPONENT.finditer(src):
        # The body opens after the parameter list, which may itself destructure ({ a, b }).
        body_start = src.find("{", _close(src, m.end() - 1, "(", ")"))
        if body_start < 0:
            continue
        body_end = _close(src, body_start, "{", "}")
        body = src[body_start:body_end]
        # Declarations at the component's own level (one brace deep).
        decls = {}
        for d in DECL.finditer(body):
            depth = body[:d.start()].count("{") - body[:d.start()].count("}")
            if depth != 1:
                continue
            for g in d.groups():
                if g:
                    for nm in (_names(g) if ("," in g or ":" in g) else {g.strip()}):
                        decls.setdefault(nm, d.start())
        for h in HOOK_DEPS.finditer(body):
            call_end = _close(body, h.end() - 1, "(", ")")
            call = body[h.end():call_end - 1]
            dm = re.search(r",\s*\[([^\[\]]*)\]\s*$", call)
            if not dm:
                continue
            at = h.end() + dm.start(1)
            for nm in re.findall(r"[A-Za-z_$][\w$]*", dm.group(1)):
                if nm in decls and decls[nm] > at:
                    line = src[:body_start + at].count("\n") + 1
                    out.append("%s: %s lists %r before its declaration (line %d)" % (m.group(1), "hook", nm, line))
    return out


def test_every_hook_dependency_is_declared_above_the_hook():
    bad = []
    for p in SCRIPTS:
        bad += ["%s: %s" % (p.name, v) for v in violations(p.read_text(encoding="utf-8"))]
    assert not bad, "\n".join(bad)


def test_the_rule_catches_a_dependency_declared_below():
    src = ("function Library(){\n  const [s] = useCards();\n"
           "  const on = useCallback(() => cards.length, [cards]);\n"
           "  const cards = s.cards;\n  return on;\n}\n")
    assert violations(src), "a dependency declared below the hook is a render-time error"
    fixed = src.replace("  const cards = s.cards;\n", "").replace("[s] = useCards();\n", "[s] = useCards();\n  const cards = s.cards;\n")
    assert not violations(fixed)
