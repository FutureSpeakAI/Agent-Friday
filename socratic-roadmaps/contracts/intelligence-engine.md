## Interface Contract: Intelligence Engine
**Generated:** 2026-03-06
**Source:** src/main/intelligence.ts (238 lines)

### Exports
- `intelligenceEngine` — singleton instance of `IntelligenceEngine`
- `Briefing` — interface: { id, topic, content, createdAt, delivered, priority }
  - priority: 'high' | 'medium' | 'low'

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| initialize() | `(): Promise<void>` | Load briefings from disk |
| runResearch(topic, priority?) | `(topic: string, priority?: 'high'\|'medium'\|'low'): Promise<void>` | Run Claude research, store result |
| getUndeliveredBriefings() | `(): Promise<Briefing[]>` | Get + mark as delivered, sorted priority→recency |
| getBriefingSummary() | `(): Promise<string>` | Formatted string for LLM context injection |
| setupResearchTopics(topics) | `(topics: ResearchTopic[]): Promise<void>` | Configure scheduled research |

### Behavior Notes
- Briefings are stored in `{userData}/briefings.json`
- Max 20 briefings retained; older than 24h are pruned
- `runResearch()` calls `llmClient.text()` with user profile context
- `getUndeliveredBriefings()` marks briefings as delivered (side effect)

### Dependencies
- Requires: llm-client (for text generation), memory (for user profile), scheduler (for periodic research)
- Required by: briefing-pipeline (Track A), briefing-delivery (Track A)
