"""SP-6: the page's files. The screens themselves are checked in a real browser (see the SP-6
walkthrough); these keep the things a browser run would not catch from sliding."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "art_speed" / "web"


def _tool():
    spec = importlib.util.spec_from_file_location("build_web_css", ROOT / "tools" / "build_web_css.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scoping_prefixes_every_rule_and_leaves_keyframes_alone():
    css = """/* c */ :root{--a:1} *{box-sizing:border-box} html,body{margin:0}
    body{color:red} button,.x:hover{top:0} @media (max-width:9px){.y{left:0}}
    @keyframes sp{to{transform:rotate(360deg)}}"""
    out = _tool().scope_css(css, ".q")
    assert ".q{--a:1}" in out and ".q *{box-sizing:border-box}" in out
    assert ".q{margin:0}" in out and ".q{color:red}" in out          # html is dropped, body is the scope
    assert ".q button,.q .x:hover{top:0}" in out
    assert "@media (max-width:9px){\n.q .y{left:0}" in out
    assert "@keyframes sp{to{transform:rotate(360deg)}}" in out


def test_the_two_sheets_cannot_touch_each_other():
    """Each sheet has its own button, table, .modal and .tab: every rule must sit under its scope."""
    for name, scope in (("shell.css", ".as"), ("speed.css", ".sv")):
        text = re.sub(r"/\*.*?\*/", "", (WEB / name).read_text(encoding="utf-8"), flags=re.S)
        text = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", text)
        selectors = re.findall(r"(?:^|\})\s*([^{}@]+)\{", text)
        assert selectors, name
        for selector in selectors:
            for part in selector.split(","):
                assert part.strip().startswith(scope), f"{name}: {part.strip()!r} is not under {scope}"
    assert "fonts.googleapis" not in (WEB / "speed.css").read_text(encoding="utf-8")      # no network


def test_the_page_carries_no_mockup_data_and_no_low_n_rule():
    js = (WEB / "speed.js").read_text(encoding="utf-8")
    for leftover in ("174083", "17889013", "HOURLY", "FAST", "n<30", "low n", "Turning Movement", " bands "):
        assert leftover not in js, leftover
    # the file is the finished page script, whole: every function the shell and the page call is there
    assert "//@@" not in js
    for name in ("speedShow", "speedLeave", "speedReset", "renderTab", "renderDrawer", "setupBody", "ovBody",
                 "speedSVG", "decBody", "chart", "detBody", "trailBody", "openM", "closeM"):
        assert f"function {name}(" in js, name
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert page.index("shell.js") < page.index("wizard.js") < page.index("speed.js") < page.index("boot.js")
    assert "function startWizard(" in (WEB / "wizard.js").read_text(encoding="utf-8")


def test_serve_starts_the_app_and_api_is_the_development_server():
    from art_speed.__main__ import build_parser
    args = build_parser().parse_args(["serve", "--no-browser"])
    assert args.project is None and args.port == 8766 and args.no_browser
    args = build_parser().parse_args(["api", "--project", "x"])
    assert args.command == "api" and args.mockup is None
