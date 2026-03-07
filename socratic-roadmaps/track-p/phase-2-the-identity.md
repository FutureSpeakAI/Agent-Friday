# Phase P.2 — The Identity
## ProfileManager: User Profiles and Configuration

### Hermeneutic Focus
*The system is born. Now it must know who it serves. ProfileManager creates the concept of identity — user preferences, conversation history ownership, and configuration that persists across sessions. The machine remembers who you are.*

### Current State (Post-P.1)
- SetupWizard completes first-run flow
- Hardware tier selected and models downloaded
- Settings system exists but has no profile concept
- VoiceProfileManager (S4) manages voice preferences
- No user profile abstraction exists

### Architecture Context
```
ProfileManager (this phase)
├── createProfile(opts)      — Create a new user profile
├── getProfile(id)           — Retrieve profile by ID
├── getActiveProfile()       — Currently active profile
├── setActiveProfile(id)     — Switch active profile
├── updateProfile(id, data)  — Update profile settings
├── deleteProfile(id)        — Remove a profile
├── exportProfile(id)        — Export profile as JSON
├── importProfile(json)      — Import profile from JSON
└── listProfiles()           — List all profiles
```

### Validation Criteria (Test-First)
1. `createProfile({ name: 'User' })` returns profile with generated ID
2. `getActiveProfile()` returns null when no profiles exist
3. `setActiveProfile(id)` sets the active profile
4. First `createProfile()` automatically becomes active
5. `updateProfile()` merges new data into existing profile
6. `deleteProfile()` removes profile; switches active if deleted was active
7. `exportProfile()` returns complete profile JSON including preferences
8. `importProfile()` creates new profile from exported JSON
9. Profile includes: name, createdAt, tier override, voice preferences, theme
10. Profile changes emit `profile-changed` event for other modules to react

### Socratic Inquiry

**Boundary:** *Is a profile required or optional?*
Optional but encouraged. Without a profile, the system uses defaults. Setup wizard (P.1) can create a profile or skip. Single-user mode is just one implicit default profile.

**Inversion:** *What if profiles are deleted but conversations reference them?*
Conversations reference profile IDs. Deleted profile → conversations become "unowned" but remain accessible. Never cascade-delete conversations. Profile deletion is soft — mark deleted, preserve ID for references.

**Constraint Discovery:** *How does ProfileManager interact with VoiceProfileManager?*
ProfileManager owns the user identity. VoiceProfileManager owns voice settings. A user profile references a voice profile ID. When active profile changes, emit event so VoiceProfileManager can load the right voice settings.

**Precedent:** *How does the existing settings system store data?*
`electron-store` with JSON backing. Profiles stored as `settings.get('profiles')` → `Record<string, UserProfile>`. Active profile: `settings.get('activeProfileId')`.

**Synthesis:** *How does the profile feed into the LLM?*
Profile name and preferences can be included in the system prompt: "You are speaking with {name}. They prefer {preferences}." This is a downstream concern for the intelligence router, not ProfileManager — but the data must be available.

### Boundary Constraints
- Creates `src/main/setup/profile-manager.ts` (~120-150 lines)
- Creates `tests/sprint-6/setup/profile-manager.test.ts`
- Does NOT create profile UI (thin IPC layer concern)
- Does NOT manage conversations (separate module)
- Persists via existing settings system
- Profile schema is extensible — unknown keys preserved on update

### Files to Read
1. `src/main/setup/setup-wizard.ts` — Integration point
2. `src/main/settings.ts` — Persistence layer
3. `src/main/voice/voice-profile-manager.ts` — Voice preference pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-p-phase-2.md` before closing.
