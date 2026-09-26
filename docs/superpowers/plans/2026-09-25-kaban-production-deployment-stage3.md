# KABAN Production Deployment Stage 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship KABAN v1.13 as a provider-neutral, Docker Compose-based 24/7 production service with independent scheduler/admin processes, persistent data, authenticated gateway access, and health reporting while preserving the current Windows/local workflow.

**Architecture:** Build one immutable KABAN Python image and run it as two independent application services (`scheduler` and `admin`) behind a Caddy `gateway`. Generic deployment and process-health behavior stays in KABAN/top-level deployment assets; CAELUS remains only a Project adapter/business workflow and keeps its existing `06:00/08:00 Pacific/Auckland` schedule.

**Tech Stack:** Python 3.10, stdlib `http.server`/`urllib`, PyYAML, Docker Engine, Docker Compose v2, Caddy 2, existing KABAN scheduler/storage APIs.

**Spec:** `docs/superpowers/specs/2026-09-25-kaban-production-deployment-stage3-design.md`

## Global Constraints

- Target release is `v1.13`; base is `KABAN / CAELUS Production Schedule v1.12.1`.
- Generic deployment/runtime functionality belongs in KABAN or top-level deployment assets; do not add deployment code under `projects/caelus/`.
- KABAN Core must not import `projects.caelus` directly.
- Preserve CAELUS timezone `Pacific/Auckland`, `generate_ru = 0 6 * * *`, `publish_ru = 0 8 * * *`; do not add EN or auto-approve.
- Preserve local defaults: Review Console `127.0.0.1:8088` and all existing non-Docker CLIs.
- Use one Python image for `scheduler` and `admin`; run them as independent processes/containers.
- Application containers must run non-root, without privileged mode, host networking, or Docker socket mounts.
- Production persistence is host-backed `./data/generated -> /app/generated` and `./data/runtime -> /app/runtime`.
- Secrets remain host-side in `.env`; no `.env`, runtime state, caches, tests, private keys, or generated development output may enter the application image.
- `admin:8088` must not be directly published to the host; human access goes through authenticated Caddy gateway.
- Canonical deployment commands use `docker compose`, never legacy `docker-compose`.
- Docker logs are stdout/stderr with bounded rotation (`json-file`, `max-size=10m`, `max-file=5`).
- Existing release workspace has no Git repository. Do **not** initialize fake Git history only to satisfy process mechanics; each task instead ends with a verified test/checkpoint recorded in the execution ledger.

## Review Focus

1. **Host filesystem ownership:** bind-mounted `data/` must remain writable by a non-root container user; Task 4 pins UID/GID behavior and Task 6 documents the production preparation commands.
2. **Corrupt/missing heartbeat:** scheduler health must fail closed without crashing or claiming health; Task 2 tests missing, stale, future-skewed, corrupt, and fresh heartbeat cases.
3. **Accidental public admin exposure:** `admin` may use `expose`, but never host `ports`; Task 5 parses Compose and asserts only the gateway publishes ports.
4. **Existing production data:** bootstrap may never delete or overwrite host data implicitly; Task 3 tests non-empty destination, safe merge, and host-wins conflicts.
5. **Secret leakage through build/release context:** `.env`, key/token patterns, runtime state, and private keys must be absent from image context/release; Tasks 4 and 7 scan both configuration and final archive.

---

## File Structure

Create or modify these units only:

```text
admin_app.py                         # env-configurable bind + /healthz
scheduler.py                         # health CLI subcommand
kaban/scheduler/health.py            # heartbeat persistence/freshness rules
kaban/scheduler/runner.py            # daemon writes heartbeat

deploy/bootstrap_data.py             # safe one-time generated-data bootstrap
deploy/Caddyfile                     # authenticated reverse proxy
deploy/README_PRODUCTION_RU.md        # deterministic operator workflow

Dockerfile                           # common Python application image
.dockerignore                        # image-context hygiene
compose.yaml                         # scheduler/admin/gateway topology
.env.example                         # deployment placeholders + runtime vars
.gitignore                           # ignore host deployment data/runtime

README_RU.md                         # high-level Docker production entrypoint
ARCHITECTURE.md                      # Stage 3 topology/invariants
VERSION                              # 1.13
RELEASE_NOTES_KABAN_DEPLOYMENT_v1_13.md

tests/test_admin_runtime.py
tests/test_scheduler_health.py
tests/test_deployment_bootstrap.py
tests/test_docker_image_contract.py
tests/test_compose_contract.py
tests/test_deployment_acceptance.py
```

No CAELUS workflow/provider/rendering files are modified by this stage.

---

### Task 1: Review Console runtime binding and minimal health endpoint

**Files:**
- Modify: `admin_app.py`
- Create: `tests/test_admin_runtime.py`

**Interfaces:**
- Consumes: existing `Handler`, `ThreadingHTTPServer`, local Review Console behavior.
- Produces: `admin_host() -> str`, `admin_port() -> int`, `health_payload() -> dict[str, object]`, `GET /healthz`.

- [ ] **Step 1: Write failing tests for bind defaults/overrides and `/healthz`**

