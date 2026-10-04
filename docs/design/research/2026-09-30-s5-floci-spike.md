# S5: floci as the salon's local cloud emulator, the spike

**Status:** code audit, coverage map and licence check done 2026-09-30, from the
source at commit 865dd1d (2026-10-01 UTC) and the project's docs. **Run on
this PC 2026-09-30** (JVM build, user-level JDK 25 in a scratch folder,
nothing system-wide): connection log, RAM, and the parity suites for the six
in-process services, all under "The run" below. The recommendation holds.
Report only; nothing bundled.

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

## 1. Telemetry and phone-home (code audit, confirmed by the run)

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

**Seen in the run:** two starts, about four and five minutes of life each,
the six in-process services exercised by their own parity suites; the only
TCP endpoints the Java process ever owned were its listener on
`127.0.0.1:4566` and the suites' loopback connections to it. Zero remote
addresses, zero DNS. A packet capture (admin) remains the stronger proof if
the owner wants it; see "The run" for the method and its limit.

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
- **RAM on this PC, JVM build (measured):** 250–279 MB resident idle, 300–319
  MB after the suites, 333 MB peak working set, with `-Xmx1024m`. Ready in
  38–48 s from the launch command (JVM start, 60+ services, 4800 classes),
  against the 24 ms the native binary claims. An ordinary user would need the
  native build; the JVM figure is what this PC can measure.
- **Two listeners on all interfaces by default.** Besides `127.0.0.1:4566`
  (honouring `QUARKUS_HTTP_HOST`), the default start also binds `0.0.0.0` and
  `[::]` on **9169** (the EC2 instance-metadata server) and **12000** (the
  Lambda runtime API). Neither honours the HTTP host setting; both go away
  with `FLOCI_SERVICES_EC2_ENABLED=false` and `FLOCI_SERVICES_LAMBDA_ENABLED=false`,
  after which the process holds exactly one socket, `127.0.0.1:4566`
  (verified by listing the process's listeners). Friday's wrapper must set
  both, or the box is reachable from the LAN.
- **Memory mode still writes to disk:** three small `resourceexplorer2-*.json`
  files (11 KB) land under `./data` because that service's storage is
  "hybrid" regardless of `FLOCI_STORAGE_MODE=memory`. Harmless, but the
  wrapper should give floci a per-codebase working directory so nothing
  lands in the codebase itself.

## 3. Coverage against the salon's needs (docs and code; parity run below)

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
Terraform and OpenTofu suites. Their results against this PC's floci are in
"The run".

## The run (2026-09-30, this PC)

**Toolchain, all user-level in a scratch folder, nothing system-wide:** Temurin
JDK 25.0.4.1 (SHA-256 checked against the published value), the tree's Maven
wrapper and its dependency tree (~190 MB in `~/.m2`), Go 1.26 already present.
`./mvnw package -DskipTests` built the JVM app in 9 min 39 s; `quarkus-app/`
is 91 MB. Docker was never started. The native binary (GraalVM/Mandrel 25,
~400 MB more) was not built.

**Method.** floci started by PowerShell with `-PassThru`, so its real process
id is known; a logger polled that process's TCP connections and UDP endpoints
every 500 ms (`Get-NetTCPConnection -OwningProcess`) and wrote each new
endpoint once. Settings: `FLOCI_STORAGE_MODE=memory`,
`QUARKUS_HTTP_HOST=127.0.0.1`, `-Xmx1024m`; pass two added the EC2 and Lambda
switches above. The seven suites ran from
`compatibility-tests/sdk-test-go` with `FLOCI_ENDPOINT=http://127.0.0.1:4566`
and the dummy `test`/`test` credentials. A positive control ran the same
logger against a PowerShell process making one HEAD request to GitHub: it
caught the connection (one `Established` line to a `:443` remote). The
method's limit: a connection that opens and fully closes inside one 500 ms
poll could escape it; the code audit is the answer to that gap, and a packet
capture would close it.

**Egress.** Pass one and pass two together: the Java process owned
`127.0.0.1:4566 Listen`, loopback `Established` connections from the suites,
and (pass one only) the two all-interface listeners on 9169 and 12000. No
remote address, no DNS, nothing on UDP. **Zero egress.**

**RAM.** Idle 279 MB (pass one, all services), 250 MB (pass two, EC2 and
Lambda off); after the suites 319 / 300 MB; peak working set 333 MB.

**Parity, Go SDK v2 suites, pass two (`go test -v`, each suite's tests run
by name, `-count=1`):**

| Suite | Result | Tests | Subtests | Wall time |
|---|---|---|---|---|
| s3 (TestS3, LocationConstraint, NonASCIIKey, MultipartCopyNonASCIIKey, LargeObject) | ok | 5 pass | 11 pass | 4.6 s |
| dynamodb | ok | 1 pass | 10 pass | 0.5 s |
| sqs | ok | 1 pass | 11 pass | 0.4 s |
| sns | ok | 1 pass | 9 pass | 0.5 s |
| secretsmanager | ok | 1 pass | 6 pass | 0.8 s |
| cognito | ok | 2 pass | none | 2.9 s |
| lambda (pass one) | **FAIL** | 1 fail | 1 fail | n/a |

Six of seven green with no failures or skips. Lambda fails exactly as the
code predicted: `ContainerLauncher` tries Docker Desktop's named pipe
(`dockerDesktopLinuxEngine`), finds nothing, and the function never runs.
The same missing pipe produces one WARN at startup from the ECS container
sweep; it is logged and ignored. The suites in the tree are smoke-depth (5 to
11 checks per service), not the AWS conformance suite; they prove the SDK
wire format and the common paths, not every edge.

**Disk.** C: stayed between 24 and 18 GB free through the build and both
runs, above the 15 GB floor. Build outputs and the JDK live in the session's
scratch folder and can be deleted.

## Recommendation (the run confirms it)

**Wrap it, don't rebuild it.** The run confirmed what the code said: no
outbound socket, RAM in the low hundreds of MB for the JVM build, six of six
in-process services passing their suites, and Lambda Docker-only. floci
should become the Phase 5
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

Three findings from the run shape the wrapper and are already inside the
10–14 days: it must set `FLOCI_SERVICES_EC2_ENABLED=false` and
`FLOCI_SERVICES_LAMBDA_ENABLED=false` (or the box listens on the LAN), give
floci a working directory outside the codebase (memory mode still writes
three files), and expect 40–50 s to ready on the JVM build, so Friday starts
it when a codebase opens, not when a request arrives. The first two are a
rule in code already: `services/floci_box.py` holds the launch environment
(loopback host and port, both switches off, memory storage under the box's
own folder, nothing of the owner's environment but the path) and the listener
audit a started box must pass, `require_loopback_only`, which refuses any
bind beyond 127.0.0.1 or ::1. The lifecycle that starts the process is still
Phase 5.

**Open items for the owner:** whether Friday ships a Friday-built native
binary per platform (a build pipeline, not a dependency on floci's releases;
the JVM build needs a 300 MB JDK on the user's PC and idles near 300 MB);
and whether Lambda-style functions stay Friday's own runner (recommended) or
require Docker. A packet capture (admin) is available as the stronger egress
proof if wanted.

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
