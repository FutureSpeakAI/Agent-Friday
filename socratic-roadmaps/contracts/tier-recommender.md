# Contract: TierRecommender

## Module
`src/main/hardware/tier-recommender.ts`

## Pure Functions (No Singleton)
All functions are stateless — pure input → output.

## Types

```typescript
type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

interface TierRecommendation {
  tier: TierName;
  models: ModelRequirement[];
  totalVRAM: number;        // bytes needed for all models
  totalDisk: number;        // bytes needed for all downloads
  diskSufficient: boolean;
  vramHeadroom: number;     // bytes remaining after models
  upgradePath: UpgradePath | null;
  warnings: string[];       // e.g., "Low disk space"
}

interface ModelRequirement {
  name: string;              // e.g., "nomic-embed-text"
  vramBytes: number;
  diskBytes: number;
  purpose: string;           // e.g., "embeddings"
  required: boolean;         // false = optional for this tier
}

interface UpgradePath {
  nextTier: TierName;
  requiredVRAM: number;
  requiredDisk: number;
  unlocks: string[];         // e.g., ["Local 8B LLM", "Offline chat"]
}
```

## Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `recommend` | `(profile: HardwareProfile) → TierRecommendation` | Full recommendation |
| `getTier` | `(profile: HardwareProfile) → TierName` | Just the tier name |
| `getModelList` | `(tier: TierName) → ModelRequirement[]` | Models for a tier |
| `estimateVRAMUsage` | `(models: ModelRequirement[]) → number` | Sum VRAM |
| `canFitModel` | `(model: ModelRequirement, profile: HardwareProfile, loaded: ModelRequirement[]) → boolean` | Check fit |
| `getUpgradePath` | `(tier: TierName) → UpgradePath \| null` | Next tier info |

## Tier Thresholds (Effective VRAM)
| Tier | VRAM | Models |
|------|------|--------|
| whisper | 0 GB | None (CPU Whisper, cloud LLM) |
| light | 4 GB | nomic-embed-text (0.5GB) |
| standard | 8 GB | embed + llama3.1:8b-q4 (5.5GB) |
| full | 12 GB | embed + 8B LLM + moondream (1.2GB) |
| sovereign | 24+ GB | All + larger models |

## Boundary
- Pure functions — no state, no side effects
- Model sizes hardcoded as data table
- Disk space check included in recommendation
- System VRAM reservation (~1.5GB) already subtracted in HardwareProfile