```python
# tests/test_admin_runtime.py
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import admin_app


def test_admin_bind_defaults(monkeypatch):
    monkeypatch.delenv("KABAN_ADMIN_HOST", raising=False)
    monkeypatch.delenv("KABAN_ADMIN_PORT", raising=False)
    assert admin_app.admin_host() == "127.0.0.1"
    assert admin_app.admin_port() == 8088


def test_admin_bind_environment_overrides(monkeypatch):
    monkeypatch.setenv("KABAN_ADMIN_HOST", "0.0.0.0")
    monkeypatch.setenv("KABAN_ADMIN_PORT", "18088")
    assert admin_app.admin_host() == "0.0.0.0"
    assert admin_app.admin_port() == 18088


@pytest.mark.parametrize("raw", ["0", "65536", "abc", ""])
def test_admin_port_rejects_invalid_values(monkeypatch, raw):
    monkeypatch.setenv("KABAN_ADMIN_PORT", raw)
    with pytest.raises(ValueError, match="KABAN_ADMIN_PORT"):
        admin_app.admin_port()


def test_healthz_is_minimal_json_and_does_not_redirect():
    server = ThreadingHTTPServer(("127.0.0.1", 0), admin_app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/healthz", timeout=3
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert response.headers.get_content_type() == "application/json"
            assert payload == {"status": "ok", "service": "kaban-admin"}
            rendered = json.dumps(payload)
            assert "OPENAI" not in rendered
            assert "TELEGRAM" not in rendered
            assert "generated" not in rendered.lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
```

- [ ] **Step 2: Run the new tests and confirm RED for missing runtime helpers/route**

Run:

```bash
python -m pytest tests/test_admin_runtime.py -q
```

Expected: failures because `admin_host`, `admin_port`, and `/healthz` behavior do not yet exist.

- [ ] **Step 3: Implement minimal runtime configuration and route**

Add to `admin_app.py`:

```python
def admin_host() -> str:
    return os.getenv("KABAN_ADMIN_HOST", "127.0.0.1").strip() or "127.0.0.1"


def admin_port() -> int:
    raw = os.getenv("KABAN_ADMIN_PORT", "8088").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("KABAN_ADMIN_PORT должен быть целым числом 1..65535") from exc
    if not 1 <= value <= 65535:
        raise ValueError("KABAN_ADMIN_PORT должен быть в диапазоне 1..65535")
    return value


def health_payload() -> dict[str, object]:
    return {"status": "ok", "service": "kaban-admin"}
```

At the top of `Handler.do_GET`, before `/` redirect handling:

```python
if parsed.path == "/healthz":
    data = json.dumps(health_payload(), ensure_ascii=False).encode("utf-8")
    self.send_bytes(data, "application/json; charset=utf-8", status=200)
    return
```

Import `json`, and change `main()` to:

```python
host = admin_host()
port = admin_port()
server = ThreadingHTTPServer((host, port), Handler)
print(f"CAELUS Review Console: http://{host}:{port}")
```

Do not change any existing human routes or POST actions.

- [ ] **Step 4: Verify GREEN plus existing Review Console tests**

Run:

```bash
python -m pytest tests/test_admin_runtime.py tests/test_admin_diversity.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Record Task 1 checkpoint**

Run:

```bash
python -m pytest tests/test_admin_runtime.py -q > /tmp/kaban-stage3-task1.txt
```

Expected: exit code `0`; record the test count/output in the Stage 3 execution ledger.

---

### Task 2: Scheduler daemon heartbeat and health CLI

**Files:**
- Create: `kaban/scheduler/health.py`
- Modify: `kaban/scheduler/runner.py`
- Modify: `scheduler.py`
- Create: `tests/test_scheduler_health.py`

**Interfaces:**
- Consumes: `kaban.storage.load_json`, `kaban.storage.write_json`, `KABAN_RUNTIME_DIR` convention.
- Produces:
  - `SchedulerHealthError`;
  - `Heartbeat` dataclass;
  - `write_heartbeat(*, now_utc=None, pid=None, runtime_dir=None) -> Path`;
  - `read_heartbeat(*, runtime_dir=None) -> Heartbeat`;
  - `check_heartbeat(*, max_age_seconds=120, now_utc=None, runtime_dir=None) -> Heartbeat`;
  - `python scheduler.py health [--max-age-seconds N]`.

- [ ] **Step 1: Write failing heartbeat and CLI tests**

```python
# tests/test_scheduler_health.py
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kaban.scheduler.health import (
    SchedulerHealthError,
    check_heartbeat,
    read_heartbeat,
    write_heartbeat,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)


def test_write_and_read_fresh_heartbeat(tmp_path):
    path = write_heartbeat(now_utc=NOW, pid=123, runtime_dir=tmp_path)
    assert path == tmp_path / "scheduler" / "heartbeat.json"
    heartbeat = read_heartbeat(runtime_dir=tmp_path)
    assert heartbeat.pid == 123
    assert heartbeat.status == "running"
    assert heartbeat.updated_at == NOW
    assert not list(path.parent.glob("*.tmp"))
    assert check_heartbeat(now_utc=NOW + timedelta(seconds=119), runtime_dir=tmp_path).pid == 123


def test_missing_heartbeat_is_unhealthy(tmp_path):
    with pytest.raises(SchedulerHealthError, match="не найден"):
        check_heartbeat(now_utc=NOW, runtime_dir=tmp_path)


def test_stale_heartbeat_is_unhealthy(tmp_path):
    write_heartbeat(now_utc=NOW, pid=123, runtime_dir=tmp_path)
    with pytest.raises(SchedulerHealthError, match="устарел"):
        check_heartbeat(
            now_utc=NOW + timedelta(seconds=121), max_age_seconds=120, runtime_dir=tmp_path
        )


