## Interface Contract: ImageUnderstanding
**Sprint:** 5, Phase M.3
**Source:** src/main/vision/image-understanding.ts (to be created)

### Exports
- `imageUnderstanding` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| processImage(source) | `(source: Buffer \| string): Promise<ImageResult>` | Analyze image |
| processClipboardImage() | `(): Promise<ImageResult \| null>` | From clipboard |
| handleDrop(files) | `(files: string[]): Promise<ImageResult \| null>` | From drag-drop |
| handleFileSelect() | `(): Promise<ImageResult \| null>` | From file picker |
| getLastResult() | `(): ImageResult \| null` | Cached last result |
| on(event, cb) | `(event: string, cb: Function): () => void` | Subscribe |

### Types
```typescript
interface ImageResult {
  description: string;
  source: 'clipboard' | 'file' | 'drop' | 'screen' | 'buffer';
  timestamp: number;
  imageSizeBytes: number;
}
```

### Events
- `image-result` — New image understanding ready (payload: `ImageResult`)

### IPC Channels
- `vision:process-clipboard` — Renderer requests clipboard image processing
- `vision:file-dropped` — Renderer reports image file dropped
- `vision:select-file` — Renderer requests file picker

### Dependencies
- Requires: VisionProvider (M.1)
- Required by: VisionCircle (N.1)
