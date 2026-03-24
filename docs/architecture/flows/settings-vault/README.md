## Settings & Vault Flow
**Status:** Current | **Type:** System-internal (continuous) | **Complexity:** High
**Last analyzed:** 2026-03-24

### Overview
The Settings & Vault flow governs how all agent configuration is persisted and protected at rest. Every settings change follows a strict pipeline: renderer UI action -> IPC bridge -> main-process handler -> SettingsManager mutation (serialized write queue) -> vault encryption (AES-256-GCM) -> encrypted disk write. The entire key hierarchy derives from a user-chosen passphrase via Argon2id + BLAKE2b KDF, with no OS credential store, no machine binding, and no recovery backdoor. A two-phase boot ensures the app is usable before the vault is unlocked (Phase A: defaults/plaintext fallback) and that encrypted secrets are restored once the passphrase is entered (Phase B: decrypt + reload).

### Flow Boundaries
- **Start (initialization):** `SettingsManager.initialize()` reads `friday-settings.json` via `vaultRead()` on app launch (Phase A, vault locked -- falls back to defaults if file is encrypted)
- **Start (runtime):** Renderer calls any `window.eve.settings.*` or `window.eve.vault.*` IPC method
- **End:** Encrypted bytes written to `{userData}/friday-settings.json` on disk via `vaultWrite()`

### Quick Reference
| Component | File | Purpose |
|-----------|------|---------|
| SettingsManager | `src/main/settings.ts` | Singleton settings store; in-memory state, serialized write queue, sensitive field guard |
| FridaySettings | `src/main/settings.ts:97-189` | Full settings interface (~60 fields including API keys, agent config, privacy, evolution state) |
| vault.ts | `src/main/vault.ts` | AES-256-GCM encrypt/decrypt, vault lifecycle (init/unlock/destroy/reset), key state |
| passphrase-kdf.ts | `src/main/crypto/passphrase-kdf.ts` | Argon2id master key derivation, BLAKE2b sub-key KDF, canary, passphrase validation |
| SecureBuffer | `src/main/crypto/secure-buffer.ts` | Secure memory wrapper with logical NOACCESS/READONLY/READWRITE states, guaranteed zeroing |
| core-handlers.ts | `src/main/ipc/core-handlers.ts` | IPC handlers for settings get/set, API keys, validation, health checks, MCP, shell |
| index.ts (vault IPC) | `src/main/index.ts:876-929` | Vault IPC handlers: initialize-new, unlock, reset-all; triggers Phase B boot |
| index.ts (Phase B) | `src/main/index.ts:961-1026` | `completeBootAfterUnlock()`: re-reads settings, injects hmacKey, starts integrity + agent network |
| preload.ts (settings) | `src/main/preload.ts:170-191` | `window.eve.settings` namespace (IPC bridge) |
| preload.ts (vault) | `src/main/preload.ts:1204-1215` | `window.eve.vault` namespace (IPC bridge) |
| types.d.ts (settings) | `src/renderer/types.d.ts:647-707` | TypeScript declarations for `window.eve.settings` |
| types.d.ts (vault) | `src/renderer/types.d.ts:1790-1797` | TypeScript declarations for `window.eve.vault` |

### Key Hierarchy

```
User Passphrase (>=8 words, never stored)
  + salt (16 bytes, random, stored in .vault-salt)
  |
  v Argon2id (opslimit=4, memlimit=256MB)
  |
  masterKey (32 bytes -- destroyed after sub-key derivation in ~ms)
  |
  +-- BLAKE2b KDF(id=1, ctx="AF_VAULT") -> vaultKey    (AES-256-GCM for all vault files)
  +-- BLAKE2b KDF(id=2, ctx="AF_HMAC_") -> hmacKey     (HMAC-SHA256 integrity signing)
  +-- BLAKE2b KDF(id=3, ctx="AF_IDENT") -> identityKey (wraps Ed25519/X25519 private keys)
```

All three sub-keys are wrapped in `SecureBuffer` instances with logical protection states and secure zeroing on destroy.

### Two-Phase Boot