def test_corrupt_heartbeat_is_unhealthy(tmp_path):
    path = tmp_path / "scheduler" / "heartbeat.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SchedulerHealthError, match="поврежд"):
        check_heartbeat(now_utc=NOW, runtime_dir=tmp_path)


def test_future_heartbeat_beyond_clock_tolerance_is_unhealthy(tmp_path):
    write_heartbeat(now_utc=NOW + timedelta(minutes=10), pid=123, runtime_dir=tmp_path)
    with pytest.raises(SchedulerHealthError, match="будущ"):
        check_heartbeat(now_utc=NOW, runtime_dir=tmp_path)
```

Add CLI tests in the same file:

```python
import scheduler


def test_health_cli_returns_zero_for_fresh_heartbeat(monkeypatch):
    monkeypatch.setattr(scheduler, "check_heartbeat", lambda **_: object())
    assert scheduler.main(["health"]) == 0


def test_health_cli_returns_one_for_unhealthy_heartbeat(monkeypatch, capsys):
    def fail(**_):
        raise SchedulerHealthError("heartbeat устарел")
    monkeypatch.setattr(scheduler, "check_heartbeat", fail)
    assert scheduler.main(["health"]) == 1
    assert "устарел" in capsys.readouterr().err
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_scheduler_health.py -q
```

Expected: import/behavior failures because the health module and CLI command do not yet exist.

- [ ] **Step 3: Implement heartbeat model/persistence/freshness**

Create `kaban/scheduler/health.py` with a frozen dataclass and strict parser:

```python
@dataclass(frozen=True)
class Heartbeat:
    schema_version: int
    updated_at: datetime
    pid: int
    status: str
```

Store exactly:

```json
{
  "schema_version": 1,
  "updated_at": "<timezone-aware ISO-8601 UTC>",
  "pid": 123,
  "status": "running"
}
```

Rules in `check_heartbeat()`:

- `max_age_seconds > 0` or raise `ValueError`;
- missing/corrupt/wrong schema/wrong types -> `SchedulerHealthError`;
- stale when age `> max_age_seconds`;
- future heartbeat more than 30 seconds ahead -> `SchedulerHealthError`;
- writes use existing atomic `write_json()`;
- heartbeat root follows the same `KABAN_RUNTIME_DIR` semantics as `SchedulerStore`.

- [ ] **Step 4: Make `run_forever()` write heartbeat without changing slot semantics**

Refactor `kaban/scheduler/runner.py` to support deterministic testing while preserving the existing call:

```python
def run_forever(
    engine,
    *,
    project_id: str | None = None,
    poll_seconds: float = 30.0,
    heartbeat_writer=write_heartbeat,
    now_fn=lambda: datetime.now(timezone.utc),
    sleep_fn=time.sleep,
) -> None:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds должен быть > 0")
    heartbeat_writer(now_utc=now_fn())
    try:
        while True:
            now_utc = now_fn()
            engine.tick(now_utc=now_utc, project_id=project_id)
            heartbeat_writer(now_utc=now_utc)
            sleep_fn(poll_seconds)
    except KeyboardInterrupt:
        return
```

Add a test where `sleep_fn` raises `KeyboardInterrupt` after the first cycle and assert: one `engine.tick`, at least two heartbeat writes, and the tick receives the same timezone-aware `now_utc` as before.

- [ ] **Step 5: Add `scheduler.py health`**

Add parser:

```python
p = sub.add_parser("health")
p.add_argument("--max-age-seconds", type=int, default=120)
```

Handle it before constructing `ProjectRegistry`/`SchedulerEngine`:

```python
if args.command == "health":
    try:
        heartbeat = check_heartbeat(max_age_seconds=args.max_age_seconds)
    except SchedulerHealthError as exc:
        print(f"UNHEALTHY: {exc}", file=sys.stderr)
        return 1
    print(f"HEALTHY\tupdated_at={heartbeat.updated_at.isoformat()}\tpid={heartbeat.pid}")
    return 0
```

Invalid CLI values (`<= 0`) return the existing configuration/error exit path, not health success.

- [ ] **Step 6: Verify focused and scheduler regression tests**

```bash
python -m pytest \
  tests/test_scheduler_health.py \
  tests/test_scheduler_cli.py \
  tests/test_scheduler_engine.py \
  tests/test_scheduler_acceptance.py -q
```

Expected: all pass; Stage 2 exactly-once/retry semantics remain green.

- [ ] **Step 7: Record Task 2 checkpoint**

```bash
python scheduler.py health --max-age-seconds 120 >/tmp/kaban-stage3-health.txt 2>&1; test $? -eq 1
```

Expected before daemon start: exit `1` because a missing heartbeat is correctly reported unhealthy; record this expected fail-closed behavior in the ledger.

---

### Task 3: Safe bootstrap for persistent generated data

**Files:**
- Create: `deploy/bootstrap_data.py`
- Create: `tests/test_deployment_bootstrap.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: shipped repository `generated/` tree.
- Produces: `bootstrap_generated(source: Path, destination: Path, *, merge: bool = False) -> BootstrapResult` and CLI `python deploy/bootstrap_data.py [--source ...] [--destination ...] [--merge]`.

- [ ] **Step 1: Write failing bootstrap safety tests**

