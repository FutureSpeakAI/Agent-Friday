"""
Demo-readiness UI audit for Agent Friday Desktop.

This is a *probe*, not a pass/fail suite. It explores the running UI at
http://localhost:3000 and writes a structured report to
tests/audit_screenshots/audit_report.json plus a folder of screenshots.

Robustness rules:
  - Every interactive step is wrapped in try/except — never crash mid-audit.
  - Auto-hidden chrome (top bar) is revealed before interacting.
  - Force-clicks are used where elements may be transformed off-screen.

Run:
    pytest tests/test_ui_audit.py -v -s
    # or
    python tests/test_ui_audit.py
"""
from __future__ import annotations

import json
import re
import time
import traceback
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright, TimeoutError as PWTimeout


BASE_URL = "http://localhost:3000"
OUT = Path(__file__).parent / "audit_screenshots"
OUT.mkdir(parents=True, exist_ok=True)
REPORT_PATH = OUT / "audit_report.json"


# ───────────────────────── helpers ─────────────────────────
def _luminance(r, g, b):
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast_ratio(rgb1, rgb2):
    L1 = _luminance(*rgb1)
    L2 = _luminance(*rgb2)
    if L1 < L2:
        L1, L2 = L2, L1
    return (L1 + 0.05) / (L2 + 0.05)


def _parse_rgb(s):
    if not s:
        return None
    m = re.match(r"rgba?\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)(?:,\s*([0-9.]+))?\)", s)
    if not m:
        return None
    r, g, b = int(float(m.group(1))), int(float(m.group(2))), int(float(m.group(3)))
    a = float(m.group(4)) if m.group(4) else 1.0
    return (r, g, b, a)


def _blend(fg, bg):
    fr, fgc, fb, fa = fg
    br, bgc, bb, _ = bg
    return (
        int(fr * fa + br * (1 - fa)),
        int(fgc * fa + bgc * (1 - fa)),
        int(fb * fa + bb * (1 - fa)),
    )


def _shot(page, name):
    p = OUT / f"{name}.png"
    try:
        page.screenshot(path=str(p), full_page=False, timeout=8000)
        return str(p)
    except Exception as e:
        return f"FAILED: {e}"


def _save(payload):
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _reveal_topbar(page):
    """Top bar auto-hides after 3s — move the mouse to the very top to reveal it."""
    try:
        page.mouse.move(960, 2)
        page.wait_for_timeout(450)
    except Exception:
        pass


def _reveal_dock(page):
    """Dock auto-hides after 3s — move the mouse near the bottom edge to reveal it."""
    try:
        page.mouse.move(960, 1070)
        page.wait_for_timeout(450)
    except Exception:
        pass


def _safe(fn, *a, **kw):
    """Run a probe step; never raise."""
    try:
        return fn(*a, **kw)
    except Exception as e:
        return {"_error": str(e)[:200], "_trace": traceback.format_exc().splitlines()[-3:]}


