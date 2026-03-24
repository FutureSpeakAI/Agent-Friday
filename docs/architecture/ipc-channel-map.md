# IPC Channel Map

> Agent Friday v3.12.0 — Complete reference for `window.eve.*` bridge
> Source of truth: `src/main/preload.ts` (~1855 lines)
> Last updated: 2026-03-24

All methods use `ipcRenderer.invoke()` (request/response) unless noted as `send` (fire-and-forget) or `on` (event listener).

---

## 1. Core & System

### `eve.window` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `minimize()` | `window:minimize` | Minimize app window |
| `maximize()` | `window:maximize` | Maximize/restore app window |
| `close()` | `window:close` | Close app window |

### `eve.shell` (2 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `showInFolder(path)` | `shell:show-in-folder` | Reveal file in OS file manager |
| `openPath(path)` | `shell:open-path` | Open file with default app |

### `eve.system` (2 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getStats()` | `system:stats` | Get CPU, RAM, disk usage |
| `getProcesses(limit?)` | `system:processes` | List top processes by resource use |

### `eve.telemetry` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getAggregates(category?)` | `telemetry:get-aggregates` | Get aggregated telemetry by category |
| `getRecentEvents(count?, category?)` | `telemetry:get-recent-events` | Fetch recent telemetry events |
| `clear()` | `telemetry:clear` | Clear all telemetry data |
| `appLaunched(appId)` | `telemetry:app-launched` | Record an app launch event |
| `recordError(name, msg?)` | `telemetry:record-error` | Record an error event |

### `eve.settings` (8 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `get()` | `settings:get` | Get all settings |
| `setAutoLaunch(enabled)` | `settings:set-auto-launch` | Toggle auto-launch on OS startup |
| `setAutoScreenCapture(enabled)` | `settings:set-auto-screen-capture` | Toggle automatic screen capture |
| `setApiKey(key, value)` | `settings:set-api-key` | Store API key (gemini, anthropic, etc.) |
| `validateApiKey(keyType, value)` | `settings:validate-api-key` | Test API key validity |
| `checkApiHealth()` | `settings:check-api-health` | Check health of all configured APIs |
| `setObsidianVaultPath(path)` | `settings:set-obsidian-vault-path` | Set Obsidian vault directory |
| `set(key, value)` | `settings:set` | Set arbitrary setting by key |

### `eve.sessionHealth` (9 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `get()` | `session-health:get` | Get current session health summary |
| `reset()` | `session-health:reset` | Reset session health counters |
| `sessionStarted()` | `session-health:session-started` | Mark session as started |
| `recordToolCall(name, success, ms)` | `session-health:record-tool-call` | Record a tool call outcome |
| `recordError(source, msg)` | `session-health:record-error` | Record an error |
| `recordWsClose(code, reason)` | `session-health:record-ws-close` | Record WebSocket close |
| `recordReconnect(type, success)` | `session-health:record-reconnect` | Record reconnect attempt |
| `recordVoiceAnchor()` | `session-health:record-voice-anchor` | Record voice anchor timestamp |
| `recordPromptSize(chars)` | `session-health:record-prompt-size` | Record prompt character count |
| `onUpdate(cb)` | `session-health:update` | **Event**: session health updated |

### Root-level helpers (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getApiPort()` | `get-api-port` | Get internal Express server port |
| `getGeminiApiKey()` | `get-gemini-api-key` | Get Gemini API key for renderer |
| `getLiveSystemInstruction()` | `get-live-system-instruction` | Get system prompt for Gemini Live |
| `onApiHealthChange(cb)` | `api-health:update` | **Event**: API health status changed |

---

## 2. Voice & Audio

### `eve.voice` (6 sub-namespaces + 12 event listeners)

#### `eve.voice.whisper` (7 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `loadModel(size?)` | `voice:whisper:load-model` | Load Whisper model (base/small/medium) |
| `unloadModel()` | `voice:whisper:unload-model` | Unload Whisper model from memory |
| `isReady()` | `voice:whisper:is-ready` | Check if Whisper is loaded |
| `transcribe(audio)` | `voice:whisper:transcribe` | Transcribe PCM audio to text |
| `getAvailableModels()` | `voice:whisper:get-available-models` | List available Whisper model sizes |
| `isModelDownloaded(size?)` | `voice:whisper:is-model-downloaded` | Check if model file exists locally |
| `downloadModel(size?)` | `voice:whisper:download-model` | Download a Whisper model |
| `onDownloadProgress(cb)` | `voice:whisper:download-progress` | **Event**: model download progress |

#### `eve.voice.capture` (4 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `start()` | `voice:capture:start` | Start microphone capture |
| `stop()` | `voice:capture:stop` | Stop microphone capture |
| `isCapturing()` | `voice:capture:is-capturing` | Check if mic is active |
| `getAudioLevel()` | `voice:capture:get-audio-level` | Get current audio level (RMS) |

#### `eve.voice.pipeline` (4 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `start()` | `voice:pipeline:start` | Start transcription pipeline |
| `stop()` | `voice:pipeline:stop` | Stop transcription pipeline |
| `isListening()` | `voice:pipeline:is-listening` | Check if pipeline is active |
| `getStats()` | `voice:pipeline:get-stats` | Get pipeline statistics |

#### `eve.voice.tts` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `loadEngine(backend?)` | `voice:tts:load-engine` | Load TTS engine (kokoro/piper) |
| `unloadEngine()` | `voice:tts:unload-engine` | Unload TTS engine |
| `isReady()` | `voice:tts:is-ready` | Check if TTS is loaded |
| `synthesize(text, opts?)` | `voice:tts:synthesize` | Synthesize text to audio |
| `getAvailableVoices()` | `voice:tts:get-available-voices` | List available TTS voices |
| `getInfo()` | `voice:tts:get-info` | Get active TTS engine info |

#### `eve.voice.profiles` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getActive()` | `voice:profiles:get-active` | Get active voice profile |
| `setActive(id)` | `voice:profiles:set-active` | Set active voice profile |
| `list()` | `voice:profiles:list` | List all voice profiles |
| `create(opts)` | `voice:profiles:create` | Create new voice profile |
| `delete(id)` | `voice:profiles:delete` | Delete voice profile |
| `preview(profileId)` | `voice:profiles:preview` | Preview a voice profile |

#### `eve.voice.speech` (7 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `speak(text, opts?)` | `voice:speech:speak` | Queue text for TTS playback |
| `speakImmediate(text)` | `voice:speech:speak-immediate` | Speak immediately (skip queue) |
| `stop()` | `voice:speech:stop` | Stop all speech |
| `pause()` | `voice:speech:pause` | Pause speech |
| `resume()` | `voice:speech:resume` | Resume paused speech |
| `isSpeaking()` | `voice:speech:is-speaking` | Check if currently speaking |
| `getQueueLength()` | `voice:speech:get-queue-length` | Get speech queue depth |

#### `eve.voice.binaries` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `ensureWhisper()` | `voice:ensure-whisper-binary` | Download/verify Whisper binary |
| `ensureTTS()` | `voice:ensure-tts-binary` | Download/verify TTS binary |
| `ensureTTSModel()` | `voice:ensure-tts-model` | Download/verify TTS model files |
| `onDownloadProgress(cb)` | `voice:binary-download-progress` | **Event**: binary download progress |

#### Voice events (on `eve.voice.*`)

| Listener | IPC Channel | Description |
|---|---|---|
| `onVoiceStart(cb)` | `voice:event:voice-start` | VAD detected speech start |
| `onVoiceEnd(cb)` | `voice:event:voice-end` | VAD detected speech end |
| `onAudioChunk(cb)` | `voice:event:audio-chunk` | Raw audio chunk captured |
| `onCaptureError(cb)` | `voice:event:capture-error` | Mic capture error |
| `onTranscript(cb)` | `voice:event:transcript` | Final transcript ready |
| `onPartial(cb)` | `voice:event:partial` | Partial transcript update |
| `onPipelineError(cb)` | `voice:event:pipeline-error` | Pipeline error |
| `onUtteranceStart(cb)` | `voice:event:utterance-start` | TTS utterance started |
| `onUtteranceEnd(cb)` | `voice:event:utterance-end` | TTS utterance ended |
| `onQueueEmpty(cb)` | `voice:event:queue-empty` | TTS queue emptied |
| `onInterrupted(cb)` | `voice:event:interrupted` | TTS interrupted by user |
| `onPlayChunk(cb)` | `voice:play-chunk` | Receive TTS audio for speaker playback |

### `eve.voiceState` (3 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `voice-state:get-state` | Get current voice state machine state |
| `getTransitionLog()` | `voice-state:get-transition-log` | Get state transition history |
| `getHealth()` | `voice-state:get-health` | Get voice subsystem health |
| `onStateChange(cb)` | `voice-state:event:state-change` | **Event**: voice state transitioned |