```python
# tests/test_deployment_bootstrap.py
from pathlib import Path
import pytest

from deploy.bootstrap_data import BootstrapError, bootstrap_generated


def test_bootstrap_copies_into_empty_destination(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    (src / "2026-09-22" / "ru").mkdir(parents=True)
    (src / "2026-09-22" / "ru" / "content.json").write_text("{}", encoding="utf-8")
    result = bootstrap_generated(src, dst)
    assert result.copied == 1
    assert (dst / "2026-09-22" / "ru" / "content.json").read_text() == "{}"


def test_bootstrap_refuses_nonempty_destination_without_merge(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    src.mkdir()
    dst.mkdir(parents=True)
    (dst / "keep.txt").write_text("host", encoding="utf-8")
    with pytest.raises(BootstrapError, match="не пуст"):
        bootstrap_generated(src, dst)
    assert (dst / "keep.txt").read_text() == "host"


def test_merge_never_overwrites_existing_host_file(tmp_path):
    src = tmp_path / "generated"
    dst = tmp_path / "data" / "generated"
    src.mkdir()
    dst.mkdir(parents=True)
    (src / "same.json").write_text("source", encoding="utf-8")
    (src / "new.json").write_text("new", encoding="utf-8")
    (dst / "same.json").write_text("host", encoding="utf-8")
    result = bootstrap_generated(src, dst, merge=True)
    assert (dst / "same.json").read_text() == "host"
    assert (dst / "new.json").read_text() == "new"
    assert result.skipped_existing == 1
    assert result.copied == 1
```

Also test missing/non-directory source and assert no `runtime/` content is copied or created by the generated-data copy routine.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_deployment_bootstrap.py -q
```

Expected: import failure because `deploy/bootstrap_data.py` does not exist.

- [ ] **Step 3: Implement host-data-wins bootstrap semantics**

Use:

```python
@dataclass(frozen=True)
class BootstrapResult:
    copied: int
    skipped_existing: int

class BootstrapError(RuntimeError):
    pass
```

Implementation rules:

- source must exist and be a directory;
- destination is created if missing;
- destination with any entry aborts unless `merge=True`;
- default empty-destination copy preserves relative paths and file metadata with `shutil.copy2`;
- merge copies only missing files/directories and **never overwrites** an existing destination file;
- no delete operation exists;
- do not follow source symlinks outside the source tree; reject symlink entries with `BootstrapError`;
- CLI defaults: source=`<repo>/generated`, destination=`<repo>/data/generated`.

- [ ] **Step 4: Verify GREEN and CLI behavior**

```bash
python -m pytest tests/test_deployment_bootstrap.py -q
python deploy/bootstrap_data.py --help >/tmp/kaban-stage3-bootstrap-help.txt
```

Expected: tests pass and help exits `0`.

- [ ] **Step 5: Ignore deployment-local mutable data**

Append to `.gitignore`:

```gitignore
data/
runtime/
```

Do **not** ignore the shipped historical `generated/` tree because existing releases intentionally include it for one-time bootstrap/regression history.

- [ ] **Step 6: Record Task 3 checkpoint**

```bash
python -m pytest tests/test_deployment_bootstrap.py -q > /tmp/kaban-stage3-task3.txt
```

Expected: exit `0`; record test count in ledger.

---

### Task 4: Common non-root Docker image and build-context hygiene

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `tests/test_docker_image_contract.py`

**Interfaces:**
- Consumes: `requirements.txt`, application source, project assets/configuration.
- Produces: common image usable by both `scheduler` and `admin`.

- [ ] **Step 1: Write failing static image-contract tests**

```python
# tests/test_docker_image_contract.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_is_nonroot_python310_image():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.10-slim-bookworm" in text
    assert "PYTHONUNBUFFERED=1" in text
    assert "USER kaban" in text
    assert "COPY requirements.txt" in text
    assert "pip install" in text
    assert "COPY kaban" in text
    assert "COPY projects" in text
    assert "COPY .env" not in text


