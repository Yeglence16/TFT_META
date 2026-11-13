"""Packages web/ into a single portable HTML file (dist/tft-comp-analyzer.html).

Motivation: the normal web/ deployment (SPEC-WEB.md section 1) needs a local
HTTP server because `file://` blocks JSON `fetch()` via CORS. That's fine for
the developer, but "send a friend a link to a folder + tell them to run
`python -m http.server`" is a bad ask for casual sharing. This script embeds
everything — data, CSS, JS (the header logo is already an inline SVG) — into one `.html` file
that opens directly via double-click, no server required.

**How app.js supports both modes without a build-time fork.** `app.js`'s
`loadData()` (SPEC-WEB.md section 2) already tries `document.getElementById(
"embedded-data-<key>")` for each of `DATA_FILES`' entries before falling back
to `fetch()`. In the normal `web/index.html`, no such elements exist, so
every lookup misses and it fetches exactly as before — the fetch path is
untouched, this build's existence changes nothing about server mode. This
script's only job is producing a variant of `index.html` where those elements
DO exist (as `<script type="application/json" id="embedded-data-<key>">`
blocks) and where `style.css`/`app.js` are inlined instead of linked.

**Single source of truth for the key->filename mapping.** Rather than
hardcoding a second copy of `DATA_FILES` here (which could silently drift
from `app.js`'s if one side is edited and not the other — exactly the kind
of duplicated-but-not-synced constant this project avoids elsewhere, see
SPEC-WEB.md section 3.1 on `unit_order`), `extract_data_files_mapping()`
parses `app.js`'s actual `DATA_FILES` object literal via regex. If someone
adds/renames/removes a data file in `app.js` without touching this script,
the mapping just follows along automatically; if the regex ever fails to
match (app.js's `DATA_FILES` syntax changed shape), the build fails loudly
instead of silently shipping stale or missing data.

**`</script` injection risk — real, not theoretical, and why it's only
handled automatically for the JSON blocks.** An HTML tokenizer looks for the
literal byte sequence `</script` (case-insensitively) to end a `<script>`
element, regardless of that element's `type` attribute — a unit/item/trait
display name that happened to contain it would truncate the embedded JSON
and break everything downstream, silently. `escape_script_close()` rewrites
`</script` to `<\\/script` before embedding JSON: this breaks the HTML
tokenizer's match (extra backslash) while remaining valid JSON, since `\\/`
is JSON's optional escape for `/` and decodes back to the exact original
string. The same blind rewrite would NOT be safe applied to `web/app.js` or
`web/style.css` themselves — inserting a stray backslash into arbitrary CSS
or executable JS could change its meaning instead of just neutralizing a tag
boundary. Since a `</script` substring in the project's own source is a
should-never-happen event (verified absent — see `assert_no_script_close()`),
those two get a hard build-time check instead of a silent rewrite: fail
loudly and name the file, not guess.

**Idempotent by construction.** Every step here is a deterministic function
of already-on-disk content (`web/index.html` + `web/style.css` + `web/app.js`
+ `web/data/*.json`, all read-only) — no timestamps, no random ordering, no
non-deterministic dict/set iteration. Running this twice against unchanged
input produces byte-identical output (see tests/test_build_standalone.py's
idempotency test); a diff'able build lets `git status`/CI notice a real
content change instead of noise on every run.

Usage: `python src/build_standalone.py` — reads `web/`, writes
`dist/tft-comp-analyzer.html` (gitignored — a build artifact, not source).
Requires web/data/*.json to already exist (run build_web_data.py first).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DIST_DIR, WEB_DIR

OUTPUT_PATH = DIST_DIR / "tft-comp-analyzer.html"

# app.js's `const DATA_FILES = { key: "data/filename.json", ... };` — see
# extract_data_files_mapping()'s docstring for why this is parsed out of
# app.js rather than duplicated here.
_DATA_FILES_BLOCK_RE = re.compile(r"const DATA_FILES = \{(.*?)\};", re.DOTALL)
_DATA_FILES_ENTRY_RE = re.compile(r'(\w+):\s*"data/([\w.]+)"')

_LINK_TAG = '<link rel="stylesheet" href="style.css">'
_SCRIPT_TAG = '<script src="app.js" defer></script>'


class BuildError(RuntimeError):
    """web/'s actual content doesn't match what this build script expects."""


def extract_data_files_mapping(app_js_source: str) -> dict[str, str]:
    """Parses `{key: filename}` out of app.js's `DATA_FILES` literal (e.g.
    `{"meta": "meta.json", "compTraits": "comp_traits.json", ...}`) instead
    of hardcoding a second copy — see module docstring."""
    match = _DATA_FILES_BLOCK_RE.search(app_js_source)
    if not match:
        raise BuildError(
            "web/app.js: DATA_FILES object literal not found — its shape "
            "changed and this script's regex needs updating to match"
        )
    mapping = dict(_DATA_FILES_ENTRY_RE.findall(match.group(1)))
    if not mapping:
        raise BuildError("web/app.js: DATA_FILES matched but contained no entries")
    return mapping


def escape_script_close(text: str) -> str:
    """`</script` (case-insensitive) -> `<\\/script`. Safe for JSON text
    specifically: `\\/` is a valid (optional) JSON escape for `/`, so this
    round-trips through JSON.parse() to the original string, while no longer
    matching the HTML tokenizer's script-end token. See module docstring —
    do NOT reuse this on raw CSS/JS, use assert_no_script_close() there."""
    # Replacement is a function, not a fixed string, so whatever casing
    # actually matched (</script>, </SCRIPT>, </Script>, ...) is preserved
    # in the output — only the backslash is inserted.
    return re.sub(r"</(script)", lambda m: f"<\\/{m.group(1)}", text, flags=re.IGNORECASE)


def assert_no_script_close(source: str, label: str) -> None:
    """CSS/JS embed as executable/style code, not as a JSON string — blindly
    inserting a backslash to dodge `</script` could change what the code
    DOES, not just neutralize a tag boundary (unlike JSON's `\\/` escape).
    So for these two, a literal `</script` fails the build instead of being
    silently rewritten. Verified absent from the real web/app.js and
    web/style.css at the time this was written; this only ever fires if
    that stops being true."""
    if re.search(r"</script", source, re.IGNORECASE):
        raise BuildError(
            f"{label} contains a literal '</script' substring — embedding it verbatim inside "
            "an HTML <script> tag would truncate the page at that point. This can't be "
            "auto-escaped safely for executable code (unlike the JSON data blocks); fix the "
            "source file directly."
        )


def build_embedded_data_blocks(data_dir: Path, mapping: dict[str, str]) -> str:
    blocks = []
    for key, filename in mapping.items():
        path = data_dir / filename
        if not path.exists():
            raise BuildError(f"{path} not found — run build_web_data.py first")
        data = json.loads(path.read_text(encoding="utf-8"))
        # Compact separators: this is meant to be emailed/shared as a single
        # file, no reason to ship pretty-printed JSON's extra whitespace.
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        safe = escape_script_close(compact)
        blocks.append(f'<script type="application/json" id="embedded-data-{key}">{safe}</script>')
    return "\n".join(blocks)


def assert_no_local_asset_refs(html: str) -> None:
    """The output is a single file with no siblings, so any remaining
    relative `src=`/`href=` (a local image, stylesheet, script) would 404
    under `file://`. Not hypothetical: the header used to be a local
    `logo.png`, and the first live `file://` test of this build hit exactly
    that (`net::ERR_FILE_NOT_FOUND`). The logo is now an inline SVG, so
    nothing needs embedding today — this guard makes a future local asset
    fail the build loudly instead of shipping a silently broken page."""
    local = re.findall(r'(?:src|href)="(?!https?:|data:|#)([^"]+)"', html)
    if local:
        raise BuildError(
            f"standalone output still references local files {sorted(set(local))} — "
            "they won't exist next to the single HTML file; inline them or use a remote URL"
        )


def build(web_dir: Path) -> str:
    """Returns the standalone HTML as a string — pure function of web_dir's
    current on-disk content, no I/O beyond reading. Caller writes the file."""
    index_html = (web_dir / "index.html").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")

    assert_no_script_close(style_css, "web/style.css")
    assert_no_script_close(app_js, "web/app.js")

    if _LINK_TAG not in index_html:
        raise BuildError(f"web/index.html: expected tag not found: {_LINK_TAG!r}")
    if _SCRIPT_TAG not in index_html:
        raise BuildError(f"web/index.html: expected tag not found: {_SCRIPT_TAG!r}")

    mapping = extract_data_files_mapping(app_js)
    data_blocks = build_embedded_data_blocks(web_dir / "data", mapping)

    html = index_html.replace(_LINK_TAG, f"<style>\n{style_css}\n</style>")
    # `defer` has no meaningful effect on an inline script anyway, and this
    # tag sits at the same position (end of body) it always did, so the
    # embedded data blocks (parsed into the DOM immediately before it) are
    # already present by the time this script runs — same ordering
    # guarantee `defer` was providing for the external file, achieved here
    # by document position instead.
    html = html.replace(_SCRIPT_TAG, f"{data_blocks}\n<script>\n{app_js}\n</script>")
    assert_no_local_asset_refs(html)
    return html


def main() -> None:
    # print() below defaults to the OS locale encoding (cp1254 here) absent
    # this — same root cause as tests/test_encoding.py's other cases.
    sys.stdout.reconfigure(encoding="utf-8")
    html = build(WEB_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    size = OUTPUT_PATH.stat().st_size
    print(f"Wrote {OUTPUT_PATH} ({size:,} bytes / {size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
