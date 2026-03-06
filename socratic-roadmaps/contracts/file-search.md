## Interface Contract: File Search
**Generated:** 2026-03-06
**Source:** src/main/file-search.ts (367 lines)

### Exports
- `fileSearch` — singleton instance of `FileSearch`
- `FileSearchResult` — interface: { path, name, size, modified, type, preview? }
- `FileSearchQuery` — interface: { query, path?, extensions?, maxResults?, sortBy? }

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| search(query) | `(query: FileSearchQuery): Promise<FileSearchResult[]>` | Windows Search via PowerShell |
| getRecentFiles(limit?) | `(limit?: number): Promise<FileSearchResult[]>` | Recently modified files |
| getIndexStatus() | `(): Promise<{ indexed: boolean, path: string }>` | Check if Windows Search indexing is active |

### IPC Channels
| Channel | Request | Response |
|---------|---------|----------|
| file-search:search | `FileSearchQuery` | `FileSearchResult[]` |
| file-search:recent | `{ limit? }` | `FileSearchResult[]` |

### Tool Registry Shape (for Track B.1)
```typescript
{
  name: 'file_search',
  description: 'Search for files on the local filesystem by name, content, or type',
  input_schema: {
    type: 'object',
    properties: {
      query: { type: 'string', description: 'Search query' },
      extensions: { type: 'array', items: { type: 'string' } },
      maxResults: { type: 'number', default: 20 }
    },
    required: ['query']
  },
  safetyLevel: 'read-only'
}
```

### Dependencies
- Requires: child_process (PowerShell execution), settings (paths)
- Required by: tool-registry (Track B.1)