def test_dockerignore_excludes_secrets_runtime_tests_and_caches():
    patterns = set(
        line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    required = {
        ".env", ".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache",
        "runtime", "data", "generated", "tests", "docs", "*.pem", "*.key"
    }
    assert required <= patterns
```

Add a test that enumerates the root release tree and fails if `.env`, `.pem`, `.key`, `runtime/`, `data/`, `__pycache__/`, or `.pytest_cache/` would be part of the intended build context.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_docker_image_contract.py -q
```

Expected: missing `Dockerfile`/`.dockerignore` failures.

- [ ] **Step 3: Create minimal production Dockerfile**

Use this structure:

```dockerfile
FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 kaban \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin kaban

COPY --chown=kaban:kaban kaban ./kaban
COPY --chown=kaban:kaban projects ./projects
COPY --chown=kaban:kaban *.py ./
COPY --chown=kaban:kaban VERSION ./VERSION

USER kaban

CMD ["python", "scheduler.py", "list"]
```

The Compose file may override the numeric UID/GID through its `user:` field, but the image itself must have a safe non-root default.

- [ ] **Step 4: Create explicit `.dockerignore`**

Include at minimum:

```dockerignore
.env
.git
.gitignore
.venv
__pycache__
*.pyc
.pytest_cache
runtime
data
generated
tests
docs
*.pem
*.key
*.p12
*.pfx
preview_*.jpg
RELEASE_NOTES_*.md
```

Do not exclude `projects/**/assets`, `project.yaml`, Python modules, `requirements.txt`, or `VERSION`.

- [ ] **Step 5: Verify GREEN and optional image build**

```bash
python -m pytest tests/test_docker_image_contract.py -q
```

Then detect Docker:

```bash
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker build -t kaban-stage3:test .
  docker run --rm kaban-stage3:test python -c "import os; assert os.geteuid() != 0"
  docker run --rm kaban-stage3:test python scheduler.py list
else
  echo "Docker daemon unavailable: runtime image build deferred to release verification report"
fi
```

Expected when Docker is available: build succeeds, container EUID is non-zero, scheduler list runs. If unavailable, record the limitation; do not fabricate PASS.

- [ ] **Step 6: Record Task 4 checkpoint**

```bash
python -m pytest tests/test_docker_image_contract.py -q > /tmp/kaban-stage3-task4.txt
```

Expected: exit `0`.

---

### Task 5: Docker Compose topology and authenticated Caddy gateway

**Files:**
- Create: `compose.yaml`
- Create: `deploy/Caddyfile`
- Modify: `.env.example`
- Create: `tests/test_compose_contract.py`

**Interfaces:**
- Consumes: common image from Task 4, `/healthz` from Task 1, `scheduler.py health` from Task 2.
- Produces: `scheduler`, `admin`, `gateway` production services.

- [ ] **Step 1: Write failing Compose security/topology tests**

```python
# tests/test_compose_contract.py
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_compose():
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_has_three_isolated_services_and_persistence():
    cfg = load_compose()
    services = cfg["services"]
    assert set(services) >= {"scheduler", "admin", "gateway"}

    scheduler = services["scheduler"]
    admin = services["admin"]
    gateway = services["gateway"]

    assert scheduler["command"] == ["python", "scheduler.py", "run", "--poll-seconds", "30"]
    assert admin["command"] == ["python", "admin_app.py"]
    assert scheduler["restart"] == "unless-stopped"
    assert admin["restart"] == "unless-stopped"
    assert gateway["restart"] == "unless-stopped"

    assert "ports" not in scheduler
    assert "ports" not in admin
    assert admin["expose"] == ["8088"]
    assert gateway["ports"] == ["80:80", "443:443"]

    expected = {"./data/generated:/app/generated", "./data/runtime:/app/runtime"}
    assert expected <= set(scheduler["volumes"])
    assert expected <= set(admin["volumes"])


def test_healthchecks_and_log_rotation_are_bounded():
    cfg = load_compose()
    for name in ("scheduler", "admin"):
        service = cfg["services"][name]
        assert "healthcheck" in service
        assert service["logging"]["driver"] == "json-file"
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "5"}


def test_no_privileged_host_network_or_docker_socket():
    cfg = load_compose()
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for service in cfg["services"].values():
        assert service.get("privileged") is not True
        assert service.get("network_mode") != "host"
    assert "/var/run/docker.sock" not in text


def test_caddy_requires_auth_and_only_proxies_admin():
    text = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "basic_auth" in text
    assert "{$KABAN_ADMIN_USER}" in text
    assert "{$KABAN_ADMIN_PASSWORD_HASH}" in text
    assert "reverse_proxy admin:8088" in text
    assert "scheduler" not in text
```

Also assert `scheduler` healthcheck invokes `python scheduler.py health --max-age-seconds 120`, `admin` healthcheck uses stdlib HTTP against `127.0.0.1:8088/healthz`, and Compose sets `KABAN_ADMIN_HOST=0.0.0.0`, `KABAN_ADMIN_PORT=8088`, `KABAN_RUNTIME_DIR=/app/runtime`.

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_compose_contract.py -q
```

Expected: missing Compose/Caddy configuration failures.

- [ ] **Step 3: Create `compose.yaml`**

Use YAML anchors only to remove duplication; keep the rendered services explicit and testable. Required service semantics:

```yaml
services:
  scheduler:
    build: .
    image: kaban-content-engine:1.13
    command: ["python", "scheduler.py", "run", "--poll-seconds", "30"]
    restart: unless-stopped
    env_file: [.env]
    environment:
      KABAN_RUNTIME_DIR: /app/runtime
    volumes:
      - ./data/generated:/app/generated
      - ./data/runtime:/app/runtime
    healthcheck:
      test: ["CMD", "python", "scheduler.py", "health", "--max-age-seconds", "120"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "5"}

  admin:
    image: kaban-content-engine:1.13
    build: .
    command: ["python", "admin_app.py"]
    restart: unless-stopped
    env_file: [.env]
    environment:
      KABAN_RUNTIME_DIR: /app/runtime
      KABAN_ADMIN_HOST: 0.0.0.0
      KABAN_ADMIN_PORT: "8088"
    volumes:
      - ./data/generated:/app/generated
      - ./data/runtime:/app/runtime
    expose: ["8088"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=3).read()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "5"}

  gateway:
    image: caddy:2-alpine
    restart: unless-stopped
    env_file: [.env]
    depends_on:
      admin:
        condition: service_healthy
    ports: ["80:80", "443:443"]
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "5"}

volumes:
  caddy_data:
  caddy_config:
