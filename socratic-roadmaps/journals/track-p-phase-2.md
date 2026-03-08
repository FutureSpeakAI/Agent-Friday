# Track P -- Phase 2: "The Identity" -- ProfileManager

**Date:** 2026-03-08
**Phase:** P.2
**Status:** Complete

## What Was Built

ProfileManager singleton at `src/main/setup/profile-manager.ts` (~140 lines).

Manages user profiles with:
- UUID-based identity (via `node:crypto.randomUUID`)
- Soft delete (preserves ID for referential integrity)
- Export/import via JSON (new IDs and timestamps on import)
- Extensible preferences (unknown keys preserved on update)
- File-based persistence to `{userData}/profiles.json`
- Event system with `on()` returning unsubscribe function

## Key Design Decisions

### Persistence: File-Based, Not Settings

The existing `settingsManager.setSetting()` rejects unknown keys (`if (!(key in this.settings)) return;`). The `VoiceProfileManager` actually has a bug -- it calls `settingsManager.setSetting('voice.profiles', ...)` which silently fails. We followed the `SetupWizard` pattern instead: direct file I/O to `profiles.json` in userData.

### Event Pattern: Map<string, Set<callback>>

Used `Set` instead of arrays (like SetupWizard uses) for O(1) unsubscribe via `delete()`. The `on()` method returns an unsubscribe function matching the contract spec.

### First-Profile Auto-Active

When `createProfile()` is called and the resulting profile is the only non-deleted profile, it automatically becomes active. This handles both first-run and the edge case where all profiles were deleted before a new one is created.

### Soft Delete with Active Switching

`deleteProfile()` marks `deleted: true` on the profile, then checks if the deleted profile was active. If so, it switches to the next available non-deleted profile, or sets active to null if none remain.

## Tests (10/10 passing)

1. createProfile returns profile with generated ID
2. getActiveProfile returns null when no profiles exist
3. setActiveProfile sets the active profile
4. First createProfile automatically becomes active
5. updateProfile merges new data into existing profile
6. deleteProfile soft-deletes; switches active if deleted was active
7. exportProfile returns complete profile JSON including preferences
8. importProfile creates new profile from exported JSON
9. Profile includes required shape: name, createdAt, tierOverride, voiceProfileId, theme
10. Profile changes emit profile-changed event

## Safety Gate

- `npx tsc --noEmit` -- clean, no errors
- `npx vitest run` -- 10/10 tests passing

## Files Changed

- `src/main/setup/profile-manager.ts` (new) -- ProfileManager implementation
- `tests/sprint-6/setup/profile-manager.test.ts` (new) -- 10 tests
- `socratic-roadmaps/journals/track-p-phase-2.md` (new) -- this journal
