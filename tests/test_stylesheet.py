"""
Rules for app/static/css/keel.css. The motion rules: every animation stops
on its own, and a visitor whose system asks for reduced motion gets none at
all. And no selector is styled in two places by accident: a second rule for
a class another page already uses restyles that page too, as the event log's
`.example` once did to /learn's worked examples. No database needed.
"""

import re
from collections import defaultdict

from app.main import BASE_DIR

CSS = (BASE_DIR / "static" / "css" / "keel.css").read_text(encoding="utf-8")
REDUCED = "@media (prefers-reduced-motion: reduce)"


def test_no_animation_loops_forever():
    assert "infinite" not in CSS
    animations = [
        value.strip()
        for value in re.findall(r"animation:\s*([^;]+);", CSS)
        if not value.strip().startswith("none")
    ]
    assert animations, "keel.css should have the pulse and the spinner"
    for value in animations:
        name, *rest = value.split()
        assert f"@keyframes {name}" in CSS, value
        # the last value in the shorthand is the iteration count
        assert rest and rest[-1].isdigit(), f"{value!r} has no finite iteration count"


def test_reduced_motion_stops_every_animation_and_transition():
    assert CSS.count(REDUCED) == 1
    block = CSS.split(REDUCED, 1)[1]
    assert re.search(r"animation:\s*none\s*!important", block)
    assert re.search(r"transition:\s*none\s*!important", block)
    # the last @media in the file, so no later rule can bring motion back
    assert CSS.rindex("@media") == CSS.index(REDUCED)


MOTION = "/* --- motion ---"
# Selectors the motion section styles again on purpose: each has its look in
# one rule and its transition or animation in the motion section, so all the
# motion is in one place and the reduced-motion block can stop it.
MOTION_REPEATS = {
    "a",
    ".pulse-dot",
    ".site-nav a",
    ".link-action",
    ".btn",
    ".input",
    ".line",
    ".balance-banner",
    ".term",
    ".definition",
    ".definition:popover-open",
    ".definition-close",
    ".toc a",
    ".type-card",
    ".quiz-option summary",
    ".eq-card",
    ".chip",
    ".tx-card",
}
# Other intended repeats, outside the motion section: a rule the selectors
# share, then each one's own, right after it.
SHARED_THEN_OWN = {
    "h1": "the headings' shared margin and colour, then each heading's size",
    "h2": "the headings' shared margin and colour, then each heading's size",
    ".t-side li": "a T-account line's and total's shared layout, then each one's own",
    ".t-total": "a T-account line's and total's shared layout, then each one's own",
}


def _top_level_rules() -> dict[str, list[str]]:
    """Each selector that starts a rule outside any @-rule, and the sections it's in."""
    text = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), CSS, flags=re.S)
    motion_at = CSS.index(MOTION)
    found: dict[str, list[str]] = defaultdict(list)
    stack: list[str] = []
    start = 0
    for i, char in enumerate(text):
        if char == "{":
            prelude = text[start:i].strip()
            if not prelude.startswith("@") and not any(p.startswith("@") for p in stack):
                for selector in prelude.split(","):
                    found[" ".join(selector.split())].append("motion" if i > motion_at else "main")
            stack.append(prelude)
            start = i + 1
        elif char == "}":
            stack.pop()
            start = i + 1
        elif char == ";" and (not stack or stack[-1].startswith("@")):
            start = i + 1
    return found


def test_no_selector_is_styled_twice_by_accident():
    repeated = {sel: where for sel, where in _top_level_rules().items() if len(where) > 1}
    unexpected = {}
    for selector, where in repeated.items():
        if selector in MOTION_REPEATS and sorted(where) == ["main", "motion"]:
            continue
        if selector in SHARED_THEN_OWN and where == ["main", "main"]:
            continue
        unexpected[selector] = where
    assert unexpected == {}, (
        "styled in more than one rule; merge them, rename the new class, or list it above "
        f"with its reason: {unexpected}"
    )
    # and the lists hold nothing that isn't repeated any more
    assert set(MOTION_REPEATS) | set(SHARED_THEN_OWN) <= set(repeated)