**Phase A (vault locked):**
1. `SettingsManager.initialize()` calls `vaultRead(friday-settings.json)` (`settings.ts:294-296`)
2. `vaultRead()` detects vault is locked, returns raw file content as plaintext (`vault.ts:337-341`)
3. If the file is already encrypted (returning user), the read throws or returns garbled data; SettingsManager catches the error and uses `DEFAULTS` (`settings.ts:306-308`)
4. `.env` fallback merge fills in API keys from environment variables if settings are empty (`settings.ts:310-334`)
5. Phase A complete -- UI shell is usable, but API keys/secrets from encrypted settings are missing

**Phase B (vault unlocked):**
1. Renderer sends `vault:initialize-new` (first run) or `vault:unlock` (returning user) (`index.ts:890-917`)
2. Vault derives keys from passphrase via `deriveAllKeys()` and verifies canary (`vault.ts:82-164`)
3. `completeBootAfterUnlock()` fires (`index.ts:961-1026`):
   - `settingsManager.reloadFromVault()` re-reads `friday-settings.json` now that `vaultRead()` can decrypt (`settings.ts:351-365`)
   - Memory, trust graph, and calendar are also reloaded
   - HMAC signing key injected into integrity engine
   - Agent network initialized (private keys now decryptable via identityKey)
4. Renderer notified via `vault:boot-complete` event (`index.ts:1025`)

### Steps

**1. Settings Read (Masked)** (`core-handlers.ts:42`, `settings.ts:372-416`)
The renderer calls `window.eve.settings.get()` which invokes `settings:get`. The handler calls `settingsManager.getMasked()`, which returns a sanitized object: API keys are replaced with boolean `hasXxxKey` flags and `xxxKeyHint` strings (first 4 + last 4 chars with masked middle). Raw API key values never cross the IPC bridge to the renderer.

**2. Generic Setting Write** (`core-handlers.ts:45-53`, `settings.ts:485-552`)
The renderer calls `window.eve.settings.set(key, value)` which invokes `settings:set`.

Pre-write validation in `core-handlers.ts`:
- `key` validated as string, max 256 chars (`core-handlers.ts:46`)
- `value` serialized size capped at 100KB to prevent memory exhaustion (`core-handlers.ts:48-50`)

Pre-write validation in `setSetting()`:
- Key must exist in current settings object (`settings.ts:486`)
- Type validation: value type must match current value's type (with exceptions for arrays and objects) (`settings.ts:488-513`)
- Nullable key allowlist: only `intakeResponses`, `psychologicalProfile`, `featureSetupState`, `personalityEvolution`, `trustGraphConfig`, `onboardingCheckpoint` may be set to null (`settings.ts:493-501`)
- **Sensitive field denylist (13 fields blocked):** All 8 API keys, both bot tokens, both owner IDs, `googleCalendarTokens`, and `gatewayEnabled` are blocked from the generic `setSetting()` path. These must use dedicated setters (`settings.ts:519-534`)

Write path (serialized):
1. Mutation and disk write happen inside the `savePromise` chain to prevent concurrent interleaving (`settings.ts:540-541`)
2. In-memory field assignment: `this.settings[key] = value` (`settings.ts:541`)
3. `vaultWrite(filePath, JSON.stringify(settings))` encrypts and writes to disk (`settings.ts:542-543`)

**3. API Key Write** (`core-handlers.ts:89-107`, `settings.ts:448-483`)
The renderer calls `window.eve.settings.setApiKey(key, value)` which invokes `settings:set-api-key`.

Pre-write validation in `core-handlers.ts`:
- `key` validated as string, max 50 chars (`core-handlers.ts:96`)
- `value` validated as string, max 500 chars (`core-handlers.ts:97`)
- `key` must be one of the 8 valid key types: gemini, anthropic, elevenlabs, firecrawl, perplexity, openai, openrouter, huggingface (`core-handlers.ts:98-101`)

Write path (serialized):
1. Mutation, `applyApiKeys()` (now a no-op since Fix H5), and `vaultWrite()` all happen inside the `savePromise` chain (`settings.ts:453-474`)
2. Errors are caught to keep the promise chain alive, then re-thrown to the caller (`settings.ts:475-482`)

**4. API Key Validation** (`core-handlers.ts:110-174`)
The renderer calls `window.eve.settings.validateApiKey(keyType, value)` which invokes `settings:validate-api-key`. Validation runs server-side in the main process to avoid renderer CORS blocks.