```

Set `user: "${KABAN_UID:-1000}:${KABAN_GID:-1000}"` on both Python application services so bind-mounted host data can match the operator UID/GID while remaining non-root.

- [ ] **Step 4: Create authenticated `deploy/Caddyfile`**

```caddyfile
{$KABAN_PUBLIC_URL:http://localhost} {
    basic_auth {
        {$KABAN_ADMIN_USER} {$KABAN_ADMIN_PASSWORD_HASH}
    }
    reverse_proxy admin:8088
}
```

Rules:

- default URL is local-only `http://localhost` for safe initial smoke;
- production public domain is configured as a hostname (for example `review.example.com`) so Caddy obtains HTTPS automatically;
- do not add a route to scheduler;
- `/healthz` remains an internal container health endpoint; gateway authentication covers everything it proxies.

- [ ] **Step 5: Extend `.env.example` with deployment placeholders only**

Append:

```dotenv
# Docker production deployment
KABAN_UID=1000
KABAN_GID=1000
KABAN_PUBLIC_URL=http://localhost
KABAN_ADMIN_USER=replace_me
KABAN_ADMIN_PASSWORD_HASH=replace_with_caddy_hash
```

Retain all existing API/Telegram placeholders. No real credentials.

- [ ] **Step 6: Verify static GREEN and Compose parsing**

```bash
python -m pytest tests/test_compose_contract.py -q
python - <<'PY'
import yaml
with open('compose.yaml', encoding='utf-8') as fh:
    data = yaml.safe_load(fh)
assert {'scheduler', 'admin', 'gateway'} <= set(data['services'])
print('compose-static-ok')
PY
```

If Docker Compose is installed, also run:

```bash
if docker compose version >/dev/null 2>&1; then
  test ! -e .env || { echo 'Refusing to touch existing .env during verification' >&2; exit 1; }
  cp .env.example .env
  trap 'rm -f .env' EXIT
  docker compose config >/tmp/kaban-stage3-compose-config.yaml
  rm -f .env
  trap - EXIT
fi
```

Expected: `docker compose config` exits `0` when available. Never leave the temporary env file in the release.

- [ ] **Step 7: Record Task 5 checkpoint**

```bash
python -m pytest tests/test_compose_contract.py -q > /tmp/kaban-stage3-task5.txt
```

Expected: exit `0`.

---

### Task 6: Production operator workflow, backups, reboot behavior, and documentation

**Files:**
- Create: `deploy/README_PRODUCTION_RU.md`
- Modify: `README_RU.md`
- Modify: `ARCHITECTURE.md`
- Create: `tests/test_deployment_acceptance.py`

**Interfaces:**
- Consumes: Tasks 1–5 deployment assets.
- Produces: one deterministic Linux-host production runbook and acceptance checks for the shipped configuration.

- [ ] **Step 1: Write failing documentation/acceptance tests**

```python
# tests/test_deployment_acceptance.py
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_production_runbook_contains_canonical_commands_and_safety_rules():
    text = (ROOT / "deploy" / "README_PRODUCTION_RU.md").read_text(encoding="utf-8")
    required = [
        "docker compose up -d --build",
        "docker compose ps",
        "docker compose logs -f scheduler",
        "docker compose logs -f admin",
        "docker compose exec scheduler python scheduler.py list",
        "docker compose exec scheduler python scheduler.py status",
        "docker compose exec scheduler python scheduler.py run-now",
        "docker compose stop",
        "docker compose restart",
        "python deploy/bootstrap_data.py",
        "systemctl enable --now docker",
        "data/generated",
        "data/runtime",
    ]
    for item in required:
        assert item in text
    assert "docker-compose" not in text
    assert "down -v" not in text


def test_runbook_documents_backup_and_non_destructive_upgrade():
    text = (ROOT / "deploy" / "README_PRODUCTION_RU.md").read_text(encoding="utf-8")
    assert "tar -czf" in text
    assert "docker compose build --pull" in text
    assert "docker compose up -d --remove-orphans" in text
    assert "не удал" in text.lower()


def test_stage3_does_not_change_caelus_schedule():
    project = yaml.safe_load((ROOT / "projects" / "caelus" / "project.yaml").read_text(encoding="utf-8"))
    jobs = {job["id"]: job for job in project["automation"]["jobs"]}
    assert project["timezone"] == "Pacific/Auckland"
    assert jobs["generate_ru"]["cron"] == "0 6 * * *"
    assert jobs["publish_ru"]["cron"] == "0 8 * * *"
    assert set(jobs) == {"generate_ru", "publish_ru"}
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_deployment_acceptance.py -q
```

Expected: missing runbook assertions fail.

- [ ] **Step 3: Write `deploy/README_PRODUCTION_RU.md` as a copy/paste operator runbook**

The runbook must contain this order:

```text
1. Install Docker Engine + Compose plugin on Linux.
2. sudo systemctl enable --now docker
3. Copy release to host.
4. cp .env.example .env
5. Fill OPENAI/Telegram secrets.
6. Set KABAN_UID=$(id -u), KABAN_GID=$(id -g).
7. Generate Caddy password hash interactively and set KABAN_ADMIN_USER / KABAN_ADMIN_PASSWORD_HASH.
8. python deploy/bootstrap_data.py
9. mkdir -p data/runtime
10. docker compose up -d --build
11. docker compose ps
12. docker compose exec scheduler python scheduler.py list
13. docker compose exec scheduler python scheduler.py status
14. Verify admin/gateway access.
```

Document password hashing without putting plaintext in the shell command:

