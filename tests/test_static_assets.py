"""Static-asset guard.

A syntax error in app.js takes the entire console down silently - nothing renders and the
only signal is the browser console. That happened once; this test exists so it cannot
happen again unnoticed.
"""
from pathlib import Path

import pytest

from tools.jscheck import check_file, check_source

STATIC = Path(__file__).resolve().parents[1] / "services/gateway/app/static"
JS_FILES = [p for p in sorted(STATIC.rglob("*.js")) if "vendor" not in p.parts]


def test_js_files_exist():
    assert JS_FILES, "no first-party JavaScript found to check"


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_js_is_structurally_valid(path):
    errors = check_file(path)
    assert not errors, "\n".join(errors)


def test_checker_catches_a_newline_inside_a_string():
    """The exact defect that shipped: an escaped newline written as a literal one."""
    bad = 'const why = prompt(\n  "Reason.\nMore text");\n'
    errors = check_source(bad, "bad.js")
    assert errors and "unterminated string" in errors[0]


def test_checker_catches_unbalanced_braces():
    assert check_source("function f() { if (x) { return 1; }\n", "bad.js")


def test_checker_accepts_modern_syntax():
    """Optional chaining, templates, regex and async/await must not trip the scanner."""
    ok = """
    const a = obj?.deep?.value ?? "fallback";
    const t = `multi
    line ${a} template`;
    const re = /_id$|account|^ts$/.test(a);
    async function go() { await fetch(`/api/${a}`); }
    const div = (p, q) => p / q;   // division, not a regex
    """
    assert check_source(ok, "good.js") == []


def test_html_references_only_existing_assets():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    import re
    for ref in re.findall(r'(?:src|href)="(/[^"]+)"', html):
        assert (STATIC / ref.lstrip("/")).exists(), f"index.html references missing {ref}"


def test_no_silent_fallback_session_in_the_console():
    """A rejected /auth/me must send the user back to sign in.

    The console previously fabricated a minimal session when /auth/me failed, which
    rendered as a working app with one module, one dashboard and no tenants - a stale
    token looked exactly like a broken product.
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Never fake a session" in js, "the fallback-session guard comment is gone"
    # The old fabricated object must not come back.
    assert 'me = { role, role_label: role' not in js, \
        "console still fabricates a session when /auth/me fails"
    assert "Your session is no longer valid" in js, "no re-authentication message"


def test_every_element_id_the_js_uses_exists_in_the_html():
    """Catches a whole class of runtime break.

    The Account 360 view died with "Cannot read properties of null" because the JS still
    referenced an input that a filter-bar rewrite had removed. Nothing failed at build
    time; only clicking that one tab revealed it.
    """
    import re
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    declared = set(re.findall(r'id="([^"]+)"', html))
    # Elements the JS creates at runtime rather than declaring in markup.
    # wf-assignee / wf-assign are rendered inside the case-lifecycle template, which is
    # rebuilt on every transition rather than living in index.html.
    # acc-open / acc-add / acc-conclude live in the accountability panel, which is
    # rebuilt from the examination's status on every render - the buttons offered depend
    # on whether it has been started and whether it is concluded.
    runtime_created = {"params", "exits", "bands", "e-id", "e-desc",
                       "wf-assignee", "wf-assign",
                       "acc-open", "acc-add", "acc-conclude",
                       # The referral/recovery panel offers different controls depending
                       # on whether a complaint has been lodged, so its buttons are
                       # rendered with the panel rather than declared in markup.
                       "rec-refer", "rec-update", "rec-add"}

    used = set(re.findall(r'\$\("([^"]+)"\)', js))
    used |= set(re.findall(r'getElementById\("([^"]+)"\)', js))
    missing = sorted(used - declared - runtime_created)
    assert not missing, f"JS references element ids absent from index.html: {missing}"


def test_filter_map_controls_all_exist():
    """Every filter declared in FILTER_MAP must have a control, or it silently never
    applies."""
    import re
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const FILTER_MAP = \[(.*?)\];", js, re.S)
    assert block, "FILTER_MAP not found"
    ids = re.findall(r'\["(f-[a-z0-9]+)"', block.group(1))
    assert len(ids) >= 20, "FILTER_MAP looks truncated"
    declared = set(re.findall(r'id="([^"]+)"', html))
    missing = sorted(set(ids) - declared)
    assert not missing, f"FILTER_MAP references missing controls: {missing}"


def test_saved_view_controls_are_not_buried_in_the_collapsed_panel():
    """Sharing has to be findable without expanding "More filters".

    The share control shipped inside the collapsed #filter-more panel, so the first
    question asked about it was "where is the option for sharing views?" - a feature
    nobody can find is a feature that does not exist.
    """
    import re
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    more = re.search(r'<div class="filter-bar filter-more" id="filter-more" hidden>(.*?)\n            </div>',
                     html, re.S)
    assert more, "#filter-more panel not found - update this test"
    for control in ("saved-views", "save-view", "share-toggle"):
        assert f'id="{control}"' in html, f"{control} missing entirely"
        assert f'id="{control}"' not in more.group(1), \
            f"{control} is hidden inside the collapsed More-filters panel"


def test_all_markup_precedes_the_app_script():
    """Elements app.js binds to at load time must already exist in the document.

    The share dialog was first inserted after <script src="/app.js">, so every handler
    it binds at module scope attached to nothing: the dialog opened but its radio
    buttons were inert. Nothing threw, and the page looked fine.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script_at = html.index('<script src="/app.js">')
    after = html[script_at:]
    import re
    stragglers = re.findall(r'id="([^"]+)"', after)
    assert not stragglers, \
        f"markup declared after app.js loads, so handlers cannot bind: {stragglers}"


