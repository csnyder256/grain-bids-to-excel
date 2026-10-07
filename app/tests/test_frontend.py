"""Frontend regressions for the shipped vanilla-JS pages.

This project has no JS build step and no JS test runner, so these tests read
the committed scripts directly. They guard one specific defect that shipped in
``results.js``: a click handler whose *source* was interpolated into a
double-quoted HTML attribute. The inlined source carries its own double quotes,
which terminate the attribute early, so the browser received truncated
JavaScript and the button did nothing when clicked.
"""
import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "src" / "bidboard" / "static" / "js"

# An inline event-handler attribute whose value interpolates a JS expression,
# e.g. onclick="(${openFolder.toString()})()".
_INLINE_HANDLER_WITH_INTERPOLATION = re.compile(r'\bon[a-z]+\s*=\s*"[^"\n]*\$\{')

# A handler attribute that inlines a function's own source.
_FUNCTION_SOURCE_INLINE = re.compile(r'\bon[a-z]+\s*=\s*"[^"\n]*\.toString\(\)')


def _scripts() -> list[Path]:
    return sorted(JS_DIR.glob("*.js"))


def test_scripts_are_present():
    assert _scripts(), f"no JS assets found under {JS_DIR}"


def test_no_inline_handler_interpolates_a_js_expression():
    """A handler built from an interpolated expression is parsed as HTML first.

    Regression: ``onclick="(${openFolder.toString()})()"`` put the function's
    source (which contains double quotes) inside a double-quoted attribute, so
    the attribute ended at the first inner quote and the handler never ran.
    Handlers must be attached with addEventListener.
    """
    for path in _scripts():
        source = path.read_text(encoding="utf-8")
        assert not _INLINE_HANDLER_WITH_INTERPOLATION.search(source), (
            f"{path.name}: an inline event-handler attribute interpolates a JS "
            "expression; the value is parsed as HTML and can be truncated. "
            "Attach the handler with addEventListener instead."
        )


def test_no_inline_handler_inlines_a_function_source():
    for path in _scripts():
        source = path.read_text(encoding="utf-8")
        assert not _FUNCTION_SOURCE_INLINE.search(source), (
            f"{path.name}: a function's source is inlined into an inline handler "
            "attribute. Attach the function itself with addEventListener."
        )


def test_results_folder_button_is_wired_with_a_listener():
    """The Results card's 'Open folder' button must stay wired by listener."""
    source = (JS_DIR / "results.js").read_text(encoding="utf-8")
    assert "open-folder" in source, "the Results folder button lost its class hook"
    assert re.search(r'addEventListener\(\s*"click"\s*,\s*openFolder\s*\)', source), (
        "the Results 'Open folder' button must be attached with "
        'addEventListener("click", openFolder)'
    )
