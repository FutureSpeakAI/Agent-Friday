# Signing releases and public lists

Status: the code is in place; no certificate has been bought and no signing
key has been made yet. Until they exist, everything below runs and says so:
the installer ships unsigned with its SHA-256, the update check reports a
release's manifest as unsigned, and the public lists stay unfetched.

There are two separate kinds of signature, for two separate threats.

## 1. Authenticode on the installer (Windows' own check)

Windows SmartScreen and antivirus look for an Authenticode signature on the
setup program. Friday uses Microsoft Artifact Signing (formerly Trusted
Signing): no hardware token, and CI can call it.

To turn it on, after an Artifact Signing account and a certificate profile
exist, add these repository secrets:

| Secret | What |
|---|---|
| `ARTIFACT_SIGNING_ENDPOINT` | the account's regional endpoint, e.g. `https://eus.codesigning.azure.net/` |
| `ARTIFACT_SIGNING_ACCOUNT` | the account name |
| `ARTIFACT_SIGNING_PROFILE` | the certificate profile name |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | an app registration with the "Artifact Signing Certificate Profile Signer" role |

The installer workflow's build job then runs
`packaging/windows/sign-installer.ps1`, which signs with `signtool` and the
Artifact Signing client library, checks the signature verifies, and rewrites
the `.sha256` beside the installer (signing changes the file). With none of
the secrets set it prints that signing is not configured and changes nothing;
with only some set it fails, so a release is never half-configured.

SmartScreen reputation builds over several signed releases; the first ones may
still show a warning.

## 2. The signed release manifest (Friday's own check)

Authenticode says who built a file; it says nothing about which version is
current, so it cannot stop a downgrade or a stale mirror. Each release
therefore carries `release-manifest.json`: version, build sequence, release
date, an expiry a year out, and the SHA-256 of each file. The workflow's
release job writes it (`scripts/release_manifest.py build`) and publishes it
with the release. It is signed on an offline machine, never in CI:

```
minisign -G -p release.pub -s release.key          # once, on the offline machine
minisign -Sm release-manifest.json -t "name=agent-friday-release version=<version>" -s release.key
gh release upload v<version> release-manifest.json.minisig
python scripts/release_manifest.py verify release-manifest.json --key release.pub
```

`release.pub` (the public half) goes into `RELEASE_KEYS` in
`src/agent_friday/services/trust_keys.py`. From then on the weekly update
check (`services/update_check.verify_release`) reads the newest release's
manifest and signature and recommends the release only when the signature
verifies, the manifest names the same version, has not expired and is newer
than the installed build. A manifest that is present and fails any of these
is refused; a release with no manifest is still announced, with the advice to
compare the installer's SHA-256 with the release page.

## 3. The public lists

FutureSpeak maintains four public lists Friday fetches when a feature needs
them (`services/public_lists.LISTS`): platform export pages, cancellation
pages, retailer return windows and the states' data-broker registries.
Settlements are not among them yet. Each list is a JSON file with an `items`
array, published at `<base>/<name>.json` with `<name>.json.minisig` beside it,
signed with a separate key:

```
minisign -G -p lists.pub -s lists.key               # once, offline, a different key from the release key
minisign -Sm cancellation_pages.json -t "name=cancellation_pages version=7 expires=2027-01-31" -s lists.key
```

The trusted comment is part of what is signed: Friday refuses a list whose
name does not match, whose version is lower than the one it has, or whose
expiry has passed. `lists.pub` goes into `PUBLIC_LIST_KEYS`; the base URL is
`public_lists.DEFAULT_BASE_URL` (or the `public_lists.base_url` setting).
Fetching follows the person's "check for new versions" answer, goes through
the SSRF guard, and is written to the egress log.
