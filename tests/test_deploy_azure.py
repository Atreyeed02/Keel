"""
The deploy to Azure Container Apps: .github/workflows/deploy-azure.yml. It can
change the Azure resources and runs the migrations on the live database, so
these tests pin what keeps it safe: when it runs, what its token may do, where
the credentials go, that the migrations finish before the app moves, and that
only the exact healthy body counts as a deploy that worked.

No database and no Azure needed: they read the workflow and the Dockerfile.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-azure.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow) -> dict:
    (only,) = workflow["jobs"].values()
    return only


def _runs(job: dict) -> list[str]:
    return [step.get("run", "") for step in job["steps"]]


def _step(job: dict, name: str) -> dict:
    (step,) = [step for step in job["steps"] if step.get("name") == name]
    return step


def _index(job: dict, fragment: str) -> int:
    (index,) = [i for i, run in enumerate(_runs(job)) if fragment in run]
    return index


def _image_sources() -> set[str]:
    """What the Dockerfile COPYs, as push paths: a directory as `dir/**`."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    sources = set()
    for line in re.findall(r"^COPY (.+)$", dockerfile, re.MULTILINE):
        *copied, _destination = line.split()
        assert not any(part.startswith("--") for part in copied), line
        sources |= {f"{s.rstrip('/')}/**" if s.endswith("/") else s for s in copied}
    return sources


def test_a_merge_that_changes_the_image_deploys_and_nothing_else_does(workflow):
    # YAML 1.1 reads a bare `on` key as true.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    # Everything the image is built from, so no change to it goes undeployed,
    # and nothing else.
    assert _image_sources() == {
        "requirements.txt",
        "app/**",
        "alembic.ini",
        "alembic/**",
        "scripts/**",
    }
    assert set(triggers["push"]["paths"]) == _image_sources() | {"Dockerfile", ".dockerignore"}


def test_its_token_can_read_the_code_push_the_image_and_log_in_to_azure(workflow, job):
    assert workflow["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
    }
    assert "permissions" not in job


def test_deploys_never_overlap_and_one_is_never_cancelled(workflow):
    assert workflow["concurrency"] == {"group": "deploy-azure", "cancel-in-progress": False}


def test_it_runs_in_the_environment_azure_trusts_bounded_in_time(job):
    # The Entra app's federated credential trusts this environment's subject,
    # and the environment admits main only.
    assert job["environment"]["name"] == "production-azure"
    assert job["if"] == "github.repository == 'Atreyeed02/Keel'"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] <= 30
    assert job["defaults"]["run"]["shell"] == "bash"


def test_secrets_reach_only_the_registry_login_and_azure_login(job):
    for step in job["steps"]:
        # Never in a run line, a condition or a name: only an action's inputs
        # or a step's env, where no shell expands them.
        for key, value in step.items():
            if key not in {"with", "env"}:
                assert "secrets." not in str(value), step
    holders = {
        step.get("name"): {
            key: value
            for section in ("with", "env")
            for key, value in step.get(section, {}).items()
            if "secrets." in str(value)
        }
        for step in job["steps"]
    }
    holders = {name: found for name, found in holders.items() if found}
    assert holders == {
        "Log in to ghcr.io": {"TOKEN": "${{ secrets.GITHUB_TOKEN }}"},
        "Log in to Azure": {
            "client-id": "${{ secrets.AZURE_CLIENT_ID }}",
            "tenant-id": "${{ secrets.AZURE_TENANT_ID }}",
            "subscription-id": "${{ secrets.AZURE_SUBSCRIPTION_ID }}",
        },
    }
    # The registry token goes to docker on stdin, never on its command line.
    assert _step(job, "Log in to ghcr.io")["run"].endswith("--password-stdin")
    # No step handles DATABASE_URL: it's a Container Apps secret, not GitHub's.
    assert all("DATABASE_URL" not in str(step) for step in job["steps"])
    assert "DATABASE_URL" not in str(job.get("env", {}))


