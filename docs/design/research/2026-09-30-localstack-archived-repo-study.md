# LocalStack (archived) — what the backstage can learn from it

Status: research spike S4 for the vibe-coding salon. Read-only study of the public `localstack/localstack` repository
at tag `v4.14.0` (the last release, published 2026-02-26) and its `main` branch (archived and read-only, last push
2026-03-23). Nothing here is built; every claim cites the URL it was read from. Anything not verified is marked
UNKNOWN. LocalStack itself is ruled out for Friday (account plus telemetry); this is a study of patterns and of what
must never be imported.

Repository facts: default branch `main`; GitHub reports the licence as `NOASSERTION` / "Other" (reason UNKNOWN; the
file itself is an Apache-2.0 header, see section 6); description "A fully functional local AWS cloud stack". Root at
`v4.14.0` holds `LICENSE.txt`, `README.md`, `DOCKER.md`, `plux.ini`, `pyproject.toml`, `localstack-core/`, `tests/`,
`scripts/`, `docs/`, `bin/`. There is no `NOTICE` file at the root.

## 1. The single-port gateway and request routing

**One port.** `constants.DEFAULT_PORT_EDGE = 4566`; `GATEWAY_LISTEN` defaults to `HostAndPort(host=default_ip,
port=constants.DEFAULT_PORT_EDGE)` in `config.populate_edge_configuration()`. `GATEWAY_SERVER` selects the HTTP
server: default `"twisted"`, alternatives `hypercorn` and (dev) `werkzeug`
(`localstack-core/localstack/aws/serving/edge.py`, `serving/{twisted,hypercorn,werkzeug,wsgi,asgi}.py`). The Docker
invocation is `docker run --rm -it -p 4566:4566 -p 4510-4559:4510-4559 localstack/localstack` (`DOCKER.md`).