### `eve.voiceFallback` (4 methods + 4 events)

| Method | IPC Channel | Description |
|---|---|---|
| `probeAvailability()` | `voice-fallback:probe-availability` | Probe which voice paths are available |
| `startBestPath(prompt, tools)` | `voice-fallback:start-best-path` | Start best available voice path |
| `getCurrentPath()` | `voice-fallback:get-current-path` | Get currently active voice path |
| `switchTo(path, reason)` | `voice-fallback:switch-to` | Force switch to a specific path |
| `onSwitchStart(cb)` | `voice-fallback:event:switch-start` | **Event**: path switch initiated |
| `onSwitchComplete(cb)` | `voice-fallback:event:switch-complete` | **Event**: path switch completed |
| `onAllPathsExhausted(cb)` | `voice-fallback:event:all-paths-exhausted` | **Event**: no voice paths available |
| `onSwitchFailed(cb)` | `voice-fallback:event:switch-failed` | **Event**: path switch failed |

### `eve.connectionStage` (1 method + 4 events)

| Method | IPC Channel | Description |
|---|---|---|
| `getCurrentStage()` | `connection-stage:get-current` | Get current connection stage |
| `onStageEnter(cb)` | `connection-stage:event:stage-enter` | **Event**: entered a connection stage |
| `onStageComplete(cb)` | `connection-stage:event:stage-complete` | **Event**: stage completed |
| `onStageTimeout(cb)` | `connection-stage:event:stage-timeout` | **Event**: stage timed out |
| `onAllComplete(cb)` | `connection-stage:event:all-complete` | **Event**: all stages complete |

### `eve.localConversation` (3 methods + 5 events)

| Method | IPC Channel | Description |
|---|---|---|
| `start(prompt, tools, initial?)` | `local-conversation:start` | Start local voice conversation |
| `sendText(text)` | `local-conversation:send` | Send text into active local conversation |
| `stop()` | `local-conversation:stop` | Stop local conversation |
| `onStarted(cb)` | `local-conversation:event:started` | **Event**: conversation started |
| `onTranscript(cb)` | `local-conversation:event:transcript` | **Event**: user transcript |
| `onResponse(cb)` | `local-conversation:event:response` | **Event**: agent response text |
| `onAgentFinalized(cb)` | `local-conversation:event:agent-finalized` | **Event**: agent config finalized |
| `onError(cb)` | `local-conversation:event:error` | **Event**: conversation error |

### `eve.voiceAudition` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `generateSample(voice, phrase?)` | `voice-audition:generate-sample` | Generate TTS sample for a voice |
| `getRecommendations(gender)` | `voice-audition:get-recommendations` | Get voice recommendations by gender |
| `getCatalog()` | `voice-audition:get-catalog` | Get full voice catalog |

### `eve.callIntegration` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `isVirtualAudioAvailable()` | `call:is-virtual-audio-available` | Check virtual audio device availability |
| `enterCallMode(url?)` | `call:enter-call-mode` | Enter call mode for meeting |
| `exitCallMode()` | `call:exit-call-mode` | Exit call mode |
| `isInCallMode()` | `call:is-in-call-mode` | Check if in call mode |
| `openMeetingUrl(url)` | `call:open-meeting-url` | Open meeting URL in browser |
| `getContextString()` | `call:get-context-string` | Get call context for prompt injection |

---

## 3. Intelligence & LLM

### `eve.intelligenceRouter` (16 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `classifyTask(params)` | `router:classify-task` | Classify task complexity |
| `selectModel(task)` | `router:select-model` | Select optimal model for task |
| `classifyAndRoute(params)` | `router:classify-and-route` | Classify + select in one call |
| `recordOutcome(id, outcome)` | `router:record-outcome` | Record routing decision outcome |
| `getModel(modelId)` | `router:get-model` | Get model config by ID |
| `getAllModels()` | `router:get-all-models` | List all registered models |
| `getAvailableModels()` | `router:get-available-models` | List currently available models |
| `registerModel(model)` | `router:register-model` | Register a new model |
| `setModelAvailability(id, avail)` | `router:set-model-availability` | Set model availability flag |
| `resetModelFailures(id)` | `router:reset-model-failures` | Reset failure count for model |
| `getDecision(id)` | `router:get-decision` | Get a routing decision record |
| `getRecentDecisions(limit?)` | `router:get-recent-decisions` | Get recent routing decisions |
| `getDecisionsForModel(id, limit?)` | `router:get-decisions-for-model` | Get decisions for a specific model |
| `getStats()` | `router:get-stats` | Get routing statistics |
| `getConfig()` | `router:get-config` | Get router configuration |
| `updateConfig(partial)` | `router:update-config` | Update router configuration |
| `getPromptContext()` | `router:get-prompt-context` | Get router prompt context block |
| `discoverLocalModels()` | `router:discover-local-models` | Scan for available local models |

### `eve.ollama` (5 methods + 5 events)

| Method | IPC Channel | Description |
|---|---|---|
| `start()` | `ollama:start` | Start Ollama service |
| `stop()` | `ollama:stop` | Stop Ollama service |
| `getHealth()` | `ollama:get-health` | Get Ollama health status |
| `getAvailableModels()` | `ollama:get-available-models` | List models in Ollama |
| `getLoadedModels()` | `ollama:get-loaded-models` | List currently loaded models |
| `isModelAvailable(name)` | `ollama:is-model-available` | Check if model exists |
| `pullModel(name)` | `ollama:pull-model` | Pull model from registry |
| `onHealthy(cb)` | `ollama:event:healthy` | **Event**: Ollama became healthy |
| `onUnhealthy(cb)` | `ollama:event:unhealthy` | **Event**: Ollama became unhealthy |
| `onHealthChange(cb)` | `ollama:event:health-change` | **Event**: health status changed |
| `onModelLoaded(cb)` | `ollama:event:model-loaded` | **Event**: model loaded |
| `onModelUnloaded(cb)` | `ollama:event:model-unloaded` | **Event**: model unloaded |
| `onPullProgress(cb)` | `ollama:event:pull-progress` | **Event**: model pull progress |

### `eve.vision` (6 methods + 2 sub-namespaces + 2 events)

| Method | IPC Channel | Description |
|---|---|---|
| `loadModel(opts?)` | `vision:load-model` | Load vision model |
| `unloadModel()` | `vision:unload-model` | Unload vision model |
| `describe(imgB64, opts?)` | `vision:describe` | Describe image content |
| `answer(imgB64, question)` | `vision:answer` | Answer question about image |
| `isReady()` | `vision:is-ready` | Check if vision model loaded |
| `getModelInfo()` | `vision:get-model-info` | Get vision model metadata |

#### `eve.vision.screen` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `captureScreen()` | `vision:screen:capture-screen` | Capture full screen |
| `captureWindow(id)` | `vision:screen:capture-window` | Capture specific window |
| `captureRegion(region)` | `vision:screen:capture-region` | Capture screen region |
| `getContext()` | `vision:screen:get-context` | Get screen context summary |
| `startAutoCapture(ms?)` | `vision:screen:start-auto-capture` | Start periodic screen capture |
| `stopAutoCapture()` | `vision:screen:stop-auto-capture` | Stop periodic screen capture |

#### `eve.vision.understand` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `processImage(imgB64, opts?)` | `vision:understand:process-image` | Process and understand image |
| `processClipboard()` | `vision:understand:process-clipboard` | Process image from clipboard |
| `handleDrop(imgB64)` | `vision:understand:handle-drop` | Process dropped image |
| `handleFileSelect(path)` | `vision:understand:handle-file-select` | Process selected image file |
| `getLastResult()` | `vision:understand:get-last-result` | Get last understanding result |

#### Vision events

| Listener | IPC Channel | Description |
|---|---|---|
| `onContextUpdate(cb)` | `vision:event:context-update` | **Event**: screen context updated |
| `onImageResult(cb)` | `vision:event:image-result` | **Event**: image understanding result |

### `eve.intelligence` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getBriefing()` | `intelligence:get-briefing` | Get intelligence briefing text |
| `listAll()` | `intelligence:list-all` | List all intelligence items |
| `setup(topics)` | `intelligence:setup` | Configure intelligence topics |

---

## 4. Memory & Context

### `eve.memory` (8 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getShortTerm()` | `memory:get-short-term` | Get short-term memory |
| `getMediumTerm()` | `memory:get-medium-term` | Get medium-term memory |
| `getLongTerm()` | `memory:get-long-term` | Get long-term memory |
| `updateShortTerm(msgs)` | `memory:update-short-term` | Update short-term memory |
| `extract(history)` | `memory:extract` | Extract memories from conversation |
| `updateLongTerm(id, updates)` | `memory:update-long-term` | Update a long-term memory |
| `deleteLongTerm(id)` | `memory:delete-long-term` | Delete a long-term memory |
| `deleteMediumTerm(id)` | `memory:delete-medium-term` | Delete a medium-term memory |
| `addImmediate(fact, category)` | `memory:add-immediate` | Add an immediate memory fact |

