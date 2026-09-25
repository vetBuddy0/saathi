"""The card rules that live in CSS and JS rather than Python, checked
where they live: WCAG AAA contrast (7:1) computed from the colour tokens
in style.css; no :hover rule on a card; no setTimeout in cards.js.
Sizes are checked in the real renderer by tests/test_cards_panel.py.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "saathi" / "screen" / "static"
STYLE = STATIC / "css" / "style.css"
CARDS_JS = STATIC / "js" / "cards.js"

AAA = 7.0


def _tokens() -> dict[str, str]:
    css = STYLE.read_text()
    root = re.search(r":root\s*\{([^}]*)\}", css)
    assert root, "no :root token block in style.css"
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})", root.group(1)))


def _luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# Every foreground/background pair a card draws, by token name. Adding
# a pair to cards.js means adding it here.
PAIRS = [
    ("card-fg", "card-bg"),  # title, statement
    ("card-accent", "card-bg"),  # option numbers, progress fill on the panel
    ("card-button-fg", "card-button-bg"),  # option label
    ("card-accent", "card-button-bg"),  # option number on its button
    ("card-dismiss-fg", "card-dismiss-bg"),  # "Never mind" / "Okay"
    ("card-bg", "card-accent"),  # an option while pressed (:active)
    ("card-accent", "card-progress-track"),  # the holding bar's fill on its track
    ("card-key-fg", "card-key-bg"),  # a dialpad key (entry)
    ("card-save-fg", "card-save-bg"),  # the Save button (entry)
]


def test_every_card_colour_pair_meets_wcag_aaa():
    tokens = _tokens()
    failures = []
    for fg, bg in PAIRS:
        assert fg in tokens and bg in tokens, (fg, bg)
        ratio = contrast(tokens[fg], tokens[bg])
        if ratio < AAA:
            failures.append(f"--{fg} on --{bg}: {ratio:.1f}:1")
    assert not failures, "below 7:1 -> " + "; ".join(failures)


def test_the_contrast_formula_matches_wcag_reference_values():
    assert abs(contrast("#ffffff", "#000000") - 21.0) < 0.01
    assert abs(contrast("#777777", "#ffffff") - 4.48) < 0.01


def test_cards_have_no_hover_rule():
    css = STYLE.read_text()
    card_rules = [line for line in css.splitlines() if "card" in line and "{" in line]
    assert card_rules, "no card rules found"
    assert not [line for line in card_rules if ":hover" in line]
    # ...and nothing else in the sheet hovers a card selector either.
    assert not re.search(r"\.card[\w_-]*:hover", css)


def _cards_js_code() -> str:
    """cards.js without its comments -- the rules are about what the
    code does, and the comments name the very things they rule out."""
    return "\n".join(
        line for line in CARDS_JS.read_text().splitlines() if not line.lstrip().startswith("//")
    )


def test_cards_js_has_no_timer():
    # "Nothing times out or vanishes on its own."
    code = _cards_js_code()
    assert "setTimeout" not in code
    assert "setInterval" not in code
    assert "requestAnimationFrame" not in code


def test_cards_js_uses_no_component_library_and_no_precision_gesture():
    code = _cards_js_code()
    assert "import " not in code, "cards.js should not import a library"
    for gesture in ("touchmove", "pointermove", "drag", "wheel", "pointerdown"):
        assert gesture not in code.lower(), gesture
    assert 'addEventListener("click"' in code  # tap only