Supported validators:
- **Gemini:** Prefix check (`AIza`), then GET to `generativelanguage.googleapis.com/v1beta/models?key=...` with 8s timeout
- **Anthropic:** Prefix check (`sk-ant-`), then POST to `api.anthropic.com/v1/messages` with a minimal `claude-haiku-4-5` request, 8s timeout
- **OpenRouter:** GET to `openrouter.ai/api/v1/auth/key` with Bearer token, 8s timeout
- **Others:** No validator -- accepted by default

Returns `{ valid: boolean, error?: string }`.

**5. API Health Check** (`core-handlers.ts:179-236`)
The renderer calls `window.eve.settings.checkApiHealth()` which invokes `settings:check-api-health`. Performs parallel endpoint pings for Gemini, Anthropic, OpenRouter, and ElevenLabs (6s timeout each). Returns `Record<string, 'connected' | 'offline' | 'no-key'>`.

**6. Dedicated Setters** (`core-handlers.ts:55-267`, `settings.ts:418-696`)
Several settings bypass the generic `setSetting()` path and use dedicated handlers:

| IPC Channel | Handler | Setter | Notes |
|-------------|---------|--------|-------|
| `settings:set-auto-launch` | `core-handlers.ts:55-58` | `setAutoLaunch()` | Also calls `app.setLoginItemSettings()` |
| `settings:set-auto-screen-capture` | `core-handlers.ts:60-63` | `setAutoScreenCapture()` | |
| `settings:set-obsidian-vault-path` | `core-handlers.ts:66-87` | `setObsidianVaultPath()` | Also triggers Obsidian vault structure + memory sync |
| `settings:set-api-key` | `core-handlers.ts:89-107` | `setApiKey()` | Serialized write queue |
| `settings:set-telegram-config` | `core-handlers.ts:263-267` | `setTelegramConfig()` | Sensitive -- bypasses generic path |
| `settings:set-voice-engine` | `core-handlers.ts:240-245` | `setVoiceEngine()` | |
| `settings:set-personaplex-hf-token` | `core-handlers.ts:247-250` | `setPersonaplexHfToken()` | |
| `settings:set-personaplex-voice-id` | `core-handlers.ts:252-255` | `setPersonaplexVoiceId()` | |
| `settings:set-personaplex-cpu-offload` | `core-handlers.ts:257-260` | `setPersonaplexCpuOffload()` | |
| `settings:reset-to-defaults` | `core-handlers.ts:270-273` | `resetToDefaults()` | Nuclear reset -- wipes all settings |

All dedicated setters call `this.save()` which uses the same serialized `savePromise` chain and `vaultWrite()`.

**7. Agent Config Save** (`settings.ts:555-590`)
Called at the end of onboarding by `onboarding-handlers.ts`. `saveAgentConfig(config)` writes all 9 AgentConfig fields atomically inside the `savePromise` chain, then signs the identity via `integrityManager.signIdentity()`. This is the only path that sets `onboardingComplete = true`.

**8. Vault Initialization (First Run)** (`vault.ts:82-117`, `passphrase-kdf.ts:192-212`)
Triggered by renderer via `window.eve.vault.initializeNew(passphrase)` -> `vault:initialize-new` IPC.

Steps:
1. `ensureSodiumReady()` -- loads libsodium WASM (`passphrase-kdf.ts:74-79`)
2. Guard: rejects if vault already initialized (`vault.ts:92-94`)
3. `validatePassphrase()` -- >=8 words, avg word length >=3, >=4 unique words, >=24 total chars (`passphrase-kdf.ts:319-348`)
4. `generateSalt()` -- 16 random bytes (`passphrase-kdf.ts:100-104`)
5. `writeSalt()` -- writes to `{userData}/.vault-salt` (`passphrase-kdf.ts:121-123`)
6. `deriveAllKeys(passphrase, salt)`:
   a. `deriveMasterKey()` -- Argon2id with 4 iterations, 256MB memory (`passphrase-kdf.ts:136-152`)
   b. `deriveSubkey(masterKey, 1, "AF_VAULT")` -> vaultKey in SecureBuffer (`passphrase-kdf.ts:201`)
   c. `deriveSubkey(masterKey, 2, "AF_HMAC_")` -> hmacKey in SecureBuffer (`passphrase-kdf.ts:202`)
   d. `deriveSubkey(masterKey, 3, "AF_IDENT")` -> identityKey in SecureBuffer (`passphrase-kdf.ts:203`)
   e. Master key securely destroyed: `crypto.randomFillSync(masterKey); masterKey.fill(0)` (`passphrase-kdf.ts:206-207`)
