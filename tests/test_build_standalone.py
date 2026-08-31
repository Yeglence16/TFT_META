import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.build_standalone import (
    BuildError,
    assert_no_script_close,
    build,
    build_embedded_data_blocks,
    embed_logo,
    escape_script_close,
    extract_data_files_mapping,
)

# Smallest possible valid PNG (1x1, transparent) — a well-known fixture
# constant, not a real logo; only its byte-identity round-trip through
# base64 matters for these tests.
_TINY_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

SAMPLE_APP_JS = """"use strict";
const DATA_FILES = {
  meta: "data/meta.json",
  units: "data/units.json",
  compTraits: "data/comp_traits.json",
  selftest: "data/_selftest.json",
};
console.log("hello");
"""

SAMPLE_INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>TFT Comp Analyzer</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<img class="logo-icon" src="logo.png" alt="">
<div id="app"></div>
<script src="app.js" defer></script>
</body>
</html>
"""

SAMPLE_STYLE_CSS = "body { margin: 0; }\n"


def _make_web_dir(
    tmp_path: Path, *, index_html=SAMPLE_INDEX_HTML, app_js=SAMPLE_APP_JS, style_css=SAMPLE_STYLE_CSS, with_logo=True
) -> Path:
    web_dir = tmp_path / "web"
    data_dir = web_dir / "data"
    data_dir.mkdir(parents=True)
    (web_dir / "index.html").write_text(index_html, encoding="utf-8")
    (web_dir / "app.js").write_text(app_js, encoding="utf-8")
    (web_dir / "style.css").write_text(style_css, encoding="utf-8")
    if with_logo:
        (web_dir / "logo.png").write_bytes(_TINY_PNG_BYTES)
    (data_dir / "meta.json").write_text(json.dumps({"set_number": 18}), encoding="utf-8")
    (data_dir / "units.json").write_text(json.dumps({"u1": {"name": "Ahri"}}), encoding="utf-8")
    (data_dir / "comp_traits.json").write_text(json.dumps({"c_00": {}}), encoding="utf-8")
    (data_dir / "_selftest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    return web_dir


# ---------------------------------------------------------------------------
# extract_data_files_mapping
# ---------------------------------------------------------------------------


def test_extract_data_files_mapping_parses_all_entries():
    mapping = extract_data_files_mapping(SAMPLE_APP_JS)
    assert mapping == {
        "meta": "meta.json",
        "units": "units.json",
        "compTraits": "comp_traits.json",
        "selftest": "_selftest.json",
    }


def test_extract_data_files_mapping_raises_when_block_missing():
    with pytest.raises(BuildError):
        extract_data_files_mapping("const SOMETHING_ELSE = {};")


# ---------------------------------------------------------------------------
# escape_script_close / assert_no_script_close
# ---------------------------------------------------------------------------


def test_escape_script_close_breaks_html_tag_boundary_but_stays_valid_json():
    # A pathological display name containing a literal </script> — this is
    # exactly the injection risk build_standalone.py's module docstring
    # describes: embedded raw, this would truncate the page.
    original = 'the name is </SCRIPT><script>alert(1)</script'
    payload = json.dumps({"name": original})
    escaped = escape_script_close(payload)

    assert "</script" not in escaped.lower()
    # Still valid JSON, and decodes back to the ORIGINAL string exactly —
    # \/ is a legal JSON escape for /, so this round-trips.
    assert json.loads(escaped)["name"] == original


def test_escape_script_close_is_case_insensitive():
    escaped = escape_script_close('"</SCRIPT>"')
    assert "</script" not in escaped.lower()


def test_escape_script_close_noop_when_absent():
    payload = json.dumps({"a": 1, "b": "normal string"})
    assert escape_script_close(payload) == payload


def test_assert_no_script_close_passes_on_clean_source():
    assert_no_script_close("body { color: red; }", "web/style.css")  # no raise


def test_assert_no_script_close_raises_on_literal_close_tag():
    with pytest.raises(BuildError):
        assert_no_script_close("const x = '</script>';", "web/app.js")


# ---------------------------------------------------------------------------
# build_embedded_data_blocks
# ---------------------------------------------------------------------------


def test_build_embedded_data_blocks_produces_one_script_per_key(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "meta.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    (data_dir / "units.json").write_text(json.dumps({"y": 2}), encoding="utf-8")

    blocks = build_embedded_data_blocks(data_dir, {"meta": "meta.json", "units": "units.json"})

    assert 'id="embedded-data-meta"' in blocks
    assert 'id="embedded-data-units"' in blocks
    assert blocks.count("<script") == 2


def test_build_embedded_data_blocks_raises_on_missing_file(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with pytest.raises(BuildError):
        build_embedded_data_blocks(data_dir, {"meta": "meta.json"})


# ---------------------------------------------------------------------------
# embed_logo
# ---------------------------------------------------------------------------


def test_embed_logo_replaces_path_with_data_uri(tmp_path):
    web_dir = _make_web_dir(tmp_path)
    html = embed_logo(SAMPLE_INDEX_HTML, web_dir)

    assert 'src="logo.png"' not in html
    assert "data:image/png;base64," in html
    encoded = base64.b64encode(_TINY_PNG_BYTES).decode("ascii")
    assert encoded in html


def test_embed_logo_raises_when_file_missing(tmp_path):
    web_dir = _make_web_dir(tmp_path, with_logo=False)
    with pytest.raises(BuildError):
        embed_logo(SAMPLE_INDEX_HTML, web_dir)


def test_embed_logo_raises_when_attribute_missing(tmp_path):
    web_dir = _make_web_dir(tmp_path)
    html_without_logo_tag = SAMPLE_INDEX_HTML.replace('<img class="logo-icon" src="logo.png" alt="">\n', "")
    with pytest.raises(BuildError):
        embed_logo(html_without_logo_tag, web_dir)


# ---------------------------------------------------------------------------
# build() — end to end on a synthetic web/ dir
# ---------------------------------------------------------------------------


def test_build_embeds_data_style_script_and_logo(tmp_path):
    web_dir = _make_web_dir(tmp_path)
    html = build(web_dir)

    assert '<link rel="stylesheet" href="style.css">' not in html
    assert '<script src="app.js" defer></script>' not in html
    assert 'src="logo.png"' not in html
    assert "data:image/png;base64," in html
    assert "body { margin: 0; }" in html  # style.css inlined
    assert 'const DATA_FILES = {' in html  # app.js inlined
    assert 'id="embedded-data-meta"' in html
    assert 'id="embedded-data-units"' in html
    assert 'id="embedded-data-compTraits"' in html
    assert 'id="embedded-data-selftest"' in html


def test_build_embedded_json_round_trips(tmp_path):
    web_dir = _make_web_dir(tmp_path)
    html = build(web_dir)

    match_start = html.index('id="embedded-data-units">') + len('id="embedded-data-units">')
    match_end = html.index("</script>", match_start)
    embedded_json = html[match_start:match_end]
    assert json.loads(embedded_json) == {"u1": {"name": "Ahri"}}


def test_build_is_idempotent(tmp_path):
    web_dir = _make_web_dir(tmp_path)
    first = build(web_dir)
    second = build(web_dir)
    assert first == second


def test_build_raises_when_data_file_missing(tmp_path):
    web_dir = _make_web_dir(tmp_path)
    (web_dir / "data" / "meta.json").unlink()
    with pytest.raises(BuildError):
        build(web_dir)


def test_build_raises_when_logo_missing(tmp_path):
    web_dir = _make_web_dir(tmp_path, with_logo=False)
    with pytest.raises(BuildError):
        build(web_dir)


def test_build_raises_when_link_tag_missing(tmp_path):
    bad_html = SAMPLE_INDEX_HTML.replace('<link rel="stylesheet" href="style.css">\n', "")
    web_dir = _make_web_dir(tmp_path, index_html=bad_html)
    with pytest.raises(BuildError):
        build(web_dir)


def test_build_raises_when_script_tag_missing(tmp_path):
    bad_html = SAMPLE_INDEX_HTML.replace('<script src="app.js" defer></script>\n', "")
    web_dir = _make_web_dir(tmp_path, index_html=bad_html)
    with pytest.raises(BuildError):
        build(web_dir)


def test_build_raises_on_script_close_in_style_css(tmp_path):
    web_dir = _make_web_dir(tmp_path, style_css="/* </script> */")
    with pytest.raises(BuildError):
        build(web_dir)


def test_build_raises_on_script_close_in_app_js(tmp_path):
    web_dir = _make_web_dir(tmp_path, app_js=SAMPLE_APP_JS + "\n// </script>\n")
    with pytest.raises(BuildError):
        build(web_dir)