### `eve.chatHistory` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `load()` | `chat-history:load` | Load chat history |
| `save(messages)` | `chat-history:save` | Save chat history |
| `clear()` | `chat-history:clear` | Clear chat history |

### `eve.episodic` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `create(transcript, start, end)` | `episodic:create` | Create episodic memory |
| `list()` | `episodic:list` | List all episodes |
| `search(query)` | `episodic:search` | Search episodes |
| `get(id)` | `episodic:get` | Get episode by ID |
| `delete(id)` | `episodic:delete` | Delete episode |
| `recent(count?)` | `episodic:recent` | Get recent episodes |

### `eve.contextStream` (11 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `push(event)` | `context-stream:push` | Push context event |
| `snapshot()` | `context-stream:snapshot` | Get full context snapshot |
| `recent(opts?)` | `context-stream:recent` | Get recent events |
| `byType(type, limit?)` | `context-stream:by-type` | Get events by type |
| `latestByType()` | `context-stream:latest-by-type` | Get latest event per type |
| `contextString()` | `context-stream:context-string` | Get context as string |
| `promptContext()` | `context-stream:prompt-context` | Get prompt-ready context block |
| `status()` | `context-stream:status` | Get stream status |
| `prune()` | `context-stream:prune` | Prune old events |
| `setEnabled(enabled)` | `context-stream:set-enabled` | Enable/disable stream |
| `clear()` | `context-stream:clear` | Clear all events |

### `eve.contextGraph` (11 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `snapshot()` | `context-graph:snapshot` | Get full graph snapshot |
| `activeStream()` | `context-graph:active-stream` | Get active context stream |
| `recentStreams(limit?)` | `context-graph:recent-streams` | Get recent streams |
| `streamsByTask(task)` | `context-graph:streams-by-task` | Get streams for a task |
| `entitiesByType(type, limit?)` | `context-graph:entities-by-type` | Get entities by type |
| `topEntities(limit?)` | `context-graph:top-entities` | Get top entities by relevance |
| `activeEntities(windowMs?)` | `context-graph:active-entities` | Get recently active entities |
| `relatedEntities(type, val, limit?)` | `context-graph:related-entities` | Find related entities |
| `contextString()` | `context-graph:context-string` | Get graph as string |
| `promptContext()` | `context-graph:prompt-context` | Get prompt-ready context block |
| `status()` | `context-graph:status` | Get graph status |
| `onStreamUpdate(cb)` | `context:stream-update` | **Event**: context stream updated |

### `eve.toolRouter` (8 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `suggestions()` | `tool-router:suggestions` | Get tool suggestions |
| `activeCategory()` | `tool-router:active-category` | Get active tool category |
| `categoryScores()` | `tool-router:category-scores` | Get category relevance scores |
| `snapshot()` | `tool-router:snapshot` | Get full router snapshot |
| `contextString()` | `tool-router:context-string` | Get router context as string |
| `promptContext()` | `tool-router:prompt-context` | Get prompt-ready context block |
| `status()` | `tool-router:status` | Get router status |
| `registerTools(tools)` | `tool-router:register-tools` | Register tools with router |
| `unregisterTool(name)` | `tool-router:unregister-tool` | Unregister a tool |
| `config()` | `tool-router:config` | Get router config |

### `eve.ambient` (2 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `ambient:get-state` | Get ambient context state |
| `getContextString()` | `ambient:get-context-string` | Get ambient context as string |

### `eve.search` (2 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `query(query, opts?)` | `search:query` | Unified search across memories, notes, docs |
| `stats()` | `search:stats` | Get search index stats |

---

## 5. Security & Trust

### `eve.vault` (5 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `isInitialized()` | `vault:is-initialized` | Check if vault exists |
| `isUnlocked()` | `vault:is-unlocked` | Check if vault is unlocked |
| `initializeNew(passphrase)` | `vault:initialize-new` | Create new vault |
| `unlock(passphrase)` | `vault:unlock` | Unlock vault |
| `resetAll()` | `vault:reset-all` | Reset vault (destroys all secrets) |
| `onBootComplete(cb)` | `vault:boot-complete` | **Event**: vault boot finished |

### `eve.integrity` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `integrity:get-state` | Get integrity verification state |
| `isInSafeMode()` | `integrity:is-safe-mode` | Check if in safe mode |
| `acknowledgeMemoryChanges()` | `integrity:acknowledge-memory-changes` | Acknowledge detected memory changes |
| `verify()` | `integrity:verify` | Run integrity verification |
| `reset()` | `integrity:reset` | Reset integrity baselines |

### `eve.trustGraph` (12 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `lookup(name)` | `trust:lookup` | Look up person by name |
| `updateEvidence(person, evidence)` | `trust:update-evidence` | Update trust evidence |
| `logComm(person, event)` | `trust:log-comm` | Log communication event |
| `addAlias(personId, alias, type)` | `trust:add-alias` | Add name alias |
| `getAll()` | `trust:get-all` | Get all trust records |
| `getContext(personId)` | `trust:get-context` | Get person context |
| `getPromptContext()` | `trust:get-prompt-context` | Get trust prompt block |
| `findByDomain(domain)` | `trust:find-by-domain` | Find persons by email domain |
| `getMostTrusted(limit?)` | `trust:most-trusted` | Get most trusted persons |
| `getRecent(limit?)` | `trust:recent` | Get recently contacted persons |
| `updateNotes(personId, notes)` | `trust:update-notes` | Update person notes |
| `linkPersons(idA, idB, label)` | `trust:link-persons` | Create relationship link |

### `eve.agentTrust` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `agent-trust:get-state` | Get agent trust state |
| `processMessage(msg)` | `agent-trust:process-message` | Process message for trust signals |
| `resetSession()` | `agent-trust:reset-session` | Reset session trust |
| `getPromptBlock()` | `agent-trust:get-prompt-block` | Get trust prompt block |
| `getLabel()` | `agent-trust:get-label` | Get current trust label |
| `boost(amount)` | `agent-trust:boost` | Boost trust score |

---

## 6. Setup & Hardware

### `eve.setup` (7 methods + 4 events)

| Method | IPC Channel | Description |
|---|---|---|
| `isFirstRun()` | `setup:is-first-run` | Check if first run |
| `getState()` | `setup:get-state` | Get setup wizard state |
| `start()` | `setup:start` | Start setup wizard |
| `skip()` | `setup:skip` | Skip setup |
| `confirmTier(tier)` | `setup:confirm-tier` | Confirm hardware tier |
| `startDownload()` | `setup:start-download` | Start model downloads |
| `getDownloadProgress()` | `setup:get-download-progress` | Get download progress |
| `complete()` | `setup:complete` | Mark setup complete |
| `reset()` | `setup:reset` | Reset setup state |
| `onStateChanged(cb)` | `setup:event:state-changed` | **Event**: setup state changed |
| `onDownloadProgress(cb)` | `setup:event:download-progress` | **Event**: download progress |
| `onComplete(cb)` | `setup:event:complete` | **Event**: setup completed |
| `onError(cb)` | `setup:event:error` | **Event**: setup error |

### `eve.hardware` (15 methods + 4 events)

| Method | IPC Channel | Description |
|---|---|---|
| `detect()` | `hardware:detect` | Detect hardware capabilities |
| `getProfile()` | `hardware:get-profile` | Get hardware profile |
| `refresh()` | `hardware:refresh` | Refresh hardware detection |
| `getEffectiveVRAM()` | `hardware:get-effective-vram` | Get usable VRAM |
| `getTier(profile)` | `hardware:get-tier` | Classify hardware tier |
| `getModelList(tier)` | `hardware:get-model-list` | Get recommended models for tier |
| `estimateVRAM(models)` | `hardware:estimate-vram` | Estimate VRAM for model set |
| `recommend(profile)` | `hardware:recommend` | Get full recommendation |
| `loadTierModels(tier)` | `hardware:load-tier-models` | Load all models for tier |
| `getLoadedModels()` | `hardware:get-loaded-models` | Get currently loaded models |
| `getVRAMUsage()` | `hardware:get-vram-usage` | Get VRAM usage breakdown |
| `loadModel(name)` | `hardware:load-model` | Load a specific model |
| `unloadModel(name)` | `hardware:unload-model` | Unload a specific model |
| `evictLeastRecent()` | `hardware:evict-least-recent` | Evict LRU model |
| `getOrchestratorState()` | `hardware:get-orchestrator-state` | Get model orchestrator state |
| `markModelUsed(name)` | `hardware:mark-model-used` | Mark model as recently used |
| `onDetected(cb)` | `hardware:event:detected` | **Event**: hardware detected |
| `onModelLoaded(cb)` | `hardware:event:model-loaded` | **Event**: model loaded |
| `onModelUnloaded(cb)` | `hardware:event:model-unloaded` | **Event**: model unloaded |
| `onVRAMWarning(cb)` | `hardware:event:vram-warning` | **Event**: VRAM threshold exceeded |

