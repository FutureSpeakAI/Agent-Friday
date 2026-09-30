# S5: floci as the salon's local cloud emulator, the spike

**Status:** code audit, coverage map and licence check done 2026-09-30, from the
source at commit 865dd1d (2026-10-01 UTC) and the project's docs. **Not yet
run on this PC:** the RAM measurement, the egress-blocked run and the parity
tests need a Java 25 toolchain that is not installed here (see "What stands
between this and a run"). Recommendation below is conditional on those.
Report only; nothing installed, nothing bundled.

**Question.** Spec §4.6.1 sizes Phase 5, Friday's own local cloud emulator, at
24–34 agent-days. floci (https://github.com/floci-io/floci) claims to be a
LocalStack drop-in on :4566 with no account, no token, no feature gates, no
telemetry, ~24 ms startup and ~13 MiB idle, MIT-licensed. Could it replace
most of Phase 5? The rule it must pass first: no component ever phones home.

## What floci is (VERIFIED from source and docs)

- **A Java 25 / Quarkus 3.39 application** (`pom.xml`: `maven.compiler.release` 25),
  about 4,800 Java files, one repository holding the engine, the docs and
  the compatibility suites. Package root `io.github.hectorvent.floci`.
- **Distribution:** a Docker image (`floci/floci`, ~90 MB) and a Homebrew tap
  (macOS/Linux). **The GitHub release has no binary assets.** The ~40 MB
  native binary in the README is what *you* build with GraalVM or Mandrel 25
  (`make native-host`, "about four minutes"); CI builds it on Ubuntu only.
  **No Windows binary is published anywhere.**
- **Storage:** `FLOCI_STORAGE_MODE` = `memory` (default) | `persistent` |
  `hybrid` | `wal`, data under `FLOCI_STORAGE_PERSISTENT_PATH` (`./data`),
  per-service overrides, journaling for S3 metadata and CloudWatch Logs.
- **Bind:** `QUARKUS_HTTP_HOST` defaults to `127.0.0.1`; exposing beyond
  loopback needs `FLOCI_SECURITY_ALLOW_UNSAFE_NETWORK_EXPOSURE=true`. Good
  defaults for us.
- **Licence:** MIT, "Copyright (c) 2025 Floci and its contributors", one
  LICENSE file, no Commons Clause, no dual licence, no trademark clause
  found in the tree or the docs. The docs say "MIT licensed. Fork it, extend
  it, embed it." Bundling or recommending it is permitted; attribution and
  the licence text travel with it. (Docker-backed services pull third-party
  images such as `postgres:16-alpine`, each under its own licence; those
  are the user's choice per service, not part of floci.)

## 1. Telemetry and phone-home (code audit; run pending)

Method: every `.java`, `.properties`, `.yml` under `src/main` was searched
for telemetry-shaped words, for URLs, for HTTP clients and for Docker image
pulls; the Quarkus config (`application.yml`) was read for exporters.

- **No telemetry, analytics, crash reporter, update check or usage report
  exists in the engine.** The only matches for those words are AWS services
  it emulates (Kinesis Analytics, API Gateway usage plans, RUM). No
  OpenTelemetry or Micrometer exporter is configured.
- **No external endpoint is built in.** Every URL in the source is a spec
  link in a comment, an XML/JSON schema, an emulated AWS hostname, or a
  loopback/placeholder (`http://floci`, `https://pricing-snapshot.floci.local/`,
  the ECS credentials address `169.254.170.23`).
- **Outbound HTTP clients exist only for emulated features** that call
  endpoints the *user* configures: API Gateway HTTP/WebSocket integrations,
  CloudFront origins, SNS HTTP subscriptions, Cognito's OIDC client for a
  configured identity provider, ELB health checks, and sidecar clients
  (AppSync, CodeArtifact, Flink, Kafka REST, Bedrock proxy backend, the
  optional DynamoDB Local backend). None of these fires unless a resource
  is created that names such an endpoint. Under Friday's rules these are the
  codebase's own traffic and go through the salon proxy like everything else.
- **Docker image pulls** happen only for Docker-backed services (Lambda, RDS,
  ECS, EC2, EKS, OpenSearch, MSK, Neptune, DocumentDB, CodeBuild, Flink) and
  for the web console sidecar on first open. These reach Docker Hub and other
  registries: expected, and off by not using those services.
- The docs state it: "No auth tokens, no sign-ups, no telemetry."

**Still owed:** the run under a per-process connection log (and, when the
owner allows, a packet capture) to prove that the running process opens no
outbound socket while the stateless services are exercised. The code says it
will not; the rule wants it seen.

## 2. Windows, Docker and RAM

- **Natively on Windows without Docker: yes for the stateless services, by
  building it.** The code handles native Windows explicitly:
  `DockerClientProducer` falls back to Docker Desktop's named pipe
  (`npipe:////./pipe/docker_engine`) only when a Docker-backed service is
  used, and the docs describe "Running natively on Windows (not inside WSL or
  a container)". The Docker client is a lazy CDI producer; startup does not
  touch Docker (INFERRED from `EmulatorLifecycle`, which mentions Docker only
  when stopping sidecars). ECS, RDS-style and MQ services offer
  `mock: true` for metadata-only operation without Docker. **Lambda has no
  in-process executor:** `FLOCI_SERVICES_LAMBDA_EXECUTOR` is `docker` or
  `kubernetes`. Lambda-style functions therefore stay Friday's own (§4.6.1's
  "local subprocesses"), or need Docker.
- **What a Windows user would need:** either Docker Desktop (which phones
  home at every start, S3 finding, so not a required dependency), or a JDK 25
  to run the JVM build, or a GraalVM/Mandrel 25 build of the native binary
  that Friday ships. The 13 MiB idle figure is the native binary's; the JVM
  build will sit far higher (a Quarkus app on JDK 25 idles in the low
  hundreds of MB; UNMEASURED here). For ordinary users the honest path is a
  Friday-built native binary per platform, or `mock` modes.
- **RAM on this PC: UNMEASURED** until the run (see below).

## 3. Coverage against the salon's needs (docs and code; parity run pending)

| Salon needs | floci | How it runs | Notes |
|---|---|---|---|
| S3 | 17 documented actions; versioning, multipart, pre-signed URLs, Object Lock, event notifications | in-process | not implemented: metadata annotation tables, redirect-all website routing (§Not Implemented) |
| DynamoDB | 43 actions; GSI/LSI, Query/Scan, TTL, transactions, batch, Streams with Lambda source mapping | in-process (optional external DynamoDB Local backend) | none marked missing |
| SQS | 28 actions; standard + FIFO, DLQ, visibility, batch, tags | in-process | none marked missing |
| SNS | 55 actions; topics, subscriptions, SQS/Lambda/HTTP delivery | in-process | HTTP delivery is outbound by design |
| Lambda | 56 actions; runtimes, warm pool, aliases, Function URLs | **Docker only** | two items in its Not Implemented list; no Docker-less mode |
| Cognito / OIDC | 80 actions; user pools, app clients, auth flows, per-pool `.well-known/openid-configuration` and `jwks.json`, a relaxed `/oauth2/token`, `/oauth2/authorize` | in-process | MFA shortcuts and per-client session validity not yet |
| Secrets Manager | 25 actions; versioning, policies, tags, rotation via Lambda or the owning service, scheduled rotation sweep | in-process | rotation *via Lambda* needs Docker |
| Also useful | IAM/STS, KMS, SSM Parameter Store, Step Functions, EventBridge (+Pipes, Scheduler), SES | in-process | covers §4.6.1's auth stub, key-value and queue shims |

**Parity tests exist in the tree** for exactly these seven services
(`compatibility-tests/sdk-test-go/tests/{s3,dynamodb,sqs,sns,lambda,cognito,secretsmanager}_test.go`,
986 lines, Go SDK v2; Go 1.26 is installed here) plus AWS CLI, Java, CDK,
Terraform and OpenTofu suites. Running them against a local floci is the
parity step; it waits on the toolchain below.

## What stands between this and a run

- This PC has **Java 17**; floci needs **Java 25** and Maven 3.9+ (the tree
  ships `mvnw`, which downloads Maven itself). A JDK 25 is a user-level
  unzip (~200 MB) plus the Maven wrapper's downloads (Maven ~10 MB and a
  dependency tree of several hundred MB). Nothing system-level, no admin, no
  reboot, but a large download and a first build of several minutes.