```bash
docker run --rm -it caddy:2-alpine caddy hash-password
```

Document local/private smoke with `KABAN_PUBLIC_URL=http://localhost`, and state explicitly that public Internet deployment requires a real domain/HTTPS or a private VPN/network boundary; unauthenticated public HTTP is unsupported.

Document backup:

```bash
mkdir -p backups
tar -czf "backups/kaban-data-$(date +%Y%m%d-%H%M%S).tar.gz" data/generated data/runtime
```

Document non-destructive upgrade:

```bash
# make backup first
# replace application/release files, keep .env and data/
docker compose build --pull
docker compose up -d --remove-orphans
docker compose ps
```

Warn explicitly: do not run `docker compose down -v` as an upgrade procedure.

- [ ] **Step 4: Update top-level README and architecture**

`README_RU.md` gets a short “Production 24/7” section linking to `deploy/README_PRODUCTION_RU.md` and explains that local Windows commands remain valid.

`ARCHITECTURE.md` gets the final Stage 3 topology:

```text
gateway -> admin -> shared generated/runtime
             ^
scheduler ---+
```

It must restate: deployment generic, Project business logic isolated, no direct KABAN Core -> CAELUS dependency.

- [ ] **Step 5: Verify GREEN**

```bash
python -m pytest tests/test_deployment_acceptance.py -q
```

Expected: all acceptance/documentation tests pass.

- [ ] **Step 6: Record Task 6 checkpoint**

```bash
python -m pytest tests/test_deployment_acceptance.py -q > /tmp/kaban-stage3-task6.txt
```

Expected: exit `0`.

---

### Task 7: Full regression, container smoke, release packaging, and v1.13 verification

**Files:**
- Modify: `VERSION`
- Create: `RELEASE_NOTES_KABAN_DEPLOYMENT_v1_13.md`
- Modify tests only if a historical acceptance assertion explicitly describes v1.12.1 as “scheduler has no deployment layer” and is now intentionally superseded; do not weaken functional tests.
- Produce: `KABAN_Content_Engine_Production_Deployment_Stage3_v1_13.zip`
- Produce: `KABAN_Production_Deployment_Stage3_v1_13_verification.txt`

**Interfaces:**
- Consumes: all Stage 3 tasks plus v1.12.1 baseline archive/workspace.
- Produces: clean v1.13 release artifact with verifiable SHA-256.

- [ ] **Step 1: Add final restart/persistence acceptance test**

Extend `tests/test_deployment_acceptance.py` with a filesystem-level scheduler restart simulation using a temporary `KABAN_RUNTIME_DIR`:

```python
def test_scheduler_completed_slot_and_retry_state_survive_new_engine_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("KABAN_RUNTIME_DIR", str(tmp_path / "runtime"))
    # Use the existing scheduler acceptance fake registry/adapter pattern.
    # Engine A completes one deterministic slot and persists state.
    # Engine B is freshly constructed over the same runtime root.
    # Re-ticking the completed slot must not invoke the adapter again.
    # A separate failed/blocked state with next_retry_at remains readable by Engine B.
```

Use the concrete fake registry/adapter helper pattern already present in `tests/test_scheduler_acceptance.py`; copy the minimal helper into this test rather than importing test modules from each other. Assert adapter call counts and persisted `next_retry_at`, not just file existence.

- [ ] **Step 2: Run targeted Stage 3 suite before versioning**

```bash
python -m pytest \
  tests/test_admin_runtime.py \
  tests/test_scheduler_health.py \
  tests/test_deployment_bootstrap.py \
  tests/test_docker_image_contract.py \
  tests/test_compose_contract.py \
  tests/test_deployment_acceptance.py -q
```

Expected: all Stage 3 tests pass.

- [ ] **Step 3: Run complete Python regression suites**

```bash
python -m unittest discover -s tests -v
python -m pytest -q
```

Expected: zero failures/errors.

- [ ] **Step 4: Prove backward compatibility of manual CAELUS artifacts**

Using clean copies of v1.12.1 and current v1.13 source, run the same deterministic mock day, for example:

```bash
python run_daily.py --date 2030-01-15 --language ru --mock
```

Compare the 26 user-facing generated/card/Telegram artifacts using SHA-256. Exclude only Stage 3 runtime/heartbeat/deployment metadata from the artifact contract.

Expected: `26/26` user artifacts byte-identical and semantic `signs` payload identical.

- [ ] **Step 5: Run Docker/Compose verification when the daemon exists**

```bash
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  test ! -e .env || { echo 'Refusing to touch existing .env during smoke test' >&2; exit 1; }
  cp .env.example .env
  trap 'docker compose down >/dev/null 2>&1 || true; rm -f .env' EXIT
  docker compose config >/tmp/kaban-v1.13-compose-config.yaml
  docker compose build
  docker compose up -d admin
  docker compose exec -T admin python -c "import json, urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=3)); assert d == {'status':'ok','service':'kaban-admin'}"
  docker compose down
  rm -f .env
  trap - EXIT
else
  echo "Docker daemon unavailable; static Compose validation executed, container runtime smoke NOT claimed"
fi
```

Safety: this smoke starts only `admin`, not the production scheduler, so it cannot accidentally call OpenAI or Telegram. Never use the user's real `.env` for test smoke.

- [ ] **Step 6: Set release version and notes**

Set `VERSION` to:

