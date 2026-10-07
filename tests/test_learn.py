"""
The Learn page and the terms the other pages define in place
(app/glossary.py, the `term` macro in templates/_ui.html).

All but the last test need no database: /learn is fixed text.
"""

import os
import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema import metadata
from app.glossary import GLOSSARY
from app.main import BASE_DIR, app, templates
from tests.support import reset_schema

CSS = (BASE_DIR / "static" / "css" / "keel.css").read_text(encoding="utf-8")


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


class _NoDatabase:
    def begin(self):
        raise AssertionError("/learn should not touch the database")

    connect = begin


@pytest.fixture
async def learn(monkeypatch):
    monkeypatch.setattr("app.main.engine", _NoDatabase())
    async with _client() as client:
        response = await client.get("/learn")
    assert response.status_code == 200
    return response.text


def _ids(html: str) -> list[str]:
    return re.findall(r'\sid="([^"]+)"', html)


async def test_every_term_has_its_section_on_learn(learn):
    ids = set(_ids(learn))
    assert {term.learn for term in GLOSSARY.values()} <= ids


async def test_the_contents_list_points_at_sections_that_exist(learn):
    contents = learn.split('aria-label="On this page"', 1)[1].split("</nav>", 1)[0]
    anchors = re.findall(r'href="#([^"]+)"', contents)
    assert len(anchors) == 8
    assert set(anchors) <= set(_ids(learn))


async def test_learn_has_no_duplicate_ids(learn):
    ids = _ids(learn)
    assert len(ids) == len(set(ids))


async def test_the_quiz_has_exactly_one_right_answer(learn):
    answers = re.findall(r'class="quiz-option" data-answer="(\w+)"', learn)
    assert sorted(answers) == ["right", "wrong", "wrong", "wrong"]


def test_a_term_is_a_button_that_opens_its_card_and_is_described_by_it():
    html = templates.env.from_string(
        '{% import "_ui.html" as ui %}Equal {{ ui.term("debit", "debits") }}.'
    ).render()
    assert (
        '<button type="button" class="term term-debit" popovertarget="def-debit" '
        'aria-describedby="def-debit-text">debits</button>'
    ) in html
    assert '<span id="def-debit" class="definition definition-debit" popover>' in html
    assert f'id="def-debit-text">{GLOSSARY["debit"].definition}</span>' in html
    assert 'href="/learn#debit"' in html
    assert 'popovertarget="def-debit" popovertargetaction="hide">Close</button>' in html
    # no whitespace between the term and what follows it
    assert html.endswith("</span></span>.")


def test_every_term_anchors_its_own_card():
    for key in GLOSSARY:
        assert f".term-{key} {{ anchor-name: --term-{key}; }}" in CSS, key
        assert f".definition-{key} {{ position-anchor: --term-{key}; }}" in CSS, key


# --- the pages that use terms (Postgres-backed) -------------------------------------


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


async def test_each_page_defines_its_terms_once_and_every_reference_resolves(database):
    """
    A term's ids are per term, so a page that defined one twice would open
    the wrong card, or none. Every popovertarget and aria-describedby must
    name an element on the same page. One account of each type, so the
    overview shows every type's group row.
    """
    async with _client() as client:
        for name, account_type in (
            ("Cash", "asset"),
            ("Loan", "liability"),
            ("Capital", "equity"),
            ("Fees", "revenue"),
            ("Rent", "expense"),
        ):
            await client.post(
                "/accounts", data={"name": name, "account_type": account_type, "currency": "USD"}
            )
        pages = {
            path: (await client.get(path)).text
            for path in ("/", "/transactions", "/post-transaction", "/event-log", "/accounts/new")
        }

    expected = {
        # every term but "event", which only the demo's "try it" steps use
        # (tests/test_overview.py)
        "/": set(GLOSSARY) - {"event"},
        "/transactions": {"debit", "credit"},
        "/post-transaction": {"debit", "credit", "balanced"},
        "/event-log": {"event"},
        "/accounts/new": {"normal-side"},
    }
    for path, html in pages.items():
        ids = _ids(html)
        assert len(ids) == len(set(ids)), f"duplicate ids on {path}"
        targets = re.findall(r'popovertarget="([^"]+)"', html)
        described = re.findall(r'aria-describedby="([^"]+)"', html)
        assert set(targets) | set(described) <= set(ids), path
        terms = set(re.findall(r'class="term term-([\w-]+)"', html))
        assert terms == expected[path], path
