"""Regression: Path.write_text()/read_text() default to the OS locale
encoding, not UTF-8. On this machine that's cp1254 (Turkish Windows
codepage), which silently mangles non-ASCII characters like an em dash
instead of raising — e.g. a cluster name containing " — " came back as
" � " after a write_text()/read_text() round trip with no encoding
argument. Every text read/write of pipeline output must pass
encoding="utf-8" explicitly. This test guards the specific files known to
have hit this.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


def _read_write_text_calls_without_encoding(pyfile: Path) -> list[int]:
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    offending = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("read_text", "write_text")
        ):
            has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
            if not has_encoding:
                offending.append(node.lineno)
    return offending


def test_no_read_text_or_write_text_without_explicit_encoding():
    offenders = {}
    for pyfile in SRC_DIR.glob("*.py"):
        lines = _read_write_text_calls_without_encoding(pyfile)
        if lines:
            offenders[pyfile.name] = lines
    assert not offenders, (
        f"Path.read_text()/write_text() without encoding='utf-8' defaults to the "
        f"OS locale encoding (cp1254 here) and silently mangles non-ASCII output: {offenders}"
    )


def test_roundtrip_em_dash_through_utf8_write_text(tmp_path):
    p = tmp_path / "name.txt"
    original = "DA_18_Sentry — DA_Riftbeast18"
    p.write_text(original, encoding="utf-8")
    assert p.read_text(encoding="utf-8") == original


def _has_call(tree: ast.AST, func_name: str, attr_name: str) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr_name
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == func_name
        ):
            return True
    return False


def _has_print_call(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
        for node in ast.walk(tree)
    )


def test_files_that_print_reconfigure_stdout_to_utf8():
    """Same root cause, different call site: print() also defaults to the OS
    locale encoding (cp1254 here). Confirmed live — cli.py's report for a
    board containing Turkish display-name text and an em dash decoded as
    valid UTF-8 only after main() called sys.stdout.reconfigure(encoding=
    "utf-8"); without it, redirecting/piping the output produced invalid
    UTF-8 bytes (not just a terminal-rendering artifact — verified by
    reading the raw output bytes, not by print()ing them back, since a
    print()-based check has the exact same blind spot)."""
    offenders = []
    for pyfile in SRC_DIR.glob("*.py"):
        tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
        if _has_print_call(tree) and not _has_call(tree, "stdout", "reconfigure"):
            offenders.append(pyfile.name)
    assert not offenders, (
        f"These files call print() but never call sys.stdout.reconfigure(encoding='utf-8') "
        f"in main() — output will be mangled once redirected/piped: {offenders}"
    )
