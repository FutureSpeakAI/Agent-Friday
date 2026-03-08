# Track M Phase 3: The Focus -- ImageUnderstanding

**Date:** 2026-03-08
**Sprint:** 5
**Track:** M (Vision)
**Phase:** M.3

## What Was Built

ImageUnderstanding singleton module that enables users to provide images to
Agent Friday via clipboard paste, drag-drop, or native file picker. Validates
image format (PNG, JPEG, WebP, GIF) and enforces a 10MB size limit. Delegates
to VisionProvider for natural language description or visual question answering.
Caches the last result and emits 'image-result' events with source type.

### Files Created

- **Implementation:** src/main/vision/image-understanding.ts (~140 lines)
- **Tests:** tests/sprint-5/vision/image-understanding.test.ts (10 tests)

### Public API

| Method | Description |
|--------|-------------|
| processImage(source, question?) | Analyze image Buffer or file path via VisionProvider |
| processClipboardImage() | Read PNG/JPEG from system clipboard, process it |
| handleDrop(files) | Filter for image files, process first valid one |
| handleFileSelect() | Open native file picker filtered to images |
| getLastResult() | Return cached last ImageResult |
| on(event, cb) | Subscribe to events, returns unsubscribe function |
| getInstance() / resetInstance() | Singleton lifecycle |

### ImageResult Type

```typescript
interface ImageResult {
  description: string;
  source: 'clipboard' | 'file' | 'drop' | 'screen' | 'buffer';
  timestamp: number;
  imageSizeBytes: number;
}
```

### Events

| Event | Payload | Fires When |
|-------|---------|------------|
| image-result | ImageResult | Any image is successfully processed |

### IPC Channels

- vision:process-clipboard
- vision:file-dropped
- vision:select-file

### Architecture Decisions

- **Dual input paths (Buffer vs file path)**: processImage() detects input
  type via Buffer.isBuffer(). Buffers get source type 'buffer'; file paths
  get 'file'. File paths are validated for extension before reading.

- **Extension-based format validation**: Uses a Set of supported extensions
  (png, jpg, jpeg, webp, gif). File paths with unsupported extensions are
  rejected with a descriptive error before any I/O occurs.

- **10MB size enforcement**: For Buffer input, checks Buffer.byteLength.
  For file paths, uses fs.stat() to check size before reading. Both paths
  throw a descriptive error mentioning the 10MB limit.

- **VisionProvider delegation**: Uses VisionProvider.getInstance() to get
  the singleton. If question parameter is provided, calls answer(image,
  question) for visual QA. Otherwise calls describe(image) for general
  description.

- **Clipboard via Electron NativeImage**: processClipboardImage() uses
  clipboard.readImage() which returns a NativeImage. Checks isEmpty()
  to handle empty clipboard. Converts to PNG buffer via toPNG().

- **Drag-drop filtering**: handleDrop() receives an array of file paths,
  filters by image extension using Array.find(), and processes only the
  first valid image. Non-image files are silently skipped.

- **Native file picker**: handleFileSelect() uses dialog.showOpenDialog()
  with image extension filters. Returns null if the user cancels. Routes
  selected file through processImage() for consistent handling.

- **Result caching**: Stores last ImageResult in a private field. All
  public methods that produce results update the cache. getLastResult()
  returns it without triggering any processing.

- **Same event emitter pattern**: Uses Map<event, Set<callback>> with
  unsubscribe-returning on() method, consistent with ScreenContext,
  AudioCapture, and other modules in the project.

- **No VisionProvider modifications**: ImageUnderstanding consumes the
  existing VisionProvider API (describe, answer, isReady) without any
  changes to the provider.

### Validation Results

All 10 tests pass:
1. processImage(buffer) sends image to VisionProvider, returns description
2. processImage(filePath) reads file then processes
3. processClipboardImage() reads PNG/JPEG from system clipboard
4. handleDrop(files) filters for image files, processes first valid one
5. handleFileSelect() opens native file picker filtered to images
6. Supported formats: PNG, JPEG, WebP, GIF (first frame)
7. Images > 10MB are rejected with an informative error
8. getLastResult() returns cached result for re-reference
9. image-result event includes source type (clipboard/drop/file/buffer)
10. All tests mock clipboard, file system, and VisionProvider

### Safety Gate

- npx tsc --noEmit: 0 errors
- npx vitest run: 115 test files, 4195 tests passed, 0 failures