```text
1.13
```

`RELEASE_NOTES_KABAN_DEPLOYMENT_v1_13.md` must list:

- provider-neutral Docker Compose deployment;
- independent scheduler/admin services;
- authenticated Caddy gateway;
- `/healthz` and scheduler heartbeat health;
- persistent `data/generated` and `data/runtime`;
- safe one-time bootstrap;
- non-root image/log rotation;
- unchanged CAELUS `06:00/08:00 Pacific/Auckland` schedule;
- unchanged local Windows workflow;
- explicit Docker runtime smoke result (`PASS` or `NOT AVAILABLE IN VERIFICATION ENVIRONMENT`).

- [ ] **Step 7: Build clean release staging tree**

Copy the application to a new staging directory while excluding:

```text
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
runtime/
data/
.superpowers/
```

Keep:

- historical shipped `generated/` used by CAELUS history/bootstrap;
- `Dockerfile`, `.dockerignore`, `compose.yaml`, `deploy/`;
- source, tests, docs, assets, `.env.example`, release notes.

- [ ] **Step 8: Run release hygiene/secret scan before zipping**

Fail packaging if staging contains:

```text
.env
*.pem
*.key
*.p12
*.pfx
runtime/
data/
__pycache__/
.pytest_cache/
```

Also scan UTF-8 text files for common accidental credential patterns while allowing the documented placeholders in `.env.example`:

```text
OPENAI_API_KEY=<non-placeholder>
TELEGRAM_BOT_TOKEN=<non-placeholder>
sk-[A-Za-z0-9_-]{20,}
-----BEGIN ... PRIVATE KEY-----
```

- [ ] **Step 9: Zip, extract fresh, and verify the extracted release**

Create:

```text
/mnt/data/KABAN_Content_Engine_Production_Deployment_Stage3_v1_13.zip
```

Extract to a fresh verification directory and run:

```bash
python -m unittest discover -s tests -v
python -m pytest -q
python scheduler.py list
python scheduler.py status
python scheduler.py health --max-age-seconds 120
```

Expected health result in a clean, non-running extracted release: exit `1`/`UNHEALTHY` because no daemon heartbeat exists yet. That is correct fail-closed behavior, not a release failure.

Run static Compose validation from the extracted ZIP and, if Docker is available, repeat the safe `admin` container smoke against the extracted release.

- [ ] **Step 10: Produce verification report and SHA-256**

Write `/mnt/data/KABAN_Production_Deployment_Stage3_v1_13_verification.txt` with separate sections for:

```text
SOURCE WORKSPACE
EXTRACTED RELEASE ZIP
DOCKER RUNTIME AVAILABILITY
PYTHON TEST COUNTS
COMPOSE STATIC VALIDATION
CONTAINER SMOKE RESULT
V1.12.1 -> V1.13 ARTIFACT REGRESSION
SECRET/HYGIENE SCAN
PERSISTENCE/RESTART SIMULATION
KNOWN LIMITATIONS
SHA-256
```

Generate the archive hash with:

```bash
sha256sum /mnt/data/KABAN_Content_Engine_Production_Deployment_Stage3_v1_13.zip
```

Do not claim live Docker/container verification if the daemon was unavailable.

- [ ] **Step 11: Final verification gate**

Run one final fresh complete suite against the extracted archive after the hash/report are prepared:

```bash
python -m unittest discover -s tests -v
python -m pytest -q
```

Expected: zero failures/errors. Only after reading these fresh outputs may v1.13 be declared complete.

---

## Self-Review Record

### Spec coverage

- Independent scheduler/admin containers: Tasks 4–5.
- Authenticated gateway/TLS-capable ingress: Task 5.
- Backward-compatible admin bind + `/healthz`: Task 1.
- Scheduler heartbeat + CLI health: Task 2.
- Persistent generated/runtime data and safe bootstrap: Tasks 3, 5, 6.
- Restart/crash behavior and slot persistence: Tasks 2, 5, 7.
- Secret/image/release hygiene: Tasks 4, 5, 7.
- Bounded Docker logs: Task 5.
- Provider-neutral/operator workflow: Tasks 5–6.
- Windows/local compatibility and CAELUS schedule preservation: Tasks 1, 6, 7.
- Docker-unavailable verification honesty: Tasks 4, 5, 7.
- Explicit Stage 3 non-goals are untouched by every task.

### Placeholder scan

The implementation plan has been scanned for unfinished implementation markers and undefined steps. Example domain names and credential strings are explicitly sample values, not missing design decisions.

### Type/interface consistency

- `write_heartbeat`, `read_heartbeat`, `check_heartbeat`, `Heartbeat`, and `SchedulerHealthError` are defined once in Task 2 and used consistently by runner, CLI, Compose healthcheck, and tests.
- `admin_host`, `admin_port`, and `/healthz` are defined in Task 1 and consumed by Compose in Task 5.
- `bootstrap_generated`/`BootstrapResult`/`BootstrapError` are isolated to Task 3 and operator docs.
- `KABAN_RUNTIME_DIR=/app/runtime` is consistent across scheduler store, heartbeat, and Compose.

### Review Focus coverage

All five review-focus risks have explicit tests/tasks: non-root bind-mount ownership (Tasks 4/6), corrupt heartbeat (Task 2), admin exposure (Task 5), non-destructive bootstrap (Task 3), and secret leakage (Tasks 4/7).
