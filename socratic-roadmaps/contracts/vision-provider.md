## Interface Contract: VisionProvider
**Sprint:** 5, Phase M.1
**Source:** src/main/vision/vision-provider.ts (to be created)

### Exports
- `visionProvider` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| loadModel(name?) | `(name?: string): Promise<void>` | Load vision model via Ollama |
| unloadModel() | `(): void` | Free VRAM |
| describe(image) | `(image: ImageInput): Promise<string>` | Image → description |
| answer(image, question) | `(image: ImageInput, question: string): Promise<string>` | Visual QA |
| isReady() | `(): boolean` | Model loaded |
| getModelInfo() | `(): VisionModelInfo` | Model metadata |

### Types
```typescript
type ImageInput = Buffer | string;  // Buffer (raw PNG/JPEG) or file path

interface VisionModelInfo {
  name: string;
  vramUsageMB: number;
  loaded: boolean;
}
```

### Dependencies
- Requires: Ollama running with vision model (e.g., moondream:latest)
- Required by: ScreenContext (M.2), ImageUnderstanding (M.3), VisionCircle (N.1)