### `eve.profile` (8 methods + 3 events)

| Method | IPC Channel | Description |
|---|---|---|
| `create(opts)` | `profile:create` | Create agent profile |
| `get(id)` | `profile:get` | Get profile by ID |
| `getActive()` | `profile:get-active` | Get active profile |
| `setActive(id)` | `profile:set-active` | Set active profile |
| `update(id, data)` | `profile:update` | Update profile |
| `delete(id)` | `profile:delete` | Delete profile |
| `export(id)` | `profile:export` | Export profile JSON |
| `import(json)` | `profile:import` | Import profile JSON |
| `list()` | `profile:list` | List all profiles |
| `onChanged(cb)` | `profile:event:changed` | **Event**: active profile changed |
| `onCreated(cb)` | `profile:event:created` | **Event**: profile created |
| `onDeleted(cb)` | `profile:event:deleted` | **Event**: profile deleted |

### `eve.onboarding` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `isFirstRun()` | `onboarding:is-first-run` | Check if first run |
| `isComplete()` | `onboarding:is-complete` | Check if onboarding complete |
| `getAgentConfig()` | `onboarding:get-config` | Get agent configuration |
| `getToolDeclarations()` | `onboarding:get-tool-declarations` | Get tool declarations for onboarding |
| `getFirstGreeting()` | `onboarding:get-first-greeting` | Get first greeting text |
| `finalizeAgent(config)` | `onboarding:finalize-agent` | Finalize agent identity |

### `eve.featureSetup` (8 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `initialize()` | `feature-setup:initialize` | Initialize feature setup |
| `getState()` | `feature-setup:get-state` | Get feature setup state |
| `getPrompt(step)` | `feature-setup:get-prompt` | Get prompt for setup step |
| `advance(step, action)` | `feature-setup:advance` | Advance to next step |
| `isComplete()` | `feature-setup:is-complete` | Check if all features set up |
| `getCurrentStep()` | `feature-setup:get-current-step` | Get current setup step |
| `getToolDeclaration()` | `feature-setup:get-tool-declaration` | Get tool declaration |
| `getToolDeclarations()` | `feature-setup:get-tool-declarations` | Get all tool declarations |

---

## 7. Agents & Delegation

### `eve.agents` (5 methods + 2 events)

| Method | IPC Channel | Description |
|---|---|---|
| `spawn(type, desc, input)` | `agents:spawn` | Spawn background agent |
| `list(status?)` | `agents:list` | List agents by status |
| `get(taskId)` | `agents:get` | Get agent task |
| `cancel(taskId)` | `agents:cancel` | Cancel agent task |
| `getTypes()` | `agents:types` | List available agent types |
| `onUpdate(cb)` | `agents:update` | **Event**: agent task updated |
| `onSpeak(cb)` | `agents:speak` | **Event**: agent wants to speak (with audio) |

### `eve.delegation` (14 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `registerRoot(taskId, type, desc, tier?)` | `delegation:register-root` | Register root delegation task |
| `spawnSubAgent(payload)` | `delegation:spawn-sub-agent` | Spawn sub-agent |
| `reportCompletion(taskId, result, err)` | `delegation:report-completion` | Report task completion |
| `collectResults(parentId)` | `delegation:collect-results` | Collect sub-agent results |
| `haltTree(taskId)` | `delegation:halt-tree` | Halt delegation tree |
| `haltAll()` | `delegation:halt-all` | Halt all delegation trees |
| `getTree(rootId)` | `delegation:get-tree` | Get delegation tree |
| `getNode(taskId)` | `delegation:get-node` | Get single tree node |
| `getActiveTrees()` | `delegation:get-active-trees` | List active delegation trees |
| `getAllTrees()` | `delegation:get-all-trees` | List all delegation trees |
| `getAncestry(taskId)` | `delegation:get-ancestry` | Get task ancestry chain |
| `getStats()` | `delegation:get-stats` | Get delegation statistics |
| `getConfig()` | `delegation:get-config` | Get delegation config |
| `updateConfig(updates)` | `delegation:update-config` | Update delegation config |
| `cleanup(maxAgeMs?)` | `delegation:cleanup` | Clean up old delegations |
| `onUpdate(cb)` | `delegation:update` | **Event**: delegation updated |

### `eve.agentNetwork` (30 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getIdentity()` | `agent-net:get-identity` | Get this agent's identity |
| `getAgentId()` | `agent-net:get-agent-id` | Get this agent's ID |
| `generatePairingOffer()` | `agent-net:generate-pairing-offer` | Generate pairing offer |
| `getActivePairingCode()` | `agent-net:get-active-pairing-code` | Get active pairing code |
| `acceptPairing(remote, personId, trust)` | `agent-net:accept-pairing` | Accept pairing request |
| `recordInboundPairing(remote)` | `agent-net:record-inbound-pairing` | Record inbound pairing |
| `blockAgent(agentId)` | `agent-net:block-agent` | Block a peer agent |
| `unpairAgent(agentId)` | `agent-net:unpair-agent` | Unpair from agent |
| `getPeer(agentId)` | `agent-net:get-peer` | Get peer info |
| `getAllPeers()` | `agent-net:get-all-peers` | List all peers |
| `getPairedPeers()` | `agent-net:get-paired-peers` | List paired peers |
| `getPendingPairingRequests()` | `agent-net:get-pending-pairing-requests` | List pending pairings |
| `updatePeerTrust(id, trust, personId?)` | `agent-net:update-peer-trust` | Update peer trust |
| `setAutoApproveTaskTypes(id, types)` | `agent-net:set-auto-approve-task-types` | Set auto-approve types |
| `updatePeerCapabilities(id, caps)` | `agent-net:update-peer-capabilities` | Update peer capabilities |
| `findPeersWithCapability(cap)` | `agent-net:find-peers-with-capability` | Find peers by capability |
| `createMessage(toId, type, payload)` | `agent-net:create-message` | Create outbound message |
| `processInboundMessage(msg)` | `agent-net:process-inbound-message` | Process inbound message |
| `getMessageLog(limit?)` | `agent-net:get-message-log` | Get message log |
| `createDelegation(targetId, desc, caps?, deadline?)` | `agent-net:create-delegation` | Create cross-agent delegation |
| `handleInboundDelegation(...)` | `agent-net:handle-inbound-delegation` | Handle inbound delegation |
| `approveDelegation(id)` | `agent-net:approve-delegation` | Approve delegation |
| `rejectDelegation(id)` | `agent-net:reject-delegation` | Reject delegation |
| `startDelegation(id)` | `agent-net:start-delegation` | Start delegation |
| `completeDelegation(id, result)` | `agent-net:complete-delegation` | Complete delegation |
| `failDelegation(id, error)` | `agent-net:fail-delegation` | Fail delegation |
| `cancelDelegation(id)` | `agent-net:cancel-delegation` | Cancel delegation |
| `getDelegation(id)` | `agent-net:get-delegation` | Get delegation record |
| `getAllDelegations()` | `agent-net:get-all-delegations` | List all delegations |
| `getDelegationsForAgent(id)` | `agent-net:get-delegations-for-agent` | List delegations for agent |
| `getPendingInboundDelegations()` | `agent-net:get-pending-inbound-delegations` | List pending inbound |
| `getSafetyNumber(agentId)` | `agent-net:get-safety-number` | Get SAS safety number |
| `verifySAS(agentId)` | `agent-net:verify-sas` | Verify SAS code |
| `isSASVerified(agentId)` | `agent-net:is-sas-verified` | Check SAS verification |
| `getStats()` | `agent-net:get-stats` | Get network statistics |
| `getConfig()` | `agent-net:get-config` | Get network config |
| `updateConfig(partial)` | `agent-net:update-config` | Update network config |
| `getPromptContext()` | `agent-net:get-prompt-context` | Get network prompt block |

---

## 8. Productivity