**Handler chain.** `localstack-core/localstack/aws/gateway.py` subclasses `rolo.gateway.Gateway`; `handle()` is
`self.new_chain().handle(context, response)`. `aws/chain.py` re-exports rolo's `HandlerChain` and the composite
handlers; a `Handler` is `Callable[[HandlerChain, RequestContext, Response], None]`, an `ExceptionHandler`
additionally receives the exception. The concept doc states the gateway "is a simple interface: `process(Request,
Response)`" and that the chain "knows about three different handlers: Request Handlers, Response Handlers, and
Exception Handlers" (`docs/localstack-concepts/README.md`). The chain itself lives in the separate `rolo>=0.8.1`
package (`pyproject.toml`), not in this repo.

**Order of the chain** (`localstack-core/localstack/aws/app.py`, class `LocalstackAwsGateway`), request handlers:
`add_internal_request_params`, `handle_runtime_shutdown`, metric item creation, `load_service_for_data_plane`,
`preprocess_request`, `enforce_cors`, `content_decoder`, `validate_request_schema`, `serve_localstack_resources`
(`/_localstack/*`), `serve_edge_router_rules`, `parse_service_name`, `parse_pre_signed_url_request`,
`inject_auth_header_if_missing`, `add_region_from_header`, `rewrite_region`, `add_account_id`, `parse_trace_context`,
`parse_service_request`, `serve_custom_service_request_handlers`, `load_service`, `service_request_router`, then
`EmptyResponseHandler(404, b'{"message": "Not Found"}')`. Exception handlers: `log_exception`,
`serve_custom_exception_handlers`, `handle_service_exception`, `handle_internal_failure`. Response handlers:
`validate_response_schema`, `modify_service_response`, `parse_service_response`, `run_custom_response_handlers`,
`add_cors_response_headers`, `log_response`, `count_service_request` (analytics, section 7), metric update.
Finalizers: `set_close_connection_header`, `run_custom_finalizers`. The handler instances are declared in
`aws/handlers/__init__.py`; the modules are `aws/handlers/{analytics,
auth,codec,cors,exceptions,fallback,internal,internal_requests,legacy,logging,
metric_handler,presigned_url,proxy,region,response,routes,service,service_plugin, tracing,validation}.py`.

**Request context** (`aws/api/core.py`, `RequestContext`): `request`, `service` (botocore `ServiceModel`), `protocol`,
`operation` (`OperationModel`), `region`, `partition`, `account_id`, `request_id`, `service_request`,
`service_response`, `service_exception`, `internal_request_params`, `trace_context`.

**Matching a request to a service** (`aws/protocol/service_router.py`, `determine_aws_service_model`):
`_extract_service_indicators` pulls the signing name from the `Authorization` header's `Credential=` scope, the target
prefix and operation from `X-Amz-Target` (or the Smithy RPC v2 path), plus host and path. Resolution order: (1) unique
signing name, with `custom_signing_name_rules` for collisions (e.g. `apigateway` `/v2` paths go to `apigatewayv2`);
(2) `X-Amz-Target` prefix, checked against the operation index; (3) `custom_path_addressing_rules` (SQS queue URLs via
`is_sqs_queue_url`, Lambda `/2015-03-31/functions`); (4) host rules: endpoint-prefix match excluding S3 virtual hosts
(`".s3." not in host`), `custom_host_addressing_rules` for `.lambda-url.` and `.s3-website.`; (5) the form-encoded
`Action`/`Version` parameters for query/ec2 protocols; (6) `resolve_conflicts` (SQS JSON vs `sqs-query`); (7)
`legacy_s3_rules`, the "increasingly greedy" S3 fallbacks (pre-signed `AWSAccessKeyId`/`Signature`/
`X-Amz-Credential`, `AWS id:key` auth). The indexes come from `aws/spec.py`
(`ServiceCatalog.by_signing_name/by_target_prefix/by_operation`, endpoint-prefix index), built lazily and cached with
dill.

**Matching an operation.** For rest-json/rest-xml, `aws/protocol/op_router.py` (`RestServiceOperationRouter`) turns
each botocore `requestUri` into a werkzeug rule (`GreedyPathConverter` for multi-segment params); same path+method
collisions become a `_RequestMatchingRule` of `_RequiredArgsRule`s scored `10 + 10*len(required_query_args) +
10*len(required_header_args)` (minus 5 if deprecated). For query/json protocols the parser reads `Action` or
`X-Amz-Target` (`aws/protocol/parser.py`, classes `QueryRequestParser`, `JSONRequestParser`, `RestJSONRequestParser`,
`RestXMLRequestParser`, `EC2RequestParser`, `CBORRequestParser`, `RpcV2CBORRequestParser`, plus `S3RequestParser` and
`SQSQueryRequestParser`; factory `create_parser(service, protocol)`; errors `ProtocolParserError` (4xx),
`UnknownParserError`, `OperationNotFoundParserError`).

**Auth header parsing → account and region** (`aws/handlers/auth.py`, `aws/accounts.py`, `aws/handlers/region.py`):
`MissingAuthHeaderInjector` fabricates an `Authorization` header with access key `"injectedaccesskey"` so plain-URL S3
GETs work. `AccountIdEnricher` extracts the access key id; `get_account_id_from_ access_key_id` uses a 12-digit key
literally as the account id, decodes `LSIA`/`LKIA` keys when `PARITY_AWS_ACCESS_KEY_ID` is set, ignores real
`AKIA`/`ASIA` keys, and falls back to `DEFAULT_AWS_ACCOUNT_ID = "000000000000"`; it also sets the `x-moto-account-id`
header so moto uses the same account. `RegionContextEnricher` reads the region from the credential scope;
`RegionRewriter` rewrites unknown regions (and the header) to `us-east-1`.

**Error shaping.** `ServiceException` carries `code`, `status_code`, `sender_fault`, `message`;
`CommonServiceException(code, message, status_code=400, sender_fault=False)` is for "Common Errors"
(`aws/api/core.py`). `aws/protocol/serializer.py` `serialize_error_to_response` looks up
`shape_for_error_code(error.code)` and emits per protocol: query/ec2 XML
`<ErrorResponse><Error><Code/><Message/><Type>Sender` (+ `RequestId`), JSON `{"__type": code, "message": ...}` with
header `X-Amzn-Errortype`, rest protocols add `x-amz-request-id`; SQS maps JSON codes to legacy query codes.
`ServiceExceptionSerializer` (`aws/handlers/service.py`) turns `NotImplementedError` into 501 `InternalFailure`, moto
`RESTError` into a `CommonServiceException`, and anything else into 500 `InternalError` with a stack trace only if
`INCLUDE_STACK_TRACES_IN_HTTP_RESPONSE`. The last resort, `aws/handlers/fallback.py` `InternalFailureHandler`, returns
500 with `{"error": "Unexpected exception", "message": str(exception), "type": <class name>}`.

Other chain members worth noting: `codec.ContentDecoder` (gzip only, `SKIP_GZIP_SERVICES = ["s3"]`);
`cors.CorsEnforcer` (403 for origins outside `ALLOWED_CORS_ORIGINS`, which hard-codes `app.localstack.cloud`; flags
`DISABLE_CORS_CHECKS`, `DISABLE_CORS_HEADERS`, `EXTRA_CORS_ALLOWED_ORIGINS`); `internal.LocalstackResourceHandler`
serving `INTERNAL_RESOURCE_PATH = "/_localstack"` (`/_localstack/health`, `/info`, `/plugins`, `/init`,
`/init/<stage>`, `/config`, `/diagnose`, `/usage` in `services/internal.py`); `validation.py` validating only
`/_localstack` and `/_aws` paths against `openapi.yaml` with openapi-core; `internal_requests.py` + `aws/connect.py`
letting one service call another through the same gateway with an `x-localstack-data` header carrying `_SourceArn`/
`_ServicePrincipal` and internal credentials `"__internal_call__"`.

## 2. The service-provider plugin model

**Discovery.** Providers are plux plugins in namespace `PLUGIN_NAMESPACE = "localstack.aws.provider"`, named
`<api>:<provider>` (`services/plugins.py`). `pyproject.toml` declares `entry-points = { file = ["plux.ini"] }` with
`[tool.plux] entrypoint_build_mode = "manual"`; `plux.ini` lists 48 `localstack.aws.provider` entries (e.g.
`sqs:default = localstack.services.providers:sqs`, `lambda:default`, `lambda:v2`, `cloudformation:engine-legacy`) plus
namespaces for hooks (`localstack.hooks.on_infra_start` etc.), `localstack.init.runner`, `localstack.runtime.server`,
`localstack.packages`, `localstack.openapi.spec`, `localstack.cloudformation.resource_providers`. Each entry is a
factory function decorated `@aws_provider(...)` in `services/providers.py` that returns a `Service`.
`ServicePluginManager.get_active_provider` resolves the configured provider (`PROVIDER_OVERRIDE_*` env vars) with
fallback to `default`; services load lazily on first request (`aws/handlers/service_plugin.py` `ServiceLoader` →
`ServiceManager.require` → `request_router.add_skeleton(service.skeleton)`), guarded by per-service locks.
`ServiceState` is `AVAILABLE, STARTING, RUNNING, STOPPING, STOPPED, ERROR, DISABLED`.

**How many sat on moto.** In `services/providers.py` at `v4.14.0`, 25 declarations wrap the provider in
`MotoFallbackDispatcher` (acm, apigateway ×3, config, cloudwatch v1, ec2, iam, sts, logs, redshift, route53,
route53resolver, s3control, scheduler, secretsmanager, ses, ssm, events v1/legacy, swf, resourcegroupstaggingapi,
resource-groups, support, transcribe); 18 are plain `Service`s or use `HttpFallbackDispatcher` (cloudformation ×2,
cloudwatch default/v2, dynamodb ×2 and kinesis via `HttpFallbackDispatcher` to an external HTTP backend,
dynamodbstreams ×2, es, firehose, kms, lambda ×3, opensearch, s3, sns, sqs, events default/v2, stepfunctions ×2).
Counts include provider variants. The moto dependency is a fork: `moto-ext[all]>=5.1.22`.

**How moto is wrapped** (`services/moto.py`). `MotoFallbackDispatcher` "Wraps a provider with a moto fallthrough
mechanism": each `@handler` method that raises `NotImplementedError` falls through to `_proxy_moto`.
`call_moto(context, request)` invokes moto in-process: `load_moto_routing_table` builds a werkzeug rule map from the
moto backend's `flask_paths` (`strict_slashes=False`), `get_dispatcher` matches the raw path, and the werkzeug request
is handed to moto's response class; moto `RESTError`s become `CommonServiceException`s. `call_moto_with_request`
re-serialises a modified `ServiceRequest` first. The generic version is `aws/forwarder.py`
(`ForwardingFallbackDispatcher`, `create_aws_request_context` re-serialising with botocore,
`parameter_validation=False`). Providers patch moto where it is wrong: `services/secretsmanager/provider.py` reaches
`secretsmanager_backends[context. account_id][context.region]`, validates then `return call_moto(context, request)`,
and applies `@patch(FakeSecret.__init__)`, `@patch(SecretsManagerBackend.get_secret_value)` etc.

**State per account/region** (`services/stores.py`, docstring: stores "are analogous to Moto's BackendDict").
`BaseStore` subclasses declare descriptors: `LocalAttribute` (per account+region), `CrossRegionAttribute` (per
account, in `_global`), `CrossAccountAttribute` (in `_universal`). `AccountRegionBundle[account] [region]` yields the
store, validating both keys. Example, `services/sqs/models.py`: `class SqsStore(BaseStore): queues =
LocalAttribute(default=dict); deleted = ...; move_tasks = ...; tags = LocalAttribute(default=Tags)` and `sqs_stores =
AccountRegionBundle("sqs", SqsStore)`; `SqsProvider.get_store(account_id, region)` returns
`sqs_stores[account_id][region]` (`services/sqs/provider.py`).

**API generation from botocore specs (ASF).** `aws/scaffold.py` `generate SERVICE` loads the botocore model
(`aws/spec.py` `load_service`, with `spec-patches.json` applied by `PatchingLoader` via jsonpatch, and a
`LocalStackBuiltInDataLoaderMixin` for specs botocore dropped) and renders `localstack/aws/api/<service>/__init__.py`:
TypedDict shapes, enums, and an abstract `<Service>Api` class whose methods carry `@handler("OperationName")`;
`upgrade` regenerates all of them. `aws/skeleton.py` `create_dispatch_table` scans the provider's MRO for `@handler`
functions, `Skeleton.invoke` parses the request and calls the handler either with the `ServiceRequest` or with
expanded kwargs (`xform_name(k)`), and serialises the result; a missing handler consults the AWS catalog for an
"availability" error. 37 generated API packages exist under `aws/api/` at `v4.14.0`.

## 3. Persistence, init hooks, awslocal

**State API in the open tree** (`localstack-core/localstack/state/`): `core.py` defines `StateContainer` ("can in
principle be anything"; supported: moto `BackendDict`, `AccountRegionBundle`, `AssetDirectory`), `StateVisitor.visit`,
`StateVisitable.accept_state_visitor`, and `StateLifecycleHook` with `on_before/after_state_{reset,save,load}`.
`inspect.py` `ReflectionStateLocator` finds `<service>_stores` in `localstack.pro.core.services.<svc>.models` then
`localstack.services.<svc>.models`, and `<service>_backends` in `moto.<svc>.models`. `pickle.py` is "A small wrapper
around dill" with `register`/`reducer` for custom reducers; `codecs.py` is the encoder/decoder factory. `snapshot.py`
is 24 lines: only `SnapshotPersistencePlugin` (namespace `"localstack.persistence.snapshot"`) with
`create_load_snapshot_visitor` / `create_save_snapshot_visitor` raising `NotImplementedError`. Providers expose state
via `accept_state_visitor` (`SqsProvider`: `visitor.visit(sqs_stores)`).

**What is not there.** The save/load visitors, the directory layout and the request-time trigger are not in this
repository; `services/internal.py` has no `/_localstack/state` endpoint. The config surface exists — `PERSISTENCE`,
`SNAPSHOT_SAVE_STRATEGY` (`on_shutdown, on_request, scheduled, manual`), `SNAPSHOT_LOAD_STRATEGY` (`on_startup,
on_request, manual`), `SNAPSHOT_FLUSH_INTERVAL` (15 s), deprecated `DATA_DIR`, `dirs.data =
"{DEFAULT_VOLUME_DIR}/state"` ("holds localstack state, pods") — and the concept doc says "the states of all providers
are restored when the LocalStack instance is restarted", but the implementation is UNKNOWN here (the imports point at
`localstack.pro.core`, which is closed). Cloud pods: no module in the open tree.

**Init hooks** (`runtime/init.py`): `Stage` = `BOOT, START, READY, SHUTDOWN`; directories `boot.d`, `start.d`,
`ready.d`, `shutdown.d` under `dirs.init = "/etc/localstack/init"`; runners are plux plugins in
`"localstack.init.runner"` (`ShellScriptRunner` for `.sh`, `PythonScriptRunner` using `exec` for `.py`); files run in
sorted order, each tracked `UNKNOWN → RUNNING → SUCCESSFUL | ERROR`; a failure is logged and the stage continues; AWS
credential/region env vars are restored after each script; status is served at `/_localstack/init[/<stage>]`. Runtime
hooks (`runtime/hooks.py`) are plux plugins too: `on_runtime_create`, `on_infra_start`, `on_pro_infra_start`,
`on_infra_ready`, `on_infra_shutdown`, `configure_localstack_container`, `prepare_host`, ordered by `hook_priority`.

**awslocal** (separate repo `localstack/awscli-local`, Apache-2.0): "a thin wrapper around the `aws` command line
interface". `bin/awslocal` picks the endpoint (`AWS_ENDPOINT_URL` wins; else `LOCALSTACK_HOST`, default
`localhost:4566`), injects `--endpoint-url`, sets `AWS_ACCESS_KEY_ID=test`/`AWS_SECRET_ACCESS_KEY=test` and
`AWS_DEFAULT_REGION=us-east-1` when absent, then `os.execvpe()`s the real CLI (or runs `awscli.clidriver.main()`
in-process).

## 4. Per-API coverage tracking

- `scripts/capture_notimplemented_responses.py`: reads the service list from `/_localstack/health`, fires a generated
  request for every botocore operation (`simulate_call`), classifies via `map_to_notimplemented` (501, specific 404s,
  parse errors, DynamoDB `UnknownOperationException`), and writes `implementation_coverage_full.csv` (`service,
  operation, status_code, error_code, error_message, is_implemented`) and `implementation_coverage_aggregated.csv`
  (`service, implemented_count, full_count, percentage`).
- `aws/handlers/metric_handler.py` + `testing/pytest/metric_collection.py`: when
  `LOCALSTACK_INTERNAL_TEST_COLLECT_METRIC` is set, every request during the test run is logged to
  `metric-report-raw-data-<timestamp>-<id>.csv` with service, operation, parameters, response code, exception, test
  `node_id`, `xfail`, `aws_validated`, `snapshot`, `snapshot_skipped_paths`.
- `scripts/metrics_coverage/diff_metrics_coverage.py`: compares the raw CSVs of the full suite against the acceptance
  suite and renders `report_metric_coverage.html` (coverage = service+operation+response-code exercised).
- `scripts/tinybird/upload_raw_test_metrics_and_coverage.py` ships both CSV kinds to Tinybird datasources
  `tests_raw__v0`, `tests_raw_builds__v0`, `implementation_coverage__v0` (token `TINYBIRD_PARITY_ANALYTICS_TOKEN`).
  The public coverage tables are generated downstream of that; the generator is not in this repo (UNKNOWN).
- Marker report: `testing/pytest/marker_report.py` (`--marker-report`, JSON of node id, path, markers, aggregated
  counts; optional Tinybird upload), `scripts/render_marker_report.py` (Jinja issue template, enriched with
  CODEOWNERS), `.github/workflows/marker-report.yml` (runs `pytest --co --marker-report`, files a GitHub issue).
- "Features files": `.github/workflows/pr-validate-features-files.yml` and `create_artifact_with_features_files.yml`
  call reusable workflows in `localstack/meta` over `localstack-core/localstack/services`; the file name and schema
  are not visible in the service directories listed (UNKNOWN).

## 5. Test strategy for API parity

`tests/conftest.py` registers `pytest_plugins`: `localstack.testing.pytest.fixtures`, `container`,
`localstack_snapshot.pytest.snapshot` (external package `localstack-snapshot>=0.1.1`), `filters`, `fixture_conflicts`,
`marking`, `marker_report`, `in_memory_localstack`, `validation_tracking`, `path_filter`, plus
stepfunctions/cloudformation fixtures.

**Targets.** `testing/aws/util.py` `is_aws_cloud()` is `os.environ.get("TEST_TARGET", "") == "AWS_CLOUD"`;
`base_aws_client_factory` returns an `ExternalAwsClientFactory` for AWS or an `ExternalClientFactory(endpoint=
TEST_AWS_ENDPOINT_URL)` for LocalStack. `testing/config.py`: account `000000000000`, keys `test`/`test`, region
`us-east-1`; secondary account `000000000002`, region `ap-southeast-1`. Tests must use the `account_id`,
`secondary_account_id`, `region_name`, `secondary_region_name` fixtures, never constants; a scheduled workflow runs
the suite with randomised ids and regions (`docs/testing/multi-account-region-testing/README.md`).

**Markers** (`testing/pytest/marking.py`): `aws_validated` ("test has been successfully run against AWS, ideally
multiple times"), `aws_manual_setup_required`, `aws_needs_fixing` ("basically a TODO"), `aws_only_localstack`,
`aws_unknown`; `enforce_single_aws_marker` requires exactly one on every test under `tests/aws/`;
`skip_snapshot_verify(paths=[...])`.

**Snapshots** (`docs/testing/parity-testing/README.md`): "Parity tests (also called snapshot tests) are a special form
of integration tests that should verify and improve the correctness of LocalStack compared to AWS." Record with
`TEST_TARGET=AWS_CLOUD` and `--snapshot-update` (or `SNAPSHOT_UPDATE=1`); the file is `<filename>.snapshot.json`
beside the test (e.g. `tests/aws/services/sqs/test_sqs.snapshot.json`); transformers (`KeyValueBasedTransformer`,
`JsonPathTransformer`, `RegexTransformer`, service presets like `snapshot.transform.lambda_api()`) normalise ids, and
the `snapshot` fixture in `tests/aws/conftest.py` pre-registers account-id, region and partition transformers.
`docs/testing/integration-tests/README.md`: after a test runs reliably against AWS "you can add the pytest marker
`@markers.aws.validated`", then record.

**Recording "validated against AWS"** (`testing/pytest/validation_tracking.py`): `<test file>.validation.json` maps
node id → `last_validated_date` and `durations_in_seconds` (`setup, call, teardown, total`); written at teardown only
when `is_aws_cloud()` and the test passed; `--validation-date-limit-days` / `--validation-date-limit-timestamp` select
stale tests for re-validation; `scripts/gather_outdated_snapshots.py` lists them as a pytest selection string.

**Fixtures** (`testing/pytest/fixtures.py`): `aws_client`, `aws_client_factory`, `secondary_aws_client_factory`,
resource factories (`s3_bucket`, `sqs_queue`, `sns_topic`, `create_lambda_function`) that yield and delete, and a
`cleanups` fixture that runs registered callables in reverse order, warning on `ClientError`. Test layout:
`tests/unit`, `tests/aws/services/<svc>/` (36 service dirs), `tests/aws/test_multi_accounts.py`,
`test_multiregion.py`, `test_moto.py`; `.test_durations` at the root feeds `pytest-split`.

## 6. Licence and reuse

- `LICENSE.txt` at `v4.14.0` (15 lines) and on `main` (16 lines) is the same Apache-2.0 header: "Copyright (c) 2017+
  LocalStack contributors / Copyright (c) 2016 Atlassian Pty Ltd / Licensed under the Apache License, Version 2.0
  ...". It is the short header, not the nine-section text. `pyproject.toml` declares `license = "Apache-2.0"`. No
  `NOTICE` file exists at the root.
- `README.md` at `v4.14.0`: "This version of LocalStack is released under the Apache License, Version 2.0 (see
  LICENSE). By downloading and using this software you agree to the End-User License Agreement (EULA)." The same two
  sentences are on `main` and in `DOCKER.md`. `docs/end_user_license_agreement/README.md` (7148 bytes, identical size
  at both refs) says "You may use, extend, and redistribute this software under the terms of the Apache License 2.0"
  and "To the extent there is conflict between the license terms covering the Open-Source Components and this EULA,
  the terms of such licenses will apply in lieu of the terms of this EULA." It also states the Software "may
  automatically communicate with servers ... to send anonymized usage information" (see section 7).
- `main` README notice: "this repository is now archived and read-only"; users are pointed to "LocalStack for AWS ...
  a free Hobby plan for non-commercial use". `DOCKER.md` on `main`: "We're moving toward a unified LocalStack for AWS
  image, updating how access works."

**Verdict (engineering reading, not legal advice).** Yes: source in this repository at `v4.14.0` (and on `main`, whose
LICENSE.txt is unchanged) is Apache-2.0 and can be reused, modified and redistributed with attribution. Obligations
under Apache-2.0 section 4: keep the copyright lines above in any copied file, ship a copy of the full Apache-2.0 text
(the repo only links it), mark modified files as changed, and carry forward NOTICE content (none exists here, so only
the copyright lines). The EULA governs "downloading and using this software" (the built distribution) and by its own
words yields to the OSS licence on conflict; it does not restrict source reuse. What is NOT Apache-2.0: the unified
"LocalStack for AWS" image and anything imported as `localstack.pro.core` (the pro persistence,
`StoreSerializationCheckerPlugin`, etc.) — never copy from those. Also note the fork `moto-ext`; Friday should sit on
upstream `moto`, whose own licence is not verified in this study (UNKNOWN here). The reason GitHub labels the repo
`NOASSERTION` is UNKNOWN.

## 7. Telemetry and analytics — modules Friday must never import

Endpoints: `constants.ANALYTICS_API = "https://analytics.localstack.cloud/v1"`, `constants.API_ENDPOINT =
"https://api.localstack.cloud/v1"`. Flags: `DISABLE_EVENTS = is_env_true("DISABLE_EVENTS")` ("whether to disable
publishing events to the API"), `DEBUG_ANALYTICS`; `is_local_test_mode()` (env `LOCALSTACK_INTERNAL_TEST_RUN`) also
suppresses tracking; `DISABLE_EVENTS` is in `CONFIG_ENV_VARS` so the CLI forwards it into the container. Tracking is
ON by default and is only turned off by the flag, by test mode, or when the backend's `/session` reply lacks
`"track_events": True` (`utils/analytics/publisher.py` `is_tracking_disabled`, `_do_start_retry`).

Runtime modules (all under `localstack-core/localstack/`):

| Module | What it does |
|---|---|
| `utils/analytics/__init__.py` | builds the global `log = EventLogger(GlobalAnalyticsBus(), session_id)` at import |
| `utils/analytics/client.py` | `AnalyticsClient`: POSTs to `<ANALYTICS_API>/session` and `/events`, header `Localstack-Session-ID` |
| `utils/analytics/publisher.py` | `GlobalAnalyticsBus`, `AsyncBatcher` (20 events / 10 s), background thread, `atexit` flush |
| `utils/analytics/metadata.py` | `ClientMetadata`: session id, machine id (`dirs.cache/machine.json`, prefixes `dkr_`/`sys_`/`gen_`), `api_key` from `LOCALSTACK_AUTH_TOKEN`/`LOCALSTACK_API_KEY`, `is_ci`, `is_docker`, version, edition |
| `utils/analytics/events.py`, `logger.py` | `Event`, `EventMetadata`, `EventHandler`, `EventLogger` |
| `utils/analytics/cli.py` | `publish_invocation`: command name and truthy parameter names per CLI call, 0.5 s subprocess |
| `utils/analytics/service_request_aggregator.py` | every 60 s emits `"aws_request_agg"` with per (service, operation, status, error) counts |
| `utils/analytics/metrics/{api,counter,registry,publisher}.py` | `Counter`/`LabeledCounter` (≤6 labels), singleton `MetricRegistry`, `publish_metrics` emits `"ls_metrics"` at `on_infra_shutdown` |
| `aws/handlers/analytics.py` | `ServiceRequestCounter` in the response chain, feeds the aggregator |
| `runtime/analytics.py` | at `on_infra_start` emits `"config"` (values of ~70 `TRACKED_ENV_VAR`, presence of `PRESENCE_ENV_VAR`, `PROVIDER_OVERRIDE_*`) and `"container_info"` |
| `services/lambda_/analytics.py`, `sns/analytics.py`, `events/analytics.py`, `stepfunctions/analytics.py`, `apigateway/analytics.py`, `apigateway/next_gen/execute_api/handlers/analytics.py` | per-service `LabeledCounter`s (function invocations by runtime/status, rule invocations, ASL features, integration types) |
| `services/cloudformation/analytics.py` | counters plus `emit_stack_failure` → `log.event("cfn_stack_deploy_failures", {reason, tb})` — sends a traceback |
| `services/internal.py` `/_localstack/info`, `/_localstack/usage` | local endpoints that expose `get_client_metadata()` (session and machine id) and usage; not outbound |

Hooks wire them in via `plux.ini`: `_publish_config_as_analytics_event`, `_publish_container_info` (`on_infra_start`),
`publish_metrics` (`on_infra_shutdown`). Developer-side reporting to Tinybird: `testing/pytest/marker_report.py`
(`--marker-report-tinybird-upload`, `MARKER_REPORT_TINYBIRD_TOKEN`), `scripts/tinybird/*`, `pytest-tinybird` in the
test extras. Update checks: none found in `utils/bootstrap.py` or the CLI (`localstack update` shells out to pip /
Docker pull); the auth token only selects the Pro image (`is_auth_token_configured()`). The CLI (`cli/localstack.py`,
`main.py`, `console.py`, `profiles.py`, present through `v4.13.1`) is removed at `v4.14.0`, where `cli/` holds only
`__init__.py`, `exceptions.py`, `lpm.py`; the CLI's 14 `@publish_invocation` commands therefore live outside this repo
from that tag on (location UNKNOWN). The recursive git-tree listing was truncated, so the per-service list above
is from directory listings of sqs, sns, s3, dynamodb, events, lambda_, cloudformation, stepfunctions, secretsmanager
and apigateway only; other service packages are UNKNOWN.

## 8. What Friday should adopt

1. **One gateway port with a handler chain** (request / response / exception / finalizer lists, `chain.stop()` vs
   `terminate()` semantics) — every cross-cutting concern (auth stub, CORS, logging, error shaping) becomes one small
   handler.
2. **Service detection from the `Authorization` credential scope first**, then `X-Amz-Target`, then path/host rules —
   it is what real SDKs send, and it is why one port suffices.
3. **A typed `RequestContext`** carrying service, operation, account, region, request id and the parsed/serialised
   payloads — handlers stay stateless.
4. **Account and region derived from the fake access key** (12-digit key = account, default `000000000000`, unknown
   region → `us-east-1`) and passed to moto through `x-moto-account-id` — multi-tenant tests for free, no IAM.
5. **`AccountRegionBundle` stores with Local/CrossRegion/CrossAccount attributes** for Friday's own shims (OIDC stub,
   key-value, queues) — mirrors moto's `BackendDict`, so one state visitor covers both.
6. **Moto in-process with a fallthrough dispatcher**: own handler first, moto on `NotImplementedError`, moto
   `RESTError` mapped to a common exception — the cheapest way to a large API surface with targeted fixes.
7. **A uniform error contract**: `code`, `status_code`, `sender_fault`, `message`, serialised per protocol, 501 for
   not-implemented, 500 JSON with a `type` field as the last resort — never a bare stack trace.
8. **Init stages `boot.d/start.d/ready.d/shutdown.d` with a status endpoint** — the salon can seed buckets, tables and
   secrets for an app from plain scripts.
9. **A `/_backstage/health`-style internal prefix** served by the same port (health, init status, plugin list;
   config/diagnose only in debug).
10. **Parity discipline**: one AWS-compat marker per test enforced at collection, `.snapshot.json` recordings with
    transformers, and a `.validation.json` last-validated date — even if Friday records against moto, the "what did
    we compare to and when" metadata is the honest part.
11. **A generated implementation-coverage CSV** produced by firing every botocore operation at the gateway and
    classifying 501s — a one-script parity table.
12. **An `awslocal`-style thin wrapper** (`AWS_ENDPOINT_URL`, dummy `test` keys, default region) rather than a patched
    SDK.

**Do not adopt:** the analytics bus and every module in section 7 (Friday ships no telemetry, so no "opt-out" flag
either); the `/_localstack/info` exposure of machine ids; a session handshake with any remote before serving; the
machine-id file; hard-coded `app.localstack.cloud` CORS origins; the `moto-ext` fork (use upstream moto); the full ASF
parser/serializer rewrite unless moto's own protocol handling proves insufficient; plux entry points for a
single-binary desktop app (a static registry is enough); Tinybird/CI upload scripts; the EULA click-through.

**Sizing (agent-days, "one gateway + moto + our shims").** Assumptions: Python, upstream moto 5.x for
S3/SQS/SNS/DynamoDB/Secrets Manager with moto handling AWS protocol parsing and serialisation; Lambda-style functions
run as local subprocesses, not Docker; Mailpit and Azurite are launched as external processes; SQLite is a file, not
an emulated API; no CloudFormation, no IAM enforcement; persistence by dill-pickling moto backends and Friday stores
on shutdown; one agent at a time, tests written alongside. Estimate: gateway + service detection + account/region +
error shaping 4–6; moto hosting and fallthrough 2–3; OIDC stub 2–3; key-value and queue shims 2–3; process supervision
for Mailpit/Azurite 2; init stages and internal endpoints 2; state snapshot 3–4 (pickling moto is the risk); awslocal-
style client and env injection 1; parity harness (markers, snapshots, coverage CSV) 3–5. Total 21–31 agent-days for a
usable backstage; 30–45 if the ASF-style typed parser/serializer is rebuilt from botocore specs instead of trusting
moto's protocol layer. The unverified assumption that most moves the number is whether moto's server dispatch already
handles single-port routing well enough (UNKNOWN here; verify against moto's own code before committing to a gateway
design).

## Sources

GitHub API (`https://api.github.com/repos/localstack/localstack/...`): the repository record; `tags?per_page=15`;
`releases/latest`; `git/trees/v4.14.0?recursive=1` (truncated); `contents/` listings at `?ref=v4.14.0` for the root,
`docs`, `docs/end_user_license_agreement`, `docs/testing`, `docs/localstack-concepts`, `bin`, `scripts`,
`scripts/metrics_coverage`, `scripts/tinybird`, `.github/workflows`, `tests`, `tests/aws`, `tests/aws/services`,
`tests/aws/services/sqs`, `localstack-core/localstack`, `.../aws`, `.../aws/api`, `.../aws/handlers`, `.../aws/protocol`,
`.../aws/serving`, `.../runtime`, `.../services`, `.../services/{sqs,secretsmanager,lambda_,cloudformation,stepfunctions,
sns,events,dynamodb,s3}`, `.../state`, `.../testing`, `.../testing/pytest`, `.../testing/snapshots`,
`.../utils/analytics`, `.../utils/analytics/metrics`; `contents/localstack-core/localstack/cli` at refs v4.14.0, v4.13.1,
v4.13.0, v4.12.0, v4.9.0, v4.0.0; `contents/docs` and `contents/docs/end_user_license_agreement` at `?ref=main`.

Raw files at `https://raw.githubusercontent.com/localstack/localstack/v4.14.0/...`: `LICENSE.txt`, `README.md`,
`DOCKER.md`, `pyproject.toml`, `plux.ini`, `docs/end_user_license_agreement/README.md`,
`docs/localstack-concepts/README.md`, `docs/testing/README.md`, `docs/testing/{test-types,integration-tests,
parity-testing,multi-account-region-testing}/README.md`, `localstack-core/localstack/{config,constants,plugins}.py`,
`localstack-core/localstack/aws/{gateway,chain,app,accounts,skeleton,scaffold,spec,forwarder,connect}.py`,
`.../aws/api/core.py`, `.../aws/handlers/{__init__,service,auth,region,fallback,analytics,internal,legacy,codec,
service_plugin,cors,internal_requests,validation,exceptions,presigned_url,metric_handler}.py`,
`.../aws/protocol/{service_router,op_router,parser,serializer}.py`, `.../aws/serving/edge.py`,
`.../services/{plugins,providers,moto,stores,edge,internal}.py`, `.../services/sqs/{models,provider}.py`,
`.../services/secretsmanager/provider.py`, `.../services/{lambda_,sns,cloudformation,stepfunctions,events,apigateway}/
analytics.py`, `.../services/apigateway/next_gen/execute_api/handlers/analytics.py`,
`.../state/{core,snapshot,pickle,inspect,codecs}.py`, `.../runtime/{init,hooks,analytics}.py`, `.../utils/bootstrap.py`,
`.../utils/analytics/{__init__,client,publisher,metadata,events,logger,cli,service_request_aggregator}.py`,
`.../utils/analytics/metrics/{api,counter,registry,publisher}.py`, `.../cli/{__init__,lpm}.py`, `.../testing/config.py`,
`.../testing/aws/util.py`, `.../testing/pytest/{marking,fixtures,validation_tracking,metric_collection,marker_report}.py`,
`.../testing/pytest/snapshot.py` (404; the plugin is the external `localstack_snapshot` package), `tests/conftest.py`,
`tests/aws/conftest.py`, `scripts/{capture_notimplemented_responses,render_marker_report,gather_outdated_snapshots}.py`,
`scripts/metrics_coverage/diff_metrics_coverage.py`, `scripts/tinybird/upload_raw_test_metrics_and_coverage.py`,
`.github/workflows/{marker-report,pr-validate-features-files,create_artifact_with_features_files}.yml`.

Other refs and repositories: `raw.githubusercontent.com/localstack/localstack/main/{LICENSE.txt,README.md,DOCKER.md}`,
`raw.githubusercontent.com/localstack/localstack/master/README.md`,
`raw.githubusercontent.com/localstack/localstack/v4.13.1/localstack-core/localstack/cli/localstack.py`,
`raw.githubusercontent.com/localstack/localstack/v4.0.0/localstack-core/localstack/cli/localstack.py`,
`raw.githubusercontent.com/localstack/awscli-local/master/README.md`,
`raw.githubusercontent.com/localstack/awscli-local/master/bin/awslocal`.
