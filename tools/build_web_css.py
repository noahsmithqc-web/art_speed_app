"""SP-6: build the page's two stylesheets from the two looks it has to match.

    python tools/build_web_css.py <claude_app/art_app/web/styles.css> <speed_tab_mock_v15.html>

Writes into art_speed/web/:

    shell.css   A.R.T.'s stylesheet, every rule scoped under `.as` - the rail, Home, the pop-ups,
                Settings and the green saved banner look exactly as they do in A.R.T.
    speed.css   the <style> block of the locked mockup, every rule scoped under `.sv` - the speed
                tab and its drawer look exactly as they do in the mockup

Both sources are read, never edited. The scopes are what lets the two live on one page: each
sheet has its own `button`, `table`, `.modal`, `.tab` and `--ink`, and neither may leak into the
other. Every change made to a source's text is listed in its PATCHES and must match exactly
once, so a different version of either file cannot be half-converted without anyone noticing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "art_speed" / "web"

# (what it is, text to find, replacement)
ART_PATCHES = [
    ("page scroll rule (the page's own base rule does this)", "  body{overflow-y:scroll}\n", ""),
]
MOCK_PATCHES = [
    ("web font (the tool runs with no network)",
     "@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap');\n", ""),
    ("page base rule", "html,body{margin:0;height:100%}", ""),
    ("scrim under the mockup's title bar", ".scrim{position:fixed;inset:40px 0 0 0;", ".scrim{position:fixed;inset:0;"),
    ("drawer under the mockup's title bar", ".drawer{position:fixed;top:40px;", ".drawer{position:fixed;top:0;"),
]


def patch(text: str, patches: list, source: str) -> str:
    for what, find, replacement in patches:
        if text.count(find) != 1:
            raise SystemExit(f"{source}: patch '{what}' matched {text.count(find)} times, expected 1")
        text = text.replace(find, replacement)
    return text


def scope_selector(selector: str, scope: str) -> str:
    out = []
    for part in selector.split(","):
        part = part.strip()
        if not part or part == "html":
            continue
        if part in ("body", ":root"):
            out.append(scope)
        elif part.startswith("body "):
            out.append(scope + part[4:])
        else:
            out.append(f"{scope} {part}")
    return ",".join(out)


def scope_css(css: str, scope: str) -> str:
    """Prefix every rule's selectors with `scope`. @media blocks are scoped inside; @keyframes
    and other at-rules pass through untouched."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i, n = [], 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            break
        head = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[brace + 1:j - 1]
        if head.startswith("@media"):
            out.append(f"{head}{{\n{scope_css(body, scope)}}}")
        elif head.startswith("@"):
            out.append(f"{head}{{{body}}}")
        else:
            selector = scope_selector(head, scope)
            if selector:
                out.append(f"{selector}{{{' '.join(body.split())}}}")
        i = j
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    art, mock = Path(argv[1]), Path(argv[2])
    art_css = patch(art.read_text(encoding="utf-8"), ART_PATCHES, art.name)
    page = mock.read_text(encoding="utf-8")
    m = re.search(r"<style>\n(.*?)</style>", page, flags=re.S)
    if not m:
        raise SystemExit(f"{mock.name}: no <style> block")
    mock_css = patch(m.group(1), MOCK_PATCHES, mock.name)
    mock_css = "".join(line for line in mock_css.splitlines(keepends=True) if not line.startswith(".mockbar"))
    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "shell.css").write_text(
        "/* A.R.T.'s styles.css scoped under .as - written by tools/build_web_css.py, do not edit by hand. */\n"
        + scope_css(art_css, ".as"), encoding="utf-8", newline="\n")
    (WEB / "speed.css").write_text(
        "/* The <style> block of the locked mockup (speed_tab_mock_v15.html) scoped under .sv - "
        "written by tools/build_web_css.py, do not edit by hand. */\n" + scope_css(mock_css, ".sv"), encoding="utf-8", newline="\n")
    print(f"wrote shell.css and speed.css to {WEB}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