### `eve.commitments` (18 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getActive()` | `commitment:get-active` | Get active commitments |
| `getOverdue()` | `commitment:get-overdue` | Get overdue commitments |
| `getByPerson(name)` | `commitment:get-by-person` | Get commitments for person |
| `getUpcoming(hours?)` | `commitment:get-upcoming` | Get upcoming commitments |
| `getById(id)` | `commitment:get-by-id` | Get commitment by ID |
| `getAll()` | `commitment:get-all` | Get all commitments |
| `add(mention)` | `commitment:add` | Add commitment |
| `complete(id, notes?)` | `commitment:complete` | Mark commitment complete |
| `cancel(id, reason?)` | `commitment:cancel` | Cancel commitment |
| `snooze(id, untilMs)` | `commitment:snooze` | Snooze commitment |
| `trackOutbound(msg)` | `commitment:track-outbound` | Track outbound message |
| `recordReply(recipient, channel)` | `commitment:record-reply` | Record reply received |
| `getUnreplied()` | `commitment:get-unreplied` | Get unreplied messages |
| `generateSuggestions()` | `commitment:generate-suggestions` | Generate follow-up suggestions |
| `getPendingSuggestions()` | `commitment:get-pending-suggestions` | Get pending suggestions |
| `markSuggestionDelivered(id)` | `commitment:mark-suggestion-delivered` | Mark suggestion delivered |
| `markSuggestionActedOn(id)` | `commitment:mark-suggestion-acted-on` | Mark suggestion acted on |
| `contextString()` | `commitment:context-string` | Get commitments as string |
| `promptContext()` | `commitment:prompt-context` | Get prompt context block |
| `status()` | `commitment:status` | Get module status |
| `config()` | `commitment:config` | Get module config |

### `eve.dailyBriefing` (15 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `generate(type, sourceData)` | `briefing:generate` | Generate briefing |
| `shouldGenerate()` | `briefing:should-generate` | Check if briefing needed |
| `adaptiveLength(sourceData)` | `briefing:adaptive-length` | Calculate adaptive length |
| `getLatest(type?)` | `briefing:get-latest` | Get latest briefing |
| `getLatestToday(type)` | `briefing:get-latest-today` | Get today's latest |
| `getById(id)` | `briefing:get-by-id` | Get briefing by ID |
| `getHistory(limit?)` | `briefing:get-history` | Get briefing history |
| `getAll()` | `briefing:get-all` | Get all briefings |
| `markDelivered(id, channel)` | `briefing:mark-delivered` | Mark briefing delivered |
| `markDeliveryFailed(id, ch, reason)` | `briefing:mark-delivery-failed` | Mark delivery failed |
| `isStale(type)` | `briefing:is-stale` | Check if briefing is stale |
| `scheduledTimeToday(timeStr)` | `briefing:scheduled-time-today` | Get scheduled time |
| `formatText(id)` | `briefing:format-text` | Format as plain text |
| `formatMarkdown(id)` | `briefing:format-markdown` | Format as markdown |
| `contextString()` | `briefing:context-string` | Get context string |
| `promptContext()` | `briefing:prompt-context` | Get prompt block |
| `status()` | `briefing:status` | Get module status |
| `config()` | `briefing:config` | Get module config |

### `eve.briefingDelivery` (2 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `list()` | `briefing:list` | List pending deliveries |
| `dismiss(id)` | `briefing:dismiss` | Dismiss briefing |
| `onNew(cb)` | `briefing:new` | **Event**: new briefing ready |

### `eve.workflowRecorder` (13 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `startRecording(name)` | `workflow:start-recording` | Start recording workflow |
| `stopRecording()` | `workflow:stop-recording` | Stop recording |
| `cancelRecording()` | `workflow:cancel-recording` | Cancel recording |
| `recordEvent(type, desc, payload?)` | `workflow:record-event` | Record workflow event |
| `addAnnotation(text)` | `workflow:add-annotation` | Add annotation |
| `addKeyFrame(path, app)` | `workflow:add-keyframe` | Add keyframe |
| `createTemplate(recordingId, overrides?)` | `workflow:create-template` | Create template from recording |
| `deleteTemplate(id)` | `workflow:delete-template` | Delete template |
| `status()` | `workflow:status` | Get recorder status |
| `getRecording(id)` | `workflow:get-recording` | Get recording |
| `getAllRecordings()` | `workflow:get-all-recordings` | List all recordings |
| `getRecentRecordings(limit?)` | `workflow:get-recent-recordings` | List recent recordings |
| `getTemplate(id)` | `workflow:get-template` | Get template |
| `getAllTemplates()` | `workflow:get-all-templates` | List all templates |
| `getTemplatesByTag(tag)` | `workflow:get-templates-by-tag` | List templates by tag |
| `deleteRecording(id)` | `workflow:delete-recording` | Delete recording |
| `config()` | `workflow:config` | Get config |

### `eve.workflowExecutor` (12 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `execute(templateId, params?, triggeredBy?)` | `wf-exec:execute` | Execute workflow template |
| `pause()` | `wf-exec:pause` | Pause execution |
| `resume()` | `wf-exec:resume` | Resume execution |
| `cancel()` | `wf-exec:cancel` | Cancel execution |
| `provideUserResponse(response)` | `wf-exec:provide-user-response` | Provide user response |
| `grantPermission(templateId, opts?)` | `wf-exec:grant-permission` | Grant execution permission |
| `revokePermission(templateId)` | `wf-exec:revoke-permission` | Revoke permission |
| `getPermissions()` | `wf-exec:get-permissions` | Get all permissions |
| `activeRun()` | `wf-exec:active-run` | Get active run |
| `isRunning()` | `wf-exec:is-running` | Check if running |
| `runHistory(limit?)` | `wf-exec:run-history` | Get run history |
| `getRun(runId)` | `wf-exec:get-run` | Get specific run |
| `getConfig()` | `wf-exec:get-config` | Get executor config |
| `updateConfig(updates)` | `wf-exec:update-config` | Update executor config |

### `eve.inbox` (9 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getMessages(opts?)` | `inbox:get-messages` | Get inbox messages |
| `getMessage(id)` | `inbox:get-message` | Get single message |
| `getStats()` | `inbox:get-stats` | Get inbox stats |
| `markRead(ids)` | `inbox:mark-read` | Mark messages read |
| `markUnread(ids)` | `inbox:mark-unread` | Mark messages unread |
| `archive(ids)` | `inbox:archive` | Archive messages |
| `unarchive(ids)` | `inbox:unarchive` | Unarchive messages |
| `delete(ids)` | `inbox:delete` | Delete messages |
| `markAllRead()` | `inbox:mark-all-read` | Mark all read |
| `getConfig()` | `inbox:get-config` | Get inbox config |
| `updateConfig(partial)` | `inbox:update-config` | Update inbox config |

### `eve.outbound` (18 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `createDraft(params)` | `outbound:create-draft` | Create outbound draft |
| `getDraft(id)` | `outbound:get-draft` | Get draft |
| `editDraft(id, updates)` | `outbound:edit-draft` | Edit draft |
| `deleteDraft(id)` | `outbound:delete-draft` | Delete draft |
| `getDrafts(opts?)` | `outbound:get-drafts` | List drafts |
| `getPending()` | `outbound:get-pending` | Get pending drafts |
| `approve(id)` | `outbound:approve` | Approve draft |
| `reject(id)` | `outbound:reject` | Reject draft |
| `approveAll()` | `outbound:approve-all` | Approve all pending |
| `tryAutoApprove(id)` | `outbound:try-auto-approve` | Try auto-approve |
| `send(id)` | `outbound:send` | Send approved draft |
| `approveAndSend(id)` | `outbound:approve-and-send` | Approve and send |
| `sendAllApproved()` | `outbound:send-all-approved` | Send all approved |
| `batchReview()` | `outbound:batch-review` | Batch review pending |
| `getStyleProfile(personId)` | `outbound:get-style-profile` | Get writing style profile |
| `updateStyleProfile(id, name, obs)` | `outbound:update-style-profile` | Update style profile |
| `getAllStyleProfiles()` | `outbound:get-all-style-profiles` | List all style profiles |
| `addStandingPermission(params)` | `outbound:add-standing-permission` | Add standing permission |
| `revokeStandingPermission(id)` | `outbound:revoke-standing-permission` | Revoke standing permission |
| `deleteStandingPermission(id)` | `outbound:delete-standing-permission` | Delete standing permission |
| `getStandingPermissions()` | `outbound:get-standing-permissions` | List standing permissions |
| `getAllStandingPermissions()` | `outbound:get-all-standing-permissions` | List all standing permissions |
| `getStats()` | `outbound:get-stats` | Get outbound stats |
| `getConfig()` | `outbound:get-config` | Get outbound config |
| `updateConfig(partial)` | `outbound:update-config` | Update outbound config |
| `getPromptContext()` | `outbound:get-prompt-context` | Get prompt context block |