def test_actions_are_pinned_to_a_commit_with_their_version():
    uses = re.findall(r"^\s*(?:- )?uses: (.+)$", WORKFLOW.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(uses) == 2
    for line in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+", line), line


def test_it_deploys_the_digest_it_pushed_once_anyone_can_pull_it(job):
    build = _step(job, "Build and push the image")["run"]
    assert '--tag "${IMAGE}:${GITHUB_SHA}" --tag "${IMAGE}:main"' in build
    assert 'echo "ref=${IMAGE}@${digest}" >> "$GITHUB_OUTPUT"' in build
    assert job["env"]["IMAGE"] == "ghcr.io/atreyeed02/keel"
    # Checked with no login at all, before Azure is touched.
    check = _step(job, "Check the image can be pulled without a login")
    assert 'DOCKER_CONFIG="$(mktemp -d)"' in check["run"]
    steps = job["steps"]
    login = next(
        i for i, step in enumerate(steps) if step.get("uses", "").startswith("azure/login@")
    )
    assert steps.index(check) < login
    # Every step that changes Azure deploys the pushed digest, never a tag.
    for name in ("Point keel-migrate at the image", "Point keel at the image"):
        assert _step(job, name)["env"]["REF"] == "${{ steps.image.outputs.ref }}"


def test_the_migrations_finish_before_the_app_moves(job):
    migrate = _index(job, "az containerapp job start")
    app = _index(job, '"app.serve", "--no-migrate"')
    health = _index(job, '{"status":"ok","db":"up"}')
    assert _index(job, '"alembic", "upgrade", "head"') < migrate < app < health
    # Within 10 minutes, and only a success lets the run go on.
    run = _runs(job)[migrate]
    assert "deadline=$((SECONDS + 600))" in run
    assert re.search(r"^\s*Succeeded\)\n\s*echo .*\n\s*break$", run, re.MULTILINE)
    assert re.search(
        r"^\s*Failed \| Stopped \| Degraded\)\n\s*echo .*\n\s*exit 1$", run, re.MULTILINE
    )
    for step in job["steps"]:
        assert "continue-on-error" not in step and "if" not in step


def test_the_job_only_migrates_and_the_app_only_serves_on_8000(job):
    job_update = _runs(job)[_index(job, '"alembic", "upgrade", "head"')]
    assert 'command: ["python", "-m", "alembic", "upgrade", "head"]' in job_update
    assert 'az containerapp job show -n "$JOB"' in job_update
    app_update = _runs(job)[_index(job, '"app.serve", "--no-migrate"')]
    assert 'command: ["python", "-m", "app.serve", "--no-migrate"]' in app_update
    assert "configuration: {ingress: {targetPort: 8000}}" in app_update
    # Each sends its own container back with only the image and command
    # changed, so its env vars, secret reference and resources stay.
    for run in (job_update, app_update):
        assert "--query 'properties.template.containers[0]' -o json" in run
        assert ". + {image: $image, command: [" in run
        assert "az rest --method patch" in run
    assert job["env"]["JOB"] == "keel-migrate"
    assert job["env"]["APP"] == "keel"
    assert job["env"]["RESOURCE_GROUP"] == "rg-keel"


def test_only_the_exact_healthy_body_from_the_new_revision_passes(job):
    health = _step(job, "Wait for /health to report a live database")
    run = health["run"]
    assert 'expected=\'{"status":"ok","db":"up"}\'' in run
    assert 'if [ "$body" = "$expected" ]; then' in run
    assert "--query properties.latestReadyRevisionName" in run
    assert health["env"]["REVISION"] == "${{ steps.app.outputs.revision }}"
    assert "deadline=$((SECONDS + 180))" in run
    # The address comes from Azure, not from the file.
    assert "--query properties.configuration.ingress.fqdn" in run
    assert "azurecontainerapps.io" not in WORKFLOW.read_text(encoding="utf-8")
