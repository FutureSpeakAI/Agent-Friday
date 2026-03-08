# Track M Phase 1: The Gaze -- VisionProvider

**Date:** 2026-03-08
**Sprint:** 5
**Track:** M (Vision)
**Phase:** M.1

## What Was Built

VisionProvider singleton module that enables Agent Friday to see.
Takes image input (Buffer, file path, or base64 string) and produces
natural language descriptions or answers visual questions using a
vision-language model (default: moondream:latest) running locally via
Ollama.

### Files Created

- **Implementation:** src/main/vision/vision-provider.ts (~160 lines)
- **Tests:** tests/sprint-5/vision/vision-provider.test.ts (10 tests)

### VisionProvider Architecture



### Public API

| Method | Description |
|--------|-------------|
| loadModel(name?) | Verify model in Ollama via /api/show, track VRAM via /api/ps |
| unloadModel() | Free VRAM, isReady becomes false |
| describe(image) | Image to natural language description |
| answer(image, question) | Visual question answering |
| isReady() | Whether model is loaded |
| getModelInfo() | Model name, VRAM usage, loaded state |
| getInstance() / resetInstance() | Singleton lifecycle |

### Architecture Decisions

- **Same Ollama HTTP pattern as voice pipeline**: Uses the standard
  fetch-based POST to /api/generate with images array, consistent with
  the OllamaProvider pattern established in earlier sprints.

- **Three-way image input handling**: Buffer (direct base64 conversion),
  file path (fs.readFile then base64), or raw base64 string. Path
  detection uses heuristic: starts with / or drive letter, or contains
  backslash.

- **VRAM tracking via /api/ps**: After loading, queries the Ollama
  process list for actual VRAM usage. Falls back to 1200 MB default
  estimate for moondream Q4 if unavailable.

- **Graceful error handling**: Missing models throw with descriptive
  message. Malformed images propagate Ollama errors. The provider
  remains ready after image errors (model is not crashed).

- **No streaming**: Uses stream: false for simplicity. Vision responses
  are typically short (one paragraph) so streaming adds no benefit.

### Validation Results

All 10 tests pass:
1. loadModel() succeeds when Moondream available in Ollama
2. loadModel() returns graceful error when model missing
3. describe(image) returns text description for valid image
4. answer(image, question) returns answer to visual question
5. isReady() false before load, true after
6. Image input accepts Buffer, base64 string, and file paths
7. unloadModel() frees VRAM, isReady() returns false
8. getModelInfo() reports VRAM usage (~1.2GB for Moondream Q4)
9. Provider handles malformed/corrupt images gracefully
10. Singleton pattern works correctly (getInstance + resetInstance)

### Safety Gate

- npx tsc --noEmit: 0 errors
- npx vitest run: 113 test files, 4175 tests passed, 0 failures