def test_no_unescaped_interpolation_reaches_the_dom():
    """Stored XSS, caught by a linter rather than by an auditor.

    The console builds markup with template literals. Anything a user typed - a case
    reason, a staff name, an examination note - executes for every colleague who opens
    the record unless it is escaped on the way in. tools/htmlcheck.py enumerates every
    interpolation inside a markup-bearing template and fails on any that is not visibly
    safe; silencing one requires writing down why it cannot carry user input.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import htmlcheck

    findings = htmlcheck.scan()
    assert not findings, (
        "unescaped interpolation(s) into markup:\n"
        + "\n".join(f"  app.js:{ln}  {expr}" for ln, expr in findings))


def test_the_inline_decision_panel_is_wired_end_to_end():
    """A panel that exists in markup but is never called renders nothing, and the failure
    is silent — which is how the share dialog broke before. Assert the whole chain."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for el in ("lane-panel", "lane-body", "lane-mode", "lane-window"):
        assert f'id="{el}"' in html, f"{el} is missing from the markup"
        assert el in js, f"{el} is never referenced by app.js"
    assert "await loadLanePanel();" in js, "the panel is never loaded"
    assert '"lane-window"' in js and "loadLanePanel" in js


def test_every_css_class_the_lane_panel_uses_is_defined():
    """A class name typo renders an unstyled block rather than an error. So does a CSS
    variable that does not exist — ``var(--accent)`` silently produced a transparent bar
    until this test was written."""
    import re
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    lane_js = js[js.index("// ---- Lane A:"):]
    used = set(re.findall(r'class="(lane-[a-z-]+)"', lane_js))
    used |= set(re.findall(r"class=\"(lane-[a-z-]+)", lane_js))
    missing = sorted(c for c in used if f".{c}" not in css)
    assert not missing, f"lane panel uses undefined CSS classes: {missing}"

    for var in set(re.findall(r"var\((--[a-z0-9-]+)\)", css)):
        assert f"{var}:" in css, f"CSS variable {var} is used but never defined"