# ───────────────────────── audit ─────────────────────────
def run_audit():
    report = {
        "url": BASE_URL,
        "timestamp": time.time(),
        "issues": [],
        "screenshots": [],
        "workspaces": {},
        "contrast_violations": [],
        "console_errors": [],
        "a11y": {},
        "notifications": {},
        "top_bar_buttons": [],
        "glassmorphism": {},
        "ux_observations": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            permissions=["microphone", "camera"],
        )
        page = ctx.new_page()

        def on_console(m):
            if m.type in ("error", "warning"):
                report["console_errors"].append({"level": m.type, "text": m.text})
        page.on("console", on_console)
        page.on("pageerror", lambda e: report["console_errors"].append({
            "level": "pageerror", "text": str(e)
        }))

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".dock, input[placeholder*='Ask Friday']", timeout=15000)
            page.wait_for_timeout(2200)
        except Exception as e:
            report["issues"].append({"severity": "critical", "area": "load",
                                     "msg": f"Page load failed: {e}"})
            _save(report)
            browser.close()
            return report

        report["screenshots"].append(_shot(page, "00_initial_load"))

        # ─────────── UX observation: top-bar auto-hide ───────────
        _reveal_topbar(page)
        report["screenshots"].append(_shot(page, "01_topbar_revealed"))
        topbar_hidden = page.evaluate("""() => {
            const b = document.querySelector('.top-bar');
            if (!b) return null;
            return b.classList.contains('hidden');
        }""")
        # Auto-hide CAN be a UX trap — flag so we know about it
        report["ux_observations"].append({
            "area": "top_bar",
            "msg": f"Top bar visibility: hidden={topbar_hidden} (expected False — top bar should stay pinned for demo).",
        })

        # ─────────── Glassmorphism scan ───────────
        glass = _safe(page.evaluate, """() => {
            const all = document.querySelectorAll('*');
            const filters = new Set();
            let count = 0;
            for (const el of all) {
                const s = getComputedStyle(el);
                const f = s.backdropFilter || s.webkitBackdropFilter;
                if (f && f !== 'none') { filters.add(f); count++; }
            }
            return { uniqueFilters: [...filters].slice(0, 8), elementsWithFilter: count };
        }""")
        report["glassmorphism"] = glass

        # ─────────── Contrast audit (every visible leaf text node) ───────────
        text_nodes = _safe(page.evaluate, """() => {
            const out = [];
            const all = document.querySelectorAll('body *');
            for (const el of all) {
                if (el.children.length > 0) continue;
                const txt = (el.innerText || el.textContent || '').trim();
                if (!txt || txt.length > 200) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 4 || r.height < 4) continue;
                if (r.top < -10 || r.left < -10 || r.top > 1080 || r.left > 1920) continue;
                const s = getComputedStyle(el);
                if (s.visibility === 'hidden' || s.display === 'none') continue;
                if (parseFloat(s.opacity) < 0.1) continue;
                let bg = s.backgroundColor;
                let p = el.parentElement;
                while (p && (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent')) {
                    bg = getComputedStyle(p).backgroundColor;
                    p = p.parentElement;
                }
                out.push({
                    tag: el.tagName, cls: (el.className || '').toString().slice(0, 60),
                    text: txt.slice(0, 80), color: s.color, bg, fontSize: s.fontSize,
                    opacity: s.opacity
                });
                if (out.length > 1500) break;
            }
            return out;
        }""")

        if isinstance(text_nodes, list):
            violations = []
            tiny = []
            # Emoji ranges: text inside these renders with the system emoji font,
            # not the CSS color, so the contrast formula doesn't apply.
            def _is_emoji_only(t):
                if not t:
                    return False
                stripped = t.strip()
                if not stripped:
                    return False
                for ch in stripped:
                    cp = ord(ch)
                    in_emoji = (
                        0x1F300 <= cp <= 0x1FAFF or
                        0x2600 <= cp <= 0x27BF or
                        0x1F1E6 <= cp <= 0x1F1FF or
                        cp in (0xFE0F, 0x200D)
                    )
                    if not in_emoji and not ch.isspace():
                        return False
                return True
            for n in text_nodes:
                if _is_emoji_only(n.get("text", "")):
                    continue
                fg = _parse_rgb(n["color"])
                bg = _parse_rgb(n.get("bg") or "") or (10, 14, 26, 1.0)
                if not fg:
                    continue
                fg_blend = _blend(fg, bg) if fg[3] < 1.0 else (fg[0], fg[1], fg[2])
                ratio = _contrast_ratio(fg_blend, (bg[0], bg[1], bg[2]))
                try:
                    fs = float((n["fontSize"] or "12px").rstrip("px"))
                except Exception:
                    fs = 12.0
                threshold = 3.0 if fs >= 18 else 4.5
                if ratio < threshold:
                    violations.append({
                        "text": n["text"], "color": n["color"], "bg": n["bg"],
                        "ratio": round(ratio, 2), "fontSize": n["fontSize"],
                        "threshold": threshold, "cls": n["cls"],
                    })
                if fs < 10:
                    tiny.append(n)
            report["contrast_violations"] = violations[:80]
            report["contrast_violation_count_total"] = len(violations)
            report["tiny_text_count"] = len(tiny)
            report["tiny_text_examples"] = [t["text"][:50] for t in tiny[:6]]
            if violations:
                report["issues"].append({"severity": "high", "area": "contrast",
                    "msg": f"{len(violations)} text nodes below WCAG-AA contrast"})
            if tiny:
                report["issues"].append({"severity": "medium", "area": "tiny_text",
                    "msg": f"{len(tiny)} text nodes under 10px"})
        else:
            report["contrast_violations"] = []

        # ─────────── Top-bar buttons inventory ───────────
        _reveal_topbar(page)
        header_log = []
        try:
            for i, b in enumerate(page.locator(".top-bar button").all()):
                title = (b.get_attribute("title") or "").strip()
                txt = (b.inner_text(timeout=600) or "").strip()
                aria = (b.get_attribute("aria-label") or "").strip()
                header_log.append({"idx": i, "title": title, "text": txt[:30], "aria": aria,
                                   "accessible_name": title or aria or txt or "(none)"})
        except Exception as e:
            report["console_errors"].append({"level": "audit", "text": f"top-bar enum: {e}"})
        report["top_bar_buttons"] = header_log

        # ─────────── Notification bell ───────────
        _reveal_topbar(page)
        bell = page.locator(".top-bar button[title*='otification' i]").first
        bell_info = {"present": bell.count() > 0}
        if bell.count() > 0:
            try:
                bell.scroll_into_view_if_needed(timeout=1500)
                bell.click(force=True, timeout=3000)
                page.wait_for_timeout(700)
                report["screenshots"].append(_shot(page, "02_notif_dropdown"))
                dd = page.locator(".notif-dropdown").first
                if dd.count() > 0 and dd.is_visible():
                    bell_info["dropdown_visible"] = True
                    # measure clickability of items
                    rows = dd.locator("> div").all()
                    bell_info["section_count"] = len(rows)
                    with_pointer = 0
                    items_in_dropdown = dd.locator("div[style*='borderBottom']").all()
                    for row in items_in_dropdown:
                        try:
                            cur = row.evaluate("el => getComputedStyle(el).cursor")
                            if cur == "pointer":
                                with_pointer += 1
                        except Exception:
                            pass
                    bell_info["notif_items_total"] = len(items_in_dropdown)
                    bell_info["notif_items_with_cursor_pointer"] = with_pointer
                    bell_info["empty_states"] = dd.locator(".notif-empty").count()
                    if len(items_in_dropdown) > 0 and with_pointer == 0:
                        report["issues"].append({
                            "severity": "high", "area": "notifications",
                            "msg": "Notification items lack cursor:pointer — appear unclickable",
                        })
                else:
                    bell_info["dropdown_visible"] = False
                    report["issues"].append({"severity": "high", "area": "notifications",
                        "msg": "Bell clicked but dropdown not visible"})
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception as e:
                bell_info["error"] = str(e)[:200]
                report["issues"].append({"severity": "high", "area": "notifications",
                    "msg": f"Bell click failed: {e}"})
        else:
            report["issues"].append({"severity": "critical", "area": "notifications",
                "msg": "Bell button not found"})
        report["notifications"] = bell_info

        # ─────────── Walk every dock workspace ───────────
        _reveal_dock(page)
        dock_hidden = page.evaluate("""() => {
            const d = document.querySelector('.dock');
            return d ? d.classList.contains('hidden') : null;
        }""")
        report["ux_observations"].append({
            "area": "dock",
            "msg": f"Dock auto-hides after 30s of mouse inactivity (currently hidden={dock_hidden}). Mouse near bottom 100px reveals it.",
        })
        dock_btns = page.locator(".dock .dock-btn").all()
        report["dock_btn_count"] = len(dock_btns)
        ws_results = {}
        for i, btn in enumerate(dock_btns):
            label = ""
            try:
                txt = (btn.inner_text(timeout=1000) or "").strip()
                lines = [l for l in txt.splitlines() if l.strip()]
                label = lines[-1] if lines else f"btn_{i}"
            except Exception:
                label = f"btn_{i}"
            safe_name = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or f"btn_{i}"
            ws = {"label": label, "opened": False, "body_len": 0,
                  "inner_btns": 0, "empty": False, "errors": []}
            try:
                _reveal_dock(page)  # keep dock visible between iterations
                btn.click(timeout=3000, force=True)
                page.wait_for_timeout(1500)  # let async fetches resolve
                win = page.locator(".fwin").last
                if win.count() > 0:
                    ws["opened"] = True
                    body = win.locator(".fwin-body")
                    if body.count() > 0:
                        # Let lazy fetches resolve before measuring.
                        page.wait_for_timeout(1200)
                        try:
                            ws["inner_btns"] = body.locator("button").count()
                        except Exception:
                            pass
                        try:
                            text = (body.inner_text(timeout=3000) or "").strip()
                            ws["body_len"] = len(text)
                            ws["body_preview"] = text[:300]
                            if len(text) < 25 and ws.get("inner_btns", 0) == 0:
                                ws["empty"] = True
                                report["issues"].append({"severity": "medium",
                                    "area": "empty_workspace",
                                    "msg": f"Workspace '{label}' appears empty (body <25 chars, no buttons)"})
                        except Exception as e:
                            ws["errors"].append(f"body text: {e}"[:120])
                    report["screenshots"].append(_shot(page, f"03_ws_{safe_name}"))
                    # close
                    try:
                        close = win.locator(".fwin-btns button").first
                        if close.count() > 0:
                            close.click(timeout=1500)
                            page.wait_for_timeout(250)
                    except Exception:
                        pass
            except Exception as e:
                ws["errors"].append(str(e)[:120])
            ws_results[label] = ws
        report["workspaces"] = ws_results

        # ─────────── Accessibility audit ───────────
        a11y = _safe(page.evaluate, """() => {
            const out = {
                btns_total: 0, btns_bare: 0,
                inputs_total: 0, inputs_no_label: 0,
                examples_bare_btns: [], examples_bare_inputs: [],
                tabbable: 0
            };
            for (const b of document.querySelectorAll('button')) {
                out.btns_total++;
                const txt = (b.innerText || '').trim();
                const t = b.getAttribute('title') || '';
                const al = b.getAttribute('aria-label') || '';
                if (!txt && !t && !al) {
                    out.btns_bare++;
                    if (out.examples_bare_btns.length < 6)
                        out.examples_bare_btns.push(b.outerHTML.slice(0, 140));
                }
            }
            for (const inp of document.querySelectorAll('input,textarea,select')) {
                out.inputs_total++;
                const id = inp.id;
                const al = inp.getAttribute('aria-label') || '';
                const ph = inp.getAttribute('placeholder') || '';
                const lbl = id ? document.querySelector(`label[for="${id}"]`) : null;
                if (!lbl && !al && !ph) {
                    out.inputs_no_label++;
                    if (out.examples_bare_inputs.length < 4)
                        out.examples_bare_inputs.push(inp.outerHTML.slice(0, 140));
                }
            }
            for (const el of document.querySelectorAll('[tabindex]')) {
                if (el.tabIndex >= 0) out.tabbable++;
            }
            return out;
        }""")
        if isinstance(a11y, dict) and "btns_total" in a11y:
            report["a11y"] = a11y
            if a11y["btns_total"] > 0:
                bare_ratio = a11y["btns_bare"] / a11y["btns_total"]
                if bare_ratio > 0.20:
                    report["issues"].append({"severity": "medium", "area": "a11y_buttons",
                        "msg": f"{a11y['btns_bare']}/{a11y['btns_total']} buttons lack name ({bare_ratio:.0%})"})

        # ─────────── Focus indicator probe ───────────
        present_outline = 0
        absent_outline = 0
        for _ in range(10):
            page.keyboard.press("Tab")
            page.wait_for_timeout(60)
            ring = _safe(page.evaluate, """() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const s = getComputedStyle(el);
                const hasOutline = s.outlineWidth !== '0px' && s.outlineStyle !== 'none';
                const hasShadow = s.boxShadow && s.boxShadow !== 'none';
                return { hasOutline, hasShadow };
            }""")
            if isinstance(ring, dict):
                if ring.get("hasOutline") or ring.get("hasShadow"):
                    present_outline += 1
                else:
                    absent_outline += 1
        report["a11y"]["focus_present"] = present_outline
        report["a11y"]["focus_absent"] = absent_outline
        if absent_outline > present_outline and (present_outline + absent_outline) > 0:
            report["issues"].append({"severity": "medium", "area": "focus_indicators",
                "msg": f"Focus rings missing on {absent_outline}/{present_outline + absent_outline} tabbed elements"})

        # ─────────── Chat panel ───────────
        _reveal_topbar(page)
        chat_btn = page.locator(".top-bar button[title*='chat' i], .top-bar button[title*='ssistant' i]").first
        if chat_btn.count() > 0:
            try:
                chat_btn.click(force=True, timeout=2000)
                page.wait_for_timeout(700)
                report["screenshots"].append(_shot(page, "04_chat_panel"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass

        # ─────────── Settings panel ───────────
        _reveal_topbar(page)
        settings = page.locator(".top-bar button[title='Settings']").first
        if settings.count() > 0:
            try:
                settings.click(force=True, timeout=2000)
                page.wait_for_timeout(900)
                report["screenshots"].append(_shot(page, "05_settings"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass

        # ─────────── Quick Draft ───────────
        _reveal_topbar(page)
        qd = page.locator(".top-bar button[title='Quick Draft']").first
        if qd.count() > 0:
            try:
                qd.click(force=True, timeout=2000)
                page.wait_for_timeout(700)
                report["screenshots"].append(_shot(page, "06_quick_draft"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass

        # ─────────── Final overview ───────────
        page.mouse.move(960, 540)
        page.wait_for_timeout(400)
        report["screenshots"].append(_shot(page, "99_final"))

        browser.close()

    _save(report)
    print("\n=== AUDIT COMPLETE ===")
    print(f"Issues: {len(report['issues'])}")
    for issue in report["issues"]:
        print(f"  [{issue['severity']}] {issue['area']}: {issue['msg']}")
    print(f"UX observations: {len(report['ux_observations'])}")
    for obs in report["ux_observations"]:
        print(f"  [obs] {obs['area']}: {obs['msg']}")
    print(f"Report -> {REPORT_PATH}")
    return report


def test_demo_readiness_audit():
    report = run_audit()
    crit = [i for i in report["issues"] if i["severity"] == "critical"]
    if crit:
        pytest.fail("Critical:\n" + "\n".join(c["msg"] for c in crit))


if __name__ == "__main__":
    run_audit()