- The native binary additionally needs GraalVM or Mandrel 25 (~400 MB) and
  about four minutes of CPU per build.
- Docker is not needed for the stateless run, and the spike would not use it.

## Recommendation (conditional on the run)

**Wrap it, don't rebuild it.** If the run confirms what the code says (no
outbound socket, RAM within reason), floci should become the Phase 5
backend for S3, DynamoDB, SQS, SNS, Secrets Manager, Cognito/OIDC, IAM/STS,
KMS, SSM, EventBridge and Step Functions, sitting behind Friday's own
gateway from §4.6.1 (service detection, the Backstage status panel, the
coverage table, snapshots per codebase, "go live"), with Friday's own
subprocess runner for Lambda-style functions and mock modes where floci
wants Docker. That keeps the parts §4.6.1 says Friday must own (the gateway,
the panel, the receipts, the no-egress guarantee) and drops most of the moto
integration and the per-service shims. Sizing falls from 24–34 agent-days to
roughly **10–14**: the gateway and panel (5–7), the floci lifecycle and
native builds per platform (3–4), parity in CI (2–3).

**Adopt outright** (point codebases straight at :4566) fails our rules: no
gateway means no per-codebase egress ledger and no Backstage panel.
**Keep building our own** stays the fallback if the run shows egress, if the
JVM footprint is unacceptable and a native build cannot be shipped for
Windows, or if the project's health changes; the MIT licence means a fork
is always available.

**Open items for the owner:** the JDK 25 install for the run; whether Friday
ships a Friday-built native binary per platform (a build pipeline, not a
dependency on floci's releases); and whether Lambda-style functions stay
Friday's own runner (recommended) or require Docker.

## Sources

- https://github.com/floci-io/floci at 865dd1d: `pom.xml`, `Makefile`,
  `README.md`, `LICENSE`, `docs/index.md`, `docs/getting-started/installation.md`,
  `docs/configuration/{docker,storage,environment-variables}.md`,
  `docs/services/{s3,dynamodb,sqs,sns,lambda,cognito,secrets-manager}.md`,
  `src/main/java/io/github/hectorvent/floci/core/common/docker/DockerClientProducer.java`,
  `services/lambda/launcher/LambdaRuntimeLauncherProducer.java`,
  `services/dynamodb/backend/DynamoDbBackendSelector.java`,
  `lifecycle/EmulatorLifecycle.java`, `.github/workflows/release.yml`.
- https://floci.io/floci/ and its installation, environment-variables and
  storage pages (read 2026-09-30).
- GitHub API: organisation repositories and the 2.1.0 release (no assets).