### `eve.meetingIntel` (18 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `create(opts)` | `meeting-intel:create` | Create meeting intel record |
| `get(id)` | `meeting-intel:get` | Get meeting by ID |
| `list(opts?)` | `meeting-intel:list` | List meetings |
| `getActive()` | `meeting-intel:get-active` | Get active meeting |
| `update(meetingId, updates)` | `meeting-intel:update` | Update meeting |
| `start(meetingId)` | `meeting-intel:start` | Start meeting |
| `end(meetingId, opts?)` | `meeting-intel:end` | End meeting |
| `cancel(meetingId)` | `meeting-intel:cancel` | Cancel meeting |
| `endActive(transcript?)` | `meeting-intel:end-active` | End active meeting |
| `addNote(meetingId, note)` | `meeting-intel:add-note` | Add meeting note |
| `addNoteActive(content, type?)` | `meeting-intel:add-note-active` | Add note to active meeting |
| `setTranscript(meetingId, text)` | `meeting-intel:set-transcript` | Set full transcript |
| `setSummary(meetingId, summary)` | `meeting-intel:set-summary` | Set meeting summary |
| `search(query, limit?)` | `meeting-intel:search` | Search meetings |
| `stats()` | `meeting-intel:stats` | Get meeting stats |
| `recentSummaries(count?)` | `meeting-intel:recent-summaries` | Get recent summaries |
| `fromCalendar(event)` | `meeting-intel:from-calendar` | Create from calendar event |
| `quickStart(url, name?)` | `meeting-intel:quick-start` | Quick-start meeting |
| `refreshIntel(meetingId)` | `meeting-intel:refresh-intel` | Refresh intel for meeting |
| `getContext()` | `meeting-intel:get-context` | Get meeting context |

### `eve.meetingPrep` (1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `onBriefing(cb)` | `meeting:briefing` | **Event**: pre-meeting briefing ready |

### `eve.calendar` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `authenticate()` | `calendar:authenticate` | Authenticate with calendar |
| `isAuthenticated()` | `calendar:is-authenticated` | Check auth status |
| `getUpcoming(count?)` | `calendar:get-upcoming` | Get upcoming events |
| `getToday()` | `calendar:get-today` | Get today's events |
| `createEvent(opts)` | `calendar:create-event` | Create calendar event |

### `eve.communications` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `draft(request)` | `communications:draft` | Draft a communication |
| `refine(draftId, instruction)` | `communications:refine` | Refine a draft |
| `copy(draftId)` | `communications:copy` | Copy draft to clipboard |
| `openEmail(draftId)` | `communications:open-email` | Open draft in email client |
| `listDrafts()` | `communications:list-drafts` | List all drafts |

### `eve.scheduler` (3 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `listTools()` | `scheduler:list-tools` | List scheduler tools |
| `createTask(params)` | `scheduler:create-task` | Create scheduled task |
| `listTasks()` | `scheduler:list-tasks` | List scheduled tasks |
| `deleteTask(id)` | `scheduler:delete-task` | Delete scheduled task |
| `onTaskFired(cb)` | `scheduler:task-fired` | **Event**: scheduled task fired |

### `eve.notifications` (1 method + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `getRecent()` | `notifications:get-recent` | Get recent OS notifications |
| `onCaptured(cb)` | `notification:captured` | **Event**: OS notification captured |

---

## 9. OS & Files

### `eve.files` (14 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `listDirectory(path, hidden?)` | `files:list-directory` | List directory contents |
| `open(path)` | `files:open` | Open file with default app |
| `showInFolder(path)` | `files:show-in-folder` | Reveal in file manager |
| `getStats(path)` | `files:get-stats` | Get file stats (size, dates) |
| `exists(path)` | `files:exists` | Check if file exists |
| `readText(path)` | `files:read-text` | Read file as text |
| `rename(path, newName)` | `files:rename` | Rename file |
| `delete(path, useTrash?)` | `files:delete` | Delete file (trash or permanent) |
| `copy(src, destDir)` | `files:copy` | Copy file |
| `move(src, destDir)` | `files:move` | Move file |
| `createFolder(parent, name)` | `files:create-folder` | Create folder |
| `createFile(parent, name)` | `files:create-file` | Create empty file |
| `copyPath(path)` | `files:copy-path` | Copy file path to clipboard |
| `homeDir()` | `files:home-dir` | Get home directory path |

### `eve.fileSearch` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `search(query)` | `file-search:search` | Search files by query |
| `recentFiles(limit?, exts?)` | `file-search:recent` | Get recently modified files |
| `findDuplicates(dir, mode?)` | `file-search:duplicates` | Find duplicate files |

### `eve.fileWatcher` (4 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `addWatch(dir)` | `file-watcher:add-watch` | Watch directory for changes |
| `removeWatch(dir)` | `file-watcher:remove-watch` | Stop watching directory |
| `getWatched()` | `file-watcher:get-watched` | List watched directories |
| `getEvents(limit?)` | `file-watcher:get-events` | Get recent file events |
| `getContext()` | `file-watcher:context` | Get file context |
| `onFileModified(cb)` | `file:modified` | **Event**: file modified |

### `eve.notes` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `list()` | `notes:list` | List all notes |
| `get(id)` | `notes:get` | Get note by ID |
| `create(input)` | `notes:create` | Create note |
| `update(id, patch)` | `notes:update` | Update note |
| `delete(id)` | `notes:delete` | Delete note |
| `search(query)` | `notes:search` | Search notes |

### `eve.weather` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getCurrent()` | `weather:current` | Get current weather |
| `getForecast()` | `weather:forecast` | Get weather forecast |
| `setLocation(lat, lon, city, region?)` | `weather:set-location` | Set weather location |

### `eve.osEvents` (7 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `getPowerState()` | `os-events:power-state` | Get power state (battery, AC) |
| `getRecentEvents(limit?)` | `os-events:recent` | Get recent OS events |
| `getDisplays()` | `os-events:displays` | Get display info |
| `getFileAssociation(ext)` | `os-events:file-association` | Get file association for extension |
| `getFileAssociations(exts)` | `os-events:file-associations` | Get multiple file associations |
| `openWithDefault(path)` | `os-events:open-with-default` | Open file with default app |
| `getStartupPrograms()` | `os-events:startup-programs` | List startup programs |
| `getContext()` | `os-events:context` | Get OS context summary |
| `onOsEvent(cb)` | `os:event` | **Event**: OS event (lid, power, display) |

### `eve.documents` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `pickAndIngest()` | `documents:pick-and-ingest` | Pick file and ingest |
| `ingestFile(path)` | `documents:ingest-file` | Ingest specific file |
| `list()` | `documents:list` | List ingested documents |
| `get(id)` | `documents:get` | Get document by ID |
| `search(query)` | `documents:search` | Search documents |

### `eve.clipboard` (2 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `getRecent(count?)` | `clipboard:get-recent` | Get recent clipboard entries |
| `getCurrent()` | `clipboard:get-current` | Get current clipboard content |
| `onChanged(cb)` | `clipboard:changed` | **Event**: clipboard changed |

### `eve.project` (3 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `watch(rootPath)` | `project:watch` | Watch project directory |
| `list()` | `project:list` | List watched projects |
| `get(rootPath)` | `project:get` | Get project profile |
| `onUpdated(cb)` | `project:updated` | **Event**: project profile updated |

### `eve.screenCapture` (2 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `start()` | `screen-capture:start` | Start screen capture |
| `stop()` | `screen-capture:stop` | Stop screen capture |
| `onFrame(cb)` | `screen-capture:frame` | **Event**: captured frame (base64) |

### Legacy root-level

| Method | IPC Channel | Description |
|---|---|---|
| `onFileModified(cb)` | `file:modified` | **Event**: legacy alias for `fileWatcher.onFileModified` |

---

## 10. Apps & UI

### `eve.desktop` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `listTools()` | `desktop:list-tools` | List desktop automation tools |
| `callTool(name, args)` | `desktop:call-tool` | Call desktop tool |
| `focusWindow(target)` | `desktop:focus-window` | Focus a window |

### `eve.browser` (2 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `listTools()` | `browser:list-tools` | List browser automation tools |
| `callTool(name, args)` | `browser:call-tool` | Call browser tool |

### `eve.appContext` (1 method + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `get(appId)` | `app-context:get` | Get context for app |
| `onUpdate(cb)` | `app-context:update` | **Event**: app context updated |

### `eve.multimedia` (8 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `createPodcast(request)` | `multimedia:create-podcast` | Create podcast |
| `createVisual(request)` | `multimedia:create-visual` | Create visual (VEO-3) |
| `createAudioMessage(request)` | `multimedia:create-audio-message` | Create audio message |
| `createMusic(request)` | `multimedia:create-music` | Create music |
| `getPermissions()` | `multimedia:get-permissions` | Get media permissions |
| `updatePermissions(perms)` | `multimedia:update-permissions` | Update media permissions |
| `canCreate(level)` | `multimedia:can-create` | Check creation permission |
| `listMedia(type?)` | `multimedia:list-media` | List generated media |
| `getSpeakerPresets()` | `multimedia:get-speaker-presets` | Get speaker voice presets |
| `getMediaDir()` | `multimedia:get-media-dir` | Get media output directory |
| `saveCapture(dataURL, filename?)` | `multimedia:save-capture` | Save captured media |