7. `setKeys(keys)` -- stores keys in module state, sets `vaultUnlocked = true` (`vault.ts:511-521`)
8. `createCanary(vaultKey, userDataDir)` -- encrypts known plaintext with XSalsa20-Poly1305, writes to `.vault-canary` (`passphrase-kdf.ts:223-235`)
9. `writeVaultMeta(userDataDir)` -- writes `{version: 2, initialized: true, createdAt: ...}` to `.vault-meta.json` (`passphrase-kdf.ts:294-304`)

**9. Vault Unlock (Returning User)** (`vault.ts:129-164`, `passphrase-kdf.ts:246-273`)
Triggered by renderer via `window.eve.vault.unlock(passphrase)` -> `vault:unlock` IPC.

Steps:
1. `ensureSodiumReady()`
2. `readSalt(userDataDir)` -- reads `.vault-salt` from disk (`passphrase-kdf.ts:110-116`)
3. `deriveAllKeys(passphrase, salt)` -- same Argon2id + BLAKE2b derivation as init
4. `verifyCanary(keys.vaultKey, userDataDir)`:
   a. Read `.vault-canary` from disk
   b. Decrypt with XSalsa20-Poly1305 using derived vaultKey
   c. `crypto.timingSafeEqual()` comparison against known plaintext (`passphrase-kdf.ts:269`)
   d. If mismatch: all three derived keys are destroyed, returns `false` (`vault.ts:152-157`)
5. If match: `setKeys(keys)`, vault is now unlocked

**10. Vault Write (Encryption)** (`vault.ts:305-316`, `vault.ts:252-265`)
Called by `SettingsManager.save()`, `setSetting()`, `setApiKey()`, and `saveAgentConfig()`.

`vaultWrite(filePath, content)`:
1. Throws if vault is locked -- no plaintext fallback for writes (Fix M6) (`vault.ts:306-311`)
2. Converts content to Buffer
3. `vaultEncrypt(plaintext)`:
   a. Generates 12-byte random IV (`vault.ts:257`)
   b. Borrows vaultKey via `withAccess('readonly', ...)` (`vault.ts:259`)
   c. Creates AES-256-GCM cipher, encrypts content (`vault.ts:260-261`)
   d. Gets 16-byte auth tag (`vault.ts:262`)
   e. Returns `[12-byte IV][16-byte authTag][ciphertext]` (`vault.ts:263`)
4. Writes encrypted bytes to disk via `fs.writeFile()` (`vault.ts:315`)

**11. Vault Read (Decryption)** (`vault.ts:334-361`, `vault.ts:271-291`)
Called by `SettingsManager.initialize()`, `reloadFromVault()`, and any other vault-aware reader.

`vaultRead(filePath)`:
1. Reads raw bytes from disk (`vault.ts:335`)
2. If vault is locked: returns raw content as UTF-8 string with warning log (`vault.ts:337-341`)
3. If vault is unlocked, tries `vaultDecrypt(raw)`:
   a. Extracts IV (bytes 0-11), authTag (bytes 12-27), ciphertext (bytes 28+) (`vault.ts:278-280`)
   b. Borrows vaultKey via `withAccess('readonly', ...)` (`vault.ts:283`)
   c. Creates AES-256-GCM decipher, sets auth tag, decrypts (`vault.ts:284-286`)
   d. Returns plaintext Buffer on success, `null` on failure
4. If decryption succeeds: returns decrypted string (`vault.ts:345-347`)
5. If decryption fails: applies `isLikelyPlaintext()` heuristic (`vault.ts:353`):
   - If file looks encrypted (binary header) but failed to decrypt: throws corruption error (Fix L7) (`vault.ts:354-355`)
   - If file is legacy plaintext (printable ASCII): returns as-is, will be encrypted on next write (`vault.ts:358-360`)

**12. Serialized Write Queue** (`settings.ts:264`, `settings.ts:540-548`, `settings.ts:763-776`)
All write paths chain onto a single `savePromise: Promise<void>`. This ensures:
- Two concurrent `setSetting()` calls cannot interleave field assignments before the disk write
- Each write completes (or fails) before the next begins
- Errors are caught internally to keep the chain alive, then re-thrown to the caller
- Applied consistently in `setSetting()`, `setApiKey()`, `saveAgentConfig()`, and `save()`

