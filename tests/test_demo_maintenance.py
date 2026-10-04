"""
The scheduled demo reset: scripts/check_database_host.py, which stops the run
unless DATABASE_URL names the live endpoint, and
.github/workflows/demo-maintenance.yml, which runs the reset with the live
database's URL. The workflow tests pin what keeps that URL safe, so a change
that loosens any of it fails CI instead of going unnoticed.

No database needed. The hosts are made up, under example.com.
"""

import re
from pathlib import Path

import pytest
import yaml

from app.config import settings
from scripts import check_database_host

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "demo-maintenance.yml"

PREFIX = "ep-quiet-meadow-"
REGION = "ap-southeast-1"
ENDPOINT_ID = "a1b2c3d4"
HOST = f"ep-quiet-meadow-{ENDPOINT_ID}.c-1.ap-southeast-1.example.com"
USER = "keel_owner"
PASSWORD = "not-a-real-password"


def _url(host: str) -> str:
    return f"postgresql://{USER}:{PASSWORD}@{host}/keel?sslmode=require"


def _check(monkeypatch, capsys, url: str) -> tuple[int, str]:
    monkeypatch.setattr(settings, "database_url", url)
    status = check_database_host.main(PREFIX, REGION)
    return status, capsys.readouterr().out


# --- the host check ------------------------------------------------------------------


def test_the_expected_host_passes_and_is_printed_masked(monkeypatch, capsys):
    status, out = _check(monkeypatch, capsys, _url(HOST))
    assert status == 0
    assert "Database host: ep-quiet-meadow-********.c-1.ap-southeast-1.example.com\n" in out


def test_the_host_is_compared_case_insensitively(monkeypatch, capsys):
    status, _ = _check(monkeypatch, capsys, _url(HOST.upper()))
    assert status == 0


WRONG_HOSTS = [
    # another branch's endpoint, such as a snapshot's
    (f"ep-bold-river-{ENDPOINT_ID}.c-1.ap-southeast-1.example.com", "does not start with"),
    (f"ep-quiet-meadow-{ENDPOINT_ID}-pooler.c-1.ap-southeast-1.example.com", "connection pooler"),
    (f"ep-quiet-meadow-{ENDPOINT_ID}.c-2.us-east-2.example.com", f"is not in {REGION}"),
]


@pytest.mark.parametrize(("host", "reason"), WRONG_HOSTS)
def test_any_other_host_fails_and_says_why(monkeypatch, capsys, host, reason):
    status, out = _check(monkeypatch, capsys, _url(host))
    assert status == 1
    assert "Not the expected database: " in out
    assert reason in out


def test_a_url_without_a_host_fails(monkeypatch, capsys):
    status, out = _check(monkeypatch, capsys, "postgresql:///keel")
    assert status == 1
    assert "Database host: (none)" in out
    assert "names no host" in out


@pytest.mark.parametrize("host", [HOST] + [host for host, _ in WRONG_HOSTS])
def test_neither_the_url_nor_the_endpoint_id_is_ever_printed(monkeypatch, capsys, host):
    _, out = _check(monkeypatch, capsys, _url(host))
    for secret in (PASSWORD, USER, ENDPOINT_ID, _url(host)):
        assert secret not in out


@pytest.mark.parametrize(
    ("host", "shown"),
    [
        (HOST, "ep-quiet-meadow-********.c-1.ap-southeast-1.example.com"),
        (
            f"ep-quiet-meadow-{ENDPOINT_ID}-pooler.c-1.ap-southeast-1.example.com",
            "ep-quiet-meadow-********-pooler.c-1.ap-southeast-1.example.com",
        ),
        ("db.example.com", "db.example.com"),
    ],
)
def test_only_a_neon_endpoint_id_is_masked(host, shown):
    assert check_database_host.masked(host) == shown


# --- the workflow --------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _jobs(workflow: dict) -> list[dict]:
    return list(workflow["jobs"].values())


def _steps(workflow: dict) -> list[dict]:
    return [step for job in _jobs(workflow) for step in job["steps"]]


def test_only_the_schedule_and_a_manual_run_start_it(workflow):
    # YAML 1.1 reads a bare `on` key as true.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}


def test_its_token_can_only_read(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in _jobs(workflow))


def test_runs_never_overlap_and_a_reset_is_never_cancelled(workflow):
    assert workflow["concurrency"] == {"group": "demo-database", "cancel-in-progress": False}


def test_every_job_is_the_demo_bounded_in_time_on_a_plain_shell(workflow):
    for job in _jobs(workflow):
        assert job["environment"]["name"] == "production-demo"
        assert job["env"]["ENVIRONMENT"] == "demo"
        assert job["timeout-minutes"] <= 15
        # bash --noprofile --norc -eo pipefail: no startup files, no tracing
        assert job["defaults"]["run"]["shell"] == "bash"


def test_the_secret_reaches_only_the_steps_that_run_a_script(workflow):
    assert "DATABASE_URL" not in workflow.get("env", {})
    for job in _jobs(workflow):
        assert "DATABASE_URL" not in job.get("env", {})
    holders = 0
    for step in _steps(workflow):
        env = step.get("env", {})
        # Not in a run line, an action's inputs or a condition: only a
        # step's env, where no shell expands it.
        for key, value in step.items():
            if key != "env":
                assert "secrets." not in str(value)
        assert "DATABASE_URL" not in step.get("run", "")
        if "DATABASE_URL" in env:
            holders += 1
            assert env["DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"
            assert step["run"].startswith("python -m scripts.")
    assert holders == 2


def test_the_host_is_checked_before_the_reset_and_stops_it(workflow):
    steps = _steps(workflow)
    runs = [step.get("run", "") for step in steps]
    check = next(i for i, run in enumerate(runs) if "scripts.check_database_host" in run)
    reset = next(i for i, run in enumerate(runs) if "scripts.reset_demo_data" in run)
    assert check < reset
    assert re.search(r"--prefix ep-[a-z]+-[a-z]+- ", runs[check])
    assert "--region " in runs[check]
    assert "continue-on-error" not in steps[check]
    assert "if" not in steps[reset]
    assert runs[reset] == "python -m scripts.reset_demo_data --yes"


def test_actions_are_pinned_to_a_commit(workflow):
    for step in _steps(workflow):
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"])


def test_python_is_the_dockerfiles(workflow):
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^FROM python:(\d+\.\d+)", dockerfile, re.MULTILINE).group(1)
    versions = [
        step["with"]["python-version"]
        for step in _steps(workflow)
        if step.get("uses", "").startswith("actions/setup-python@")
    ]
    assert versions == [image]
