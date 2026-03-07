## Interface Contract: VisionCircle
**Sprint:** 5, Phase N.1
**Source:** tests/sprint-5/integration/vision-circle.test.ts (to be created)

### Integration Flow
```
Image Source → VisionProvider.describe() → text description
Text description → LLM system/user context → LLM response
Screen context → periodic background → LLM system prompt enrichment
```

### Verified Behaviors
1. Screen context flows to LLM as system prompt enrichment
2. User image → description → LLM user message
3. Vision model loads on-demand, unloads after timeout
4. VRAM tracked correctly with vision loaded
5. Graceful degradation at every node
6. No interference with voice circle or intelligence circle

### Dependencies
- Requires: VisionProvider (M.1), ScreenContext (M.2), ImageUnderstanding (M.3), LLM pipeline
- Required by: Sprint 5 completion gate