**13. Vault Destruction & Reset** (`vault.ts:200-229`)
Two destruction paths:

`destroyVault()` (shutdown cleanup, `vault.ts:200-206`):
- Calls `.destroy()` on each SecureBuffer (randomFill + zero fill)
- Sets all key references to null, `vaultUnlocked = false`

`resetVaultFiles()` (nuclear "start fresh", `vault.ts:214-229`):
- Calls `destroyVault()` first
- Deletes `.vault-salt`, `.vault-canary`, `.vault-meta.json` from userData
- Next launch treats as fresh install; all previously encrypted data is unrecoverable

Triggered via `vault:reset-all` IPC (`index.ts:921-926`), which also calls `app.relaunch(); app.exit(0)`.

**14. SecureBuffer Lifecycle** (`secure-buffer.ts`)
All derived keys (vaultKey, hmacKey, identityKey) are wrapped in SecureBuffer:

- `SecureBuffer.from(source)`: copies source, wipes source via `secureZero()`, returns in READONLY state (`secure-buffer.ts:59-68`)
- `withAccess(mode, fn)`: temporarily sets protection to mode, runs callback with raw Buffer, re-locks to NOACCESS in `finally` block (`secure-buffer.ts:126-134`)
- `withAccessAsync(mode, fn)`: async variant for promise-returning callbacks (`secure-buffer.ts:140-148`)
- `destroy()`: `crypto.randomFillSync(buf)` then `buf.fill(0)` -- two-step to defeat compiler optimizations (`secure-buffer.ts:154-159`, `secure-buffer.ts:172-175`)
- `.inner` getter: throws if destroyed or NOACCESS (`secure-buffer.ts:74-82`)

**15. Settings Factory Reset** (`settings.ts:742-746`, `core-handlers.ts:270-273`)
`resetToDefaults()` replaces all in-memory settings with `DEFAULTS` and calls `save()`. Wipes psychological profile, agent config, API keys -- everything. Does NOT affect vault passphrase (that requires `vault:reset-all`).

### IPC Channels

**Settings namespace (`window.eve.settings`):**

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `settings:get` | R -> M | Get masked settings (API keys replaced with boolean flags + hints) |
| `settings:set` | R -> M | Generic setting write (sensitive fields blocked) |
| `settings:set-api-key` | R -> M | Dedicated API key setter (8 key types) |
| `settings:validate-api-key` | R -> M | Server-side HTTP validation for API keys |
| `settings:check-api-health` | R -> M | Parallel endpoint pings for 4 services |
| `settings:set-auto-launch` | R -> M | Toggle auto-launch + Electron login item |
| `settings:set-auto-screen-capture` | R -> M | Toggle auto screen capture |
| `settings:set-obsidian-vault-path` | R -> M | Set Obsidian path + trigger sync |
| `settings:set-telegram-config` | R -> M | Set Telegram bot token + owner ID |
| `settings:get-voice-engine` | R -> M | Get current voice engine preference |
| `settings:set-voice-engine` | R -> M | Set voice engine preference |
| `settings:get-personaplex-hf-token` | R -> M | Get PersonaPlex HuggingFace token |
| `settings:set-personaplex-hf-token` | R -> M | Set PersonaPlex HuggingFace token |
| `settings:get-personaplex-voice-id` | R -> M | Get PersonaPlex voice preset ID |
| `settings:set-personaplex-voice-id` | R -> M | Set PersonaPlex voice preset ID |
| `settings:get-personaplex-cpu-offload` | R -> M | Get CPU offload flag |
| `settings:set-personaplex-cpu-offload` | R -> M | Set CPU offload flag |
| `settings:reset-to-defaults` | R -> M | Nuclear settings reset (vault unaffected) |

**Vault namespace (`window.eve.vault`):**

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `vault:is-initialized` | R -> M | Check if vault has been set up (meta + salt files exist) |
| `vault:is-unlocked` | R -> M | Check if vault is currently unlocked |
| `vault:initialize-new` | R -> M | First-time vault setup (passphrase -> keys -> canary -> meta) |
| `vault:unlock` | R -> M | Unlock existing vault with passphrase (canary verification) |
| `vault:reset-all` | R -> M | Nuclear wipe: delete all vault files, relaunch app |
| `vault:boot-complete` | M -> R | Notification that Phase B is complete (secrets available) |