### `eve.container` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `execute(payload)` | `container:execute` | Execute code in container |
| `cancel(taskId)` | `container:cancel` | Cancel container task |
| `status()` | `container:status` | Get container runtime status |
| `list()` | `container:list` | List container tasks |
| `get(taskId)` | `container:get` | Get container task |
| `available()` | `container:available` | Check if Docker available |
| `activeCount()` | `container:active-count` | Get active container count |

### `eve.code` (1 method)

| Method | IPC Channel | Description |
|---|---|---|
| `executeDirect(code, lang)` | `code:execute-direct` | Execute code directly (no container) |

### `eve.confirmation` (1 method + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `respond(id, approved)` | `desktop:confirm-response` | Respond to confirmation request |
| `onRequest(cb)` | `desktop:confirm-request` | **Event**: tool confirmation requested |

### `eve.toolExecution` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `execute(toolCall)` | `tool:execute` | Execute a tool call |
| `confirmResponse(decisionId, approved)` | `tool:confirm-response` | Confirm tool execution |
| `listTools()` | `tool:list-tools` | List available tools |

### `eve.mcp` (4 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `listTools()` | `mcp:list-tools` | List MCP tools |
| `callTool(name, args)` | `mcp:call-tool` | Call MCP tool |
| `getStatus()` | `mcp:get-status` | Get MCP status |
| `addServer(config)` | `mcp:add-server` | Add MCP server |

---

## 11. Personality & Evolution

### `eve.psychProfile` (4 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `generate(responses)` | `psych:generate` | Generate psych profile |
| `get()` | `psych:get` | Get current profile |
| `saveIntakeResponses(responses)` | `psych:save-intake` | Save intake responses |
| `getIntakeResponses()` | `psych:get-intake` | Get saved intake responses |

### `eve.personalityCalibration` (14 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `processMessage(text, ms?)` | `calibration:process-message` | Process message for calibration |
| `recordDismissal()` | `calibration:record-dismissal` | Record user dismissed suggestion |
| `recordEngagement()` | `calibration:record-engagement` | Record user engaged |
| `incrementSession()` | `calibration:increment-session` | Increment session counter |
| `getDimensions()` | `calibration:get-dimensions` | Get calibration dimensions |
| `getState()` | `calibration:get-state` | Get calibration state |
| `getDismissalRate()` | `calibration:get-dismissal-rate` | Get dismissal rate |
| `getEffectiveProactivity(critical)` | `calibration:get-effective-proactivity` | Get proactivity level |
| `getHistory()` | `calibration:get-history` | Get calibration history |
| `getExplanation()` | `calibration:get-explanation` | Get calibration explanation |
| `getPromptContext()` | `calibration:get-prompt-context` | Get prompt block |
| `getVisualWarmthModifier()` | `calibration:get-visual-warmth-modifier` | Get warmth modifier for UI |
| `getVisualEnergyModifier()` | `calibration:get-visual-energy-modifier` | Get energy modifier for UI |
| `getConfig()` | `calibration:get-config` | Get calibration config |
| `updateConfig(partial)` | `calibration:update-config` | Update calibration config |
| `resetDimension(dim)` | `calibration:reset-dimension` | Reset single dimension |
| `resetAll()` | `calibration:reset-all` | Reset all calibration |

### `eve.memoryPersonalityBridge` (17 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `recordEngagement(memId, type, ctx)` | `bridge:record-engagement` | Record memory engagement |
| `getEngagements()` | `bridge:get-engagements` | Get engagements |
| `getPriorityAdjustments()` | `bridge:get-priority-adjustments` | Get priority adjustments |
| `getExtractionGuidance()` | `bridge:get-extraction-guidance` | Get extraction guidance |
| `getExtractionHints()` | `bridge:get-extraction-hints` | Get extraction hints |
| `recomputeExtractionHints()` | `bridge:recompute-extraction-hints` | Recompute hints |
| `proposeProactivity(proposal)` | `bridge:propose-proactivity` | Propose proactive action |
| `arbitrateProactivity()` | `bridge:arbitrate-proactivity` | Arbitrate proposals |
| `getProactivityCooldown()` | `bridge:get-proactivity-cooldown` | Get cooldown state |
| `getPendingProposals()` | `bridge:get-pending-proposals` | Get pending proposals |
| `recordExchange(flattery, urgency, opts)` | `bridge:record-exchange` | Record exchange signals |
| `getManipulationMetrics()` | `bridge:get-manipulation-metrics` | Get manipulation metrics |
| `getPromptContext()` | `bridge:get-prompt-context` | Get prompt block |
| `getState()` | `bridge:get-state` | Get bridge state |
| `getConfig()` | `bridge:get-config` | Get config |
| `getRelevanceWeights()` | `bridge:get-relevance-weights` | Get relevance weights |
| `syncMemoryToPersonality()` | `bridge:sync-memory-to-personality` | Sync memory to personality |
| `updateConfig(updates)` | `bridge:update-config` | Update config |
| `reset()` | `bridge:reset` | Reset bridge state |

### `eve.memoryQuality` (13 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `assessExtraction(results)` | `memquality:assess-extraction` | Assess extraction quality |
| `assessRetrieval(results)` | `memquality:assess-retrieval` | Assess retrieval quality |
| `assessConsolidation(results)` | `memquality:assess-consolidation` | Assess consolidation quality |
| `assessPersonMentions(results)` | `memquality:assess-person-mentions` | Assess person mention quality |
| `buildReport(ext, ret, con)` | `memquality:build-report` | Build quality report |
| `getExtractionBenchmarks()` | `memquality:get-extraction-benchmarks` | Get extraction benchmarks |
| `getRetrievalBenchmarks()` | `memquality:get-retrieval-benchmarks` | Get retrieval benchmarks |
| `getConsolidationBenchmarks()` | `memquality:get-consolidation-benchmarks` | Get consolidation benchmarks |
| `getLatestReport()` | `memquality:get-latest-report` | Get latest quality report |
| `getQualityHistory()` | `memquality:get-quality-history` | Get quality history |
| `getQualityTrend(count?)` | `memquality:get-quality-trend` | Get quality trend |
| `getConfig()` | `memquality:get-config` | Get config |
| `updateConfig(partial)` | `memquality:update-config` | Update config |
| `getPromptContext()` | `memquality:get-prompt-context` | Get prompt block |

### `eve.sentiment` (3 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `analyse(text)` | `sentiment:analyse` | Analyse text sentiment |
| `getState()` | `sentiment:get-state` | Get current mood state |
| `getMoodLog()` | `sentiment:get-mood-log` | Get mood history |
| `onMoodChange(cb)` | `sentiment:mood-change` | **Event**: mood changed |

### `eve.evolution` (2 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `evolution:get-state` | Get evolution state |
| `incrementSession()` | `evolution:increment-session` | Increment session count |

### `eve.desktopEvolution` (3 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getIndex()` | `desktop-evolution:get-index` | Get current evolution index |
| `setIndex(index)` | `desktop-evolution:set-index` | Set evolution index |
| `getTransitionState()` | `desktop-evolution:get-transition` | Get transition blend state |

### `eve.artEvolution` (4 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `art-evolution:get-state` | Get art evolution state |
| `getLatest()` | `art-evolution:get-latest` | Get latest art piece |
| `check()` | `art-evolution:check` | Check if new art needed |
| `force()` | `art-evolution:force` | Force art generation |

---

## 12. Ecosystem & Connectors

### `eve.superpowers` (18 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `list()` | `superpowers:list` | List installed superpowers |
| `get(id)` | `superpowers:get` | Get superpower details |
| `toggle(id, enabled)` | `superpowers:toggle` | Enable/disable superpower |
| `toggleTool(spId, tool, enabled)` | `superpowers:toggle-tool` | Toggle individual tool |
| `updatePermissions(id, perms)` | `superpowers:update-permissions` | Update permissions |
| `install(repoUrl)` | `superpowers:install` | Install from repo URL |
| `uninstall(id)` | `superpowers:uninstall` | Uninstall superpower |
| `uninstallPreview(id)` | `superpowers:uninstall-preview` | Preview uninstall impact |
| `usageStats(id)` | `superpowers:usage-stats` | Get usage statistics |
| `enabledTools()` | `superpowers:enabled-tools` | List enabled tools |
| `flush()` | `superpowers:flush` | Flush superpower cache |
| `storeList()` | `superpowers:store-list` | List store packages |
| `storeGet(id)` | `superpowers:store-get` | Get store package |
| `storeConfirm(id, token)` | `superpowers:store-confirm` | Confirm store install |
| `storeEnabledTools()` | `superpowers:store-enabled-tools` | List store-enabled tools |
| `storeStatus()` | `superpowers:store-status` | Get store status |
| `storePromptContext()` | `superpowers:store-prompt-context` | Get store prompt block |
| `storeNeedsAttention()` | `superpowers:store-needs-attention` | Check if store needs attention |

