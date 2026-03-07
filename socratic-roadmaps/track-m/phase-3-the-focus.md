# Phase M.3 — The Focus
## ImageUnderstanding: User Image Input Pipeline

### Hermeneutic Focus
*The system can see its own screen, but users have no way to show it something specific. This phase adds intentional visual input — clipboard paste, file drag-drop, and file picker. The focus is active, directed perception — the user points and the system looks.*

### Current State (Post-M.2)
- VisionProvider describes images and answers visual questions
- ScreenContext captures and analyzes the screen
- No way for users to provide images to the system
- Electron supports clipboard, drag-drop, and file dialog APIs

### Architecture Context
```
ImageUnderstanding (this phase)
├── processImage(source)      — Route image to VisionProvider
├── processClipboardImage()   — Read image from clipboard
├── handleDrop(files)         — Process drag-dropped image files
├── handleFileSelect()        — Open file picker for images
├── getLastResult()           — Most recent image analysis
└── on('image-result', cb)    — New image understanding ready
```

### Validation Criteria (Test-First)
1. `processImage(buffer)` sends image to VisionProvider, returns description
2. `processImage(filePath)` reads file then processes
3. `processClipboardImage()` reads PNG/JPEG from system clipboard
4. `handleDrop(files)` filters for image files, processes first valid one
5. `handleFileSelect()` opens native file picker filtered to images
6. Supported formats: PNG, JPEG, WebP, GIF (first frame)
7. Images > 10MB are rejected with an informative error
8. `getLastResult()` returns cached result for re-reference
9. `image-result` event includes source type (clipboard/drop/file/screen)
10. All tests mock clipboard, file system, and VisionProvider

### Socratic Inquiry

**Boundary:** *Does ImageUnderstanding generate the question or just the description?*
Both. Default behavior: generate a description ("I see a chart showing..."). If the user provides a question alongside the image ("What does this graph show?"), pass both image and question to VisionProvider.answer().

**Inversion:** *What if the clipboard contains text, not an image?*
`processClipboardImage()` checks `clipboard.readImage()`. If empty (no image), return null. Caller handles the "no image in clipboard" case. Never crash on unexpected clipboard content.

**Constraint Discovery:** *How do images flow into the LLM conversation?*
The LLM's ChatMessage type needs an `images` field or an image attachment mechanism. This may require extending the existing LLMRequest type from Sprint 2. Keep the extension minimal — add an optional `images: ImageAttachment[]` to LLMRequest.

**Precedent:** *How does the existing drag-drop work in the Electron app?*
The renderer handles drag-drop events. Files dropped onto the window fire events with file paths. ImageUnderstanding registers a handler in the renderer that forwards image file paths to the main process via IPC.

**Safety Gate:** *Could a malicious image exploit the VLM?*
Adversarial images can cause VLMs to hallucinate specific text. The LLM pipeline's ConfidenceAssessor (Sprint 3) evaluates output quality regardless of input source. Vision descriptions flow through the same confidence check as all LLM output.

### Boundary Constraints
- Creates `src/main/vision/image-understanding.ts` (~110-140 lines)
- Creates `tests/sprint-5/vision/image-understanding.test.ts`
- Does NOT modify VisionProvider or ScreenContext
- May extend LLMRequest type with optional `images` field
- IPC channels: `vision:process-clipboard`, `vision:file-dropped`, `vision:select-file`

### Files to Read
1. `src/main/vision/vision-provider.ts` — Image processing API
2. `src/main/llm-client.ts` — LLMRequest type for extension

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-m-phase-3.md` before closing.