### State Changes

| State | Location | Trigger |
|-------|----------|---------|
| `settings` (in-memory) | `SettingsManager.settings` | Every `setSetting()`, `setApiKey()`, `saveAgentConfig()`, `reloadFromVault()` |
| `vaultUnlocked` | `vault.ts:64` | `setKeys()` sets `true`; `destroyVault()` sets `false` |
| `vaultKey` / `hmacKey` / `identityKey` | `vault.ts:61-63` | `setKeys()` assigns derived SecureBuffers; `destroyVault()` destroys and nulls them |
| `savePromise` | `SettingsManager.savePromise` | Extended by every write operation; keeps serialization chain alive |
| `process.env` API keys | Removed (Fix H5) | `applyApiKeys()` is now a no-op; keys only accessible via `settingsManager.getXxxApiKey()` getters |
| `friday-settings.json` | `{userData}/` disk | Encrypted on every `save()`/`vaultWrite()` |
| `.vault-salt` | `{userData}/` disk | Written once during vault initialization |
| `.vault-canary` | `{userData}/` disk | Written once during vault initialization; read on every unlock |
| `.vault-meta.json` | `{userData}/` disk | Written once during vault initialization |

### Error Scenarios

**1. Vault locked on write**
If `vaultWrite()` is called while the vault is locked, it throws `[Vault] Cannot write -- vault is locked. Unlock first.` (Fix M6, `vault.ts:307-310`). No plaintext fallback -- callers must ensure the vault is unlocked before writing.

**2. Wrong passphrase on unlock**
`verifyCanary()` decrypts the canary file with the derived vaultKey. If decryption fails (XSalsa20-Poly1305 auth tag mismatch), or the plaintext doesn't match the known value via `timingSafeEqual`, all derived keys are securely destroyed (`vault.ts:152-156`). Returns `{ ok: false, error: 'Incorrect passphrase' }` to the renderer.

**3. Corrupted encrypted file**
`vaultRead()` detects files that appear encrypted (binary header, sufficient length) but fail AES-256-GCM decryption. Instead of silently returning garbled data, it throws: `[Vault] File appears corrupted -- decryption failed. Recovery may be needed` (Fix L7, `vault.ts:354-355`).

**4. Sensitive field via generic path**
If the renderer attempts `settings.set('geminiApiKey', value)`, `setSetting()` silently rejects it with a console warning: `Sensitive field "geminiApiKey" cannot be set via setSetting()` (`settings.ts:531-534`). The 13 blocked fields must use their dedicated setters.

**5. Concurrent write interleaving**
All write paths (`setSetting`, `setApiKey`, `saveAgentConfig`, `save`) chain onto `savePromise`. If a save fails, the error is caught internally (keeping the chain alive for future saves) and re-thrown to the original caller (`settings.ts:544-551`, `settings.ts:767-774`).

**6. Argon2id DoS via oversized passphrase**
Passphrase length is capped at 1024 characters in the IPC handler before reaching Argon2id (`index.ts:888`). A 1GB string would cause Argon2id to allocate 256MB+ and potentially crash the process.

**7. Legacy plaintext file migration**
When `vaultRead()` encounters a file that fails decryption but passes the `isLikelyPlaintext()` heuristic (first byte is `{`, `[`, `<`, `"`, or >80% of first 32 bytes are printable ASCII), it returns the raw content and logs a warning (`vault.ts:358-360`). The file will be transparently encrypted on the next write.

**8. Save failure propagation**
If `vaultWrite()` throws during `save()`, the error is caught to keep `savePromise` alive, then re-thrown to the calling function (`settings.ts:767-774`). This ensures one failed save does not permanently break all subsequent saves.

**9. SecureBuffer use-after-destroy**
Accessing `.inner` or calling `withAccess()` on a destroyed SecureBuffer throws `SecureBuffer: use after destroy` (`secure-buffer.ts:76-77`, `secure-buffer.ts:162-164`). This prevents use of zeroed key material.

**10. First-run fallback to defaults**
On first launch, `friday-settings.json` does not exist. `vaultRead()` throws (file not found), `SettingsManager.initialize()` catches the error and uses `DEFAULTS` (`settings.ts:306-308`). Then `.env` values are merged in for any configured API keys (`settings.ts:310-334`).
