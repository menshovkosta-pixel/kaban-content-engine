# KABAN Production Deployment Stage 3 — Design Specification

**Date:** 2026-09-25  
**Target release:** v1.13  
**Status:** Draft for human review  
**Base:** KABAN / CAELUS Production Schedule v1.12.1

## 1. Purpose

Stage 3 turns the current local KABAN installation into a continuously running, restart-safe production service that keeps operating when the user's PC is off.

The deployment layer must remain **project-neutral**. KABAN owns generic runtime/deployment concerns; individual projects continue to own their workflows and business rules.

For the first production deployment, CAELUS remains the only registered project and keeps its existing schedule:

- timezone: `Pacific/Auckland`;
- `generate_ru`: `0 6 * * *`;
- `publish_ru`: `0 8 * * *`;
- EN jobs remain disabled/not configured;
- publication still requires approved content; Stage 3 does not introduce auto-approve.

## 2. Success criteria

Stage 3 is complete when a clean Docker-capable Linux host can start KABAN with one documented command and, after restart/reboot, the system automatically resumes:

1. the universal KABAN scheduler;
2. the Review Console;
3. persistent generated content and scheduler runtime state;
4. health reporting;
5. bounded container logs;
6. secure secret injection without baking secrets into an image.

The existing manual Windows workflow must remain functional outside Docker.

## 3. Architectural invariant

The existing dependency direction remains binding:

```text
KABAN Core / deployment
        ↓
ProjectRegistry
        ↓
projects/<project_id>/
```

Generic deployment/runtime functionality belongs under KABAN or top-level deployment assets. No Docker, health, logging, process-supervision, or persistent-storage logic may be placed inside `projects/caelus/` unless it is genuinely CAELUS-specific.

KABAN Core must not import `projects.caelus` directly.

## 4. Chosen deployment model

### 4.1 Alternatives considered

**A. One container running scheduler + Review Console in one Python process**  
Rejected. It couples two independent failure domains and makes health/restart semantics ambiguous.

**B. One image, two application services under Docker Compose — selected**  
The same immutable application image is used for:

- `scheduler`: `python scheduler.py run --poll-seconds 30`;
- `admin`: Review Console HTTP process.

They share the same persistent data directories but run independently and can restart independently.

**C. Kubernetes / managed queue / external orchestrator**  
Rejected for Stage 3. It adds operational complexity with no present benefit for a single-host KABAN deployment.

### 4.2 Service topology

```text
Internet / private network
          |
          v
   protected gateway
          |
          v
      admin service  -----------+
                                |
                                v
                        persistent generated/
                                ^
                                |
    scheduler service ----------+
          |
          +--------------------> persistent runtime/
```

The gateway is the only service allowed to expose the Review Console externally. The `admin` service is reachable only on the internal Compose network.

## 5. Container image

Create one production `Dockerfile` at repository root.

Requirements:

- Python 3.10+ compatible runtime; use a pinned Python 3.10 slim base for parity with the user's current environment;
- install `requirements.txt` before copying mutable source where practical for layer caching;
- run as a non-root application user;
- set `PYTHONUNBUFFERED=1`;
- no `.env`, runtime state, generated output, caches, tests, or local secrets copied into the image;
- image contains application code, project configuration, static project assets, and required Python dependencies only;
- no provider-specific hosting SDKs.

A `.dockerignore` must explicitly exclude secret/runtime/development material.

## 6. Docker Compose

Create `compose.yaml` with at least these application services:

### 6.1 `scheduler`

- uses the common KABAN image;
- command: `python scheduler.py run --poll-seconds 30`;
- `restart: unless-stopped`;
- receives secrets from `env_file: .env`;
- mounts persistent generated and runtime paths;
- no published TCP port;
- healthcheck validates that the scheduler loop has produced a recent heartbeat;
- uses bounded Docker log rotation.

### 6.2 `admin`

- uses the same image;
- starts Review Console bound to `0.0.0.0` inside the Compose network;
- `restart: unless-stopped`;
- receives the same required secrets via `.env`;
- mounts the same persistent generated directory and the runtime directory read/write as required by current Review Console actions;
- exposes its port only to the internal Compose network, not directly to the host Internet interface;
- healthcheck calls an unauthenticated, non-sensitive `/healthz` endpoint.

### 6.3 `gateway`

The production topology includes a reverse-proxy/gateway service responsible for external access, TLS termination, and authentication.

Stage 3 will ship a Caddy-based reference configuration because it is small and operationally simple. The gateway configuration must:

- proxy only to the internal `admin` service;
- never proxy scheduler internals;
- require authentication before Review Console access;
- support HTTPS when a real domain is configured;
- keep credentials outside source control;
- not expose `admin:8088` directly.

For deployments without a public domain, the documented safe mode is private-network/VPN access. Plain unauthenticated public HTTP is explicitly unsupported.

## 7. Review Console runtime changes

Current `admin_app.py` hard-codes `127.0.0.1:8088`. Stage 3 introduces generic runtime configuration:

- `KABAN_ADMIN_HOST` — default remains `127.0.0.1` for backward-compatible local use;
- `KABAN_ADMIN_PORT` — default remains `8088`;
- Docker Compose sets `KABAN_ADMIN_HOST=0.0.0.0`.

Add `GET /healthz` that returns a minimal machine-readable response and no project content, tokens, filesystem paths, or configuration secrets.

Local Windows behavior remains unchanged when these variables are absent.

## 8. Scheduler heartbeat and health

Stage 2 has scheduler state per project/job but no process-level liveness signal. Stage 3 adds a generic KABAN scheduler heartbeat under the runtime root.

Recommended path:

```text
runtime/
└── scheduler/
    └── heartbeat.json
```

The heartbeat contains only operational metadata such as:

```json
{
  "schema_version": 1,
  "updated_at": "2026-09-25T06:00:30+00:00",
  "pid": 123,
  "status": "running"
}
```

Rules:

- heartbeat is written atomically;
- `run_forever()` updates it at startup and at least once per poll cycle;
- no API keys, bot tokens, content, or stack traces are stored;
- a new CLI command `python scheduler.py health` checks heartbeat freshness and exits non-zero when stale/missing;
- freshness threshold must tolerate normal poll jitter and Docker scheduling; default target: heartbeat not older than 120 seconds for the production 30-second poll interval;
- `scheduler.py tick` and `run-now` do not pretend that the long-running scheduler daemon is healthy.

Docker's scheduler healthcheck uses this command.

## 9. Persistence model

Production data must survive image replacement, container restart, and host reboot.

Use host-backed persistent directories under a deployment-local `data/` root:

```text
data/
├── generated/
└── runtime/
```

Container mappings:

```text
./data/generated  -> /app/generated
./data/runtime    -> /app/runtime
```

Application configuration and source remain immutable inside the image.

The existing environment override `KABAN_RUNTIME_DIR` remains supported and Compose sets it consistently to the mounted runtime path.

Generated content remains authoritative at its existing logical paths; Stage 3 must not introduce a second content database.

## 10. Bootstrap of existing generated data

The repository currently contains historical `generated/` data. Production migration must not silently overwrite an existing host `data/generated` directory.

Provide a documented one-time bootstrap command/script with these semantics:

- if destination is empty, copy shipped historical generated data into the persistent directory;
- if destination already contains data, abort unless the operator explicitly selects a merge/copy action;
- never delete host data automatically;
- runtime state is not bootstrapped from development/test artifacts.

## 11. Secrets and configuration

Secrets remain in `.env` on the deployment host and are never copied into the Docker image or release archive.

At minimum:

- `OPENAI_API_KEY`;
- `TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_CHAT_ID`;
- gateway authentication credentials / hashes where applicable.

Requirements:

- `.env` stays git-ignored and docker-ignored;
- `.env.example` contains placeholders only;
- startup/preflight messages may state that a variable is missing, but may not echo secret values;
- verification scans release/image context for accidental secrets and common token/key patterns.

## 12. Logging

Application logs go to stdout/stderr. Docker owns retention.

Compose must configure bounded log rotation for application services, for example:

- driver: `json-file`;
- `max-size`: `10m`;
- `max-file`: `5`.

Do not add an application-specific log database in Stage 3.

Sensitive exception text continues to use the sanitization rules already established by KABAN/CAELUS automation before it reaches persistent runtime state. Logs must not deliberately print secret environment values.

## 13. Restart and crash behavior

`restart: unless-stopped` is required for scheduler and admin.

Expected behavior:

- scheduler process crash -> Docker restarts it;
- admin crash -> Docker restarts only admin;
- gateway crash -> Docker restarts gateway;
- host reboot -> Compose stack resumes when Docker daemon resumes, provided the deployment is installed with the documented boot policy;
- already completed scheduler slots remain protected by Stage 2 state and are not repeated after restart;
- pending retry state remains in persistent runtime storage;
- generated content remains intact.

Stage 3 must not reset scheduler runtime state during container startup.

## 14. Deployment commands and operator workflow

