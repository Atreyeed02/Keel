"""
The motion rules in app/static/css/keel.css: every animation stops on its
own, and a visitor whose system asks for reduced motion gets none at all.
No database needed.
"""

import re

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