### `eve.capabilityGaps` (13 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `record(taskDesc)` | `capability-gaps:record` | Record capability gap |
| `top(limit?)` | `capability-gaps:top` | Get top gaps |
| `get(gapId)` | `capability-gaps:get` | Get gap by ID |
| `generateProposals()` | `capability-gaps:generate-proposals` | Generate fix proposals |
| `pendingProposals()` | `capability-gaps:pending-proposals` | Get pending proposals |
| `acceptedProposals()` | `capability-gaps:accepted-proposals` | Get accepted proposals |
| `getProposal(id)` | `capability-gaps:get-proposal` | Get proposal |
| `present(id)` | `capability-gaps:present` | Present proposal to user |
| `accept(id)` | `capability-gaps:accept` | Accept proposal |
| `decline(id)` | `capability-gaps:decline` | Decline proposal |
| `markInstalled(id)` | `capability-gaps:mark-installed` | Mark as installed |
| `promptContext()` | `capability-gaps:prompt-context` | Get prompt block |
| `status()` | `capability-gaps:status` | Get module status |
| `prune()` | `capability-gaps:prune` | Prune old gaps |

### `eve.ecosystem` (21 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `createManifest(opts)` | `ecosystem:create-manifest` | Create package manifest |
| `validateManifest(manifest)` | `ecosystem:validate-manifest` | Validate manifest |
| `getDeveloperKeys()` | `ecosystem:get-developer-keys` | Get developer keys |
| `hasDeveloperKeys()` | `ecosystem:has-developer-keys` | Check for developer keys |
| `signPackage(manifest)` | `ecosystem:sign-package` | Sign package |
| `publishPackage(pkg)` | `ecosystem:publish-package` | Publish package |
| `getPublishedPackages()` | `ecosystem:get-published-packages` | List published packages |
| `getPublishedPackage(id)` | `ecosystem:get-published-package` | Get published package |
| `unpublishPackage(id)` | `ecosystem:unpublish-package` | Unpublish package |
| `searchRegistry(query)` | `ecosystem:search-registry` | Search registry |
| `getRegistryListing(id)` | `ecosystem:get-registry-listing` | Get registry listing |
| `searchForCapability(desc, keywords)` | `ecosystem:search-for-capability` | Search by capability |
| `initiatePurchase(id, amount, type?)` | `ecosystem:initiate-purchase` | Initiate purchase |
| `approvePurchase(txId, token)` | `ecosystem:approve-purchase` | Approve purchase |
| `cancelPurchase(txId)` | `ecosystem:cancel-purchase` | Cancel purchase |
| `executePurchase(txId)` | `ecosystem:execute-purchase` | Execute purchase |
| `getTransactions()` | `ecosystem:get-transactions` | List transactions |
| `getTransactionsForPackage(id)` | `ecosystem:get-transactions-for-package` | List transactions for package |
| `getTransaction(txId)` | `ecosystem:get-transaction` | Get transaction |
| `isPurchased(id)` | `ecosystem:is-purchased` | Check if purchased |
| `getStats()` | `ecosystem:get-stats` | Get ecosystem stats |
| `getConfig()` | `ecosystem:get-config` | Get config |
| `updateConfig(partial)` | `ecosystem:update-config` | Update config |
| `getPromptContext()` | `ecosystem:get-prompt-context` | Get prompt block |

### `eve.connectors` (5 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `listTools()` | `connectors:list-tools` | List connector tools |
| `callTool(name, args)` | `connectors:call-tool` | Call connector tool |
| `isConnectorTool(name)` | `connectors:is-connector-tool` | Check if tool is connector |
| `status()` | `connectors:status` | Get connector status |
| `getToolRouting()` | `connectors:get-tool-routing` | Get tool routing map |

### `eve.soc` (6 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `listTools()` | `soc:list-tools` | List SOC tools |
| `callTool(name, args)` | `soc:call-tool` | Call SOC tool |
| `checkDeps()` | `soc:check-deps` | Check SOC dependencies |
| `startBridge()` | `soc:start-bridge` | Start SOC bridge |
| `stopBridge()` | `soc:stop-bridge` | Stop SOC bridge |
| `bridgeStatus()` | `soc:bridge-status` | Get bridge status |

### `eve.gitLoader` (10 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `load(repoUrl, opts?)` | `git:load` | Load git repository |
| `getTree(repoId)` | `git:get-tree` | Get repo file tree |
| `getFile(repoId, path)` | `git:get-file` | Get file from repo |
| `search(repoId, query, opts?)` | `git:search` | Search repo |
| `getReadme(repoId)` | `git:get-readme` | Get README |
| `getSummary(repoId)` | `git:get-summary` | Get repo summary |
| `listLoaded()` | `git:list-loaded` | List loaded repos |
| `unload(repoId)` | `git:unload` | Unload repo |
| `listTools()` | `git:list-tools` | List git tools |
| `callTool(name, args)` | `git:call-tool` | Call git tool |

### `eve.gateway` (7 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `getStatus()` | `gateway:get-status` | Get gateway status |
| `setEnabled(enabled)` | `gateway:set-enabled` | Enable/disable gateway |
| `getPendingPairings()` | `gateway:get-pending-pairings` | Get pending pairing requests |
| `getPairedIdentities()` | `gateway:get-paired-identities` | Get paired identities |
| `approvePairing(code, tier?)` | `gateway:approve-pairing` | Approve pairing |
| `revokePairing(identityId)` | `gateway:revoke-pairing` | Revoke pairing |
| `getActiveSessions()` | `gateway:get-active-sessions` | Get active sessions |

### `eve.office` (3 methods + 7 events)

| Method | IPC Channel | Description |
|---|---|---|
| `getState()` | `office:get-state` | Get office state |
| `isOpen()` | `office:is-open` | Check if office open |
| `requestOpen()` | `office:request-open` | Request open office (fire-and-forget `send`) |
| `requestClose()` | `office:request-close` | Request close office (fire-and-forget `send`) |
| `onFullState(cb)` | `office:full-state` | **Event**: full state update |
| `onAgentSpawned(cb)` | `office:agent-spawned` | **Event**: agent spawned |
| `onAgentThought(cb)` | `office:agent-thought` | **Event**: agent thought bubble |
| `onAgentPhase(cb)` | `office:agent-phase` | **Event**: agent phase change |
| `onAgentCompleted(cb)` | `office:agent-completed` | **Event**: agent completed |
| `onAgentStopped(cb)` | `office:agent-stopped` | **Event**: agent stopped |
| `onAgentRemoved(cb)` | `office:agent-removed` | **Event**: agent removed |

### `eve.selfImprove` (3 methods + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `readFile(path)` | `self-improve:read-file` | Read source file |
| `listFiles(dir)` | `self-improve:list-files` | List source files |
| `proposeChange(path, content, desc)` | `self-improve:propose` | Propose code change |
| `respondToProposal(id, approved)` | `self-improve:respond` | Respond to proposal |
| `onProposal(cb)` | `self-improve:propose` | **Event**: new proposal |

### `eve.predictor` (1 method + 1 event)

| Method | IPC Channel | Description |
|---|---|---|
| `recordInteraction()` | `predictor:record-interaction` | Record interaction |
| `onSuggestion(cb)` | `predictor:suggestion` | **Event**: predictive suggestion |

### `eve.persistence` (14 methods)

| Method | IPC Channel | Description |
|---|---|---|
| `exportState(passphrase, path?)` | `persistence:export-state` | Export full state backup |
| `exportIncremental(passphrase, path?)` | `persistence:export-incremental` | Export incremental backup |
| `importState(archivePath, passphrase)` | `persistence:import-state` | Import state from backup |
| `validateArchive(path, passphrase)` | `persistence:validate-archive` | Validate backup archive |
| `setAutoPassphrase(passphrase)` | `persistence:set-auto-passphrase` | Set auto-backup passphrase |
| `clearAutoPassphrase()` | `persistence:clear-auto-passphrase` | Clear auto-backup passphrase |
| `runScheduledBackup()` | `persistence:run-scheduled-backup` | Run scheduled backup now |
| `getStateFiles()` | `persistence:get-state-files` | List state files |
| `enumerateState()` | `persistence:enumerate-state` | Enumerate all state |
| `getBackupHistory()` | `persistence:get-backup-history` | Get backup history |
| `getLastBackup()` | `persistence:get-last-backup` | Get last backup info |
| `getConfig()` | `persistence:get-config` | Get persistence config |
| `updateConfig(partial)` | `persistence:update-config` | Update config |
| `getPromptContext()` | `persistence:get-prompt-context` | Get prompt block |
| `checkContinuity()` | `persistence:check-continuity` | Check state continuity |