The release must document a small, deterministic production workflow, centered on Docker Compose:

```text
copy .env.example -> .env
fill secrets
prepare persistent data
build/start stack
verify health
inspect scheduler jobs
```

Canonical commands should use `docker compose`, not legacy `docker-compose`.

Include documented commands for:

- build/start;
- stop;
- restart;
- status;
- logs;
- scheduler job listing/status inside container;
- manual `run-now` inside scheduler container;
- backup of `data/generated` and `data/runtime` while preserving permissions;
- upgrade to a new image without deleting persistent data.

No deployment command may contain real user secrets.

## 15. Public access security boundary

Review Console can approve/regenerate/publish content, so it is an administrative interface.

Therefore:

- direct public exposure of `admin_app.py` is forbidden by the reference production Compose file;
- `/healthz` may be accessible internally without auth but contains no sensitive details;
- all human Review Console routes pass through the authenticated gateway;
- scheduler has no HTTP interface;
- Docker socket is never mounted into application containers;
- application containers do not run privileged and do not require host networking.

## 16. Backward compatibility

Stage 3 must preserve all current non-Docker workflows:

```text
python run_daily.py ...
python automation.py ...
python scheduler.py ...
python admin_app.py
python publish_telegram.py ...
```

When deployment environment variables are absent:

- Review Console still defaults to `127.0.0.1:8088`;
- scheduler runtime semantics stay identical to v1.12.1;
- CAELUS project schedule stays `06:00/08:00 Pacific/Auckland`;
- content generation/rendering/publication artifacts must remain byte-compatible where timestamps/runtime metadata are not part of the artifact contract.

## 17. Testing strategy

Implementation follows TDD.

Minimum automated coverage:

1. admin host/port defaults and environment overrides;
2. `/healthz` returns minimal 200 response;
3. heartbeat atomic write/read/fresh/stale/missing/corrupt cases;
4. `scheduler.py health` exit codes;
5. `run_forever()` updates heartbeat without changing scheduler slot semantics;
6. Compose configuration parses and contains expected mounts, commands, healthchecks, restart policy, no direct admin host port;
7. Dockerfile/.dockerignore hygiene tests;
8. release archive does not contain `.env`, runtime state, caches, private keys, or token-like values;
9. existing full unittest/pytest suites remain green;
10. v1.12.1 manual mock generation regression remains unchanged;
11. restart simulation preserves completed slots and pending retries;
12. multi-project scheduler isolation remains green.

Where Docker is available in the execution environment, run an actual `docker compose config` and container smoke test. If Docker daemon is unavailable, static Compose validation remains mandatory and the limitation must be stated explicitly in the verification report rather than fabricated as a runtime PASS.

## 18. Release verification

The v1.13 release is not complete until a clean extracted archive passes:

- full Python test suites;
- scheduler `list`, `status`, `health` behavior;
- Review Console local smoke;
- static Compose validation;
- Docker image/Compose smoke when Docker is available;
- secret/hygiene scan;
- v1.12.1 artifact regression;
- persistent-data restart simulation;
- SHA-256 generation.

The release verification report must distinguish tests run against source workspace from tests run against the final extracted ZIP.

## 19. Explicit non-goals for Stage 3

Stage 3 does **not** add:

- auto-approve;
- CAELUS EN jobs;
- Instagram or other publication channels;
- analytics dashboard;
- external database;
- Kubernetes;
- multi-host scheduler leader election;
- remote shell/admin execution;
- a new CAELUS-specific deployment subsystem;
- provider-specific cloud APIs or Terraform;
- automated off-site backups.

Those are future stages after the single-host production deployment is proven stable.

## 20. Deployment target assumption

The Stage 3 artifact is intentionally provider-neutral and targets a **Docker-capable Linux host**. Selecting and provisioning a specific VPS/PaaS account is outside the code artifact and can be done after v1.13 is verified.

This avoids coupling KABAN to a hosting vendor and keeps the same release deployable to a VPS, home server, or compatible container host.

## 21. Final architecture after Stage 3

```text
Docker-capable Linux host
│
├── gateway                  generic deployment layer
│      └── authenticated HTTPS/private ingress
│
├── admin                    KABAN/CAELUS Review Console
│      └── shared generated + runtime
│
├── scheduler                KABAN universal scheduler
│      └── ProjectRegistry
│            └── projects/caelus/scheduler.py
│                   └── CAELUS automation
│
└── data/
       ├── generated/         persistent
       └── runtime/           persistent
```

The important boundary remains unchanged: **KABAN decides how the production platform runs; each Project decides what its jobs mean.**
