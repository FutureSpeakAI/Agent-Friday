## Interface Contract: ScreenContext
**Sprint:** 5, Phase M.2
**Source:** src/main/vision/screen-context.ts (to be created)

### Exports
- `screenContext` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| captureScreen() | `(): Promise<Buffer \| null>` | Full display screenshot |
| captureWindow(id?) | `(windowId?: number): Promise<Buffer \| null>` | Specific window |
| captureRegion(rect) | `(rect: Rectangle): Promise<Buffer \| null>` | Screen region |
| getContext() | `(): string \| null` | Latest screen description |
| startAutoCapture(interval?) | `(ms?: number): void` | Periodic capture (default 30s) |
| stopAutoCapture() | `(): void` | Stop periodic |
| on(event, cb) | `(event: string, cb: Function): () => void` | Subscribe |

### Events
- `context-update` — New screen description (payload: `string`)

### Dependencies
- Requires: VisionProvider (M.1), Electron desktopCapturer
- Required by: VisionCircle (N.1)
