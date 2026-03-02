# The Socratic Forge — Agent Friday App Registry

*A guided inquiry into every unfinished application in the Agent Friday OS.*

Each app is explored through the Socratic method: we don't just spec features — we interrogate assumptions, reveal hidden dependencies, and find the essential question each app must answer before it can exist.

---

## Legend

| Status | Meaning |
|--------|---------|
| **Planned** | Concept only — no implementation yet |
| **Partial** | Backend exists, no dedicated UI |
| **Exists** | Functional, may need polish |

---

## 1. Friday Files — Smart File Browser with AI Search

**Status:** Planned | **Backend:** Native `fs` + semantic search

### The Essential Question
*What does a file browser become when it understands the contents of every file, not just the name?*

### Socratic Inquiry

**Q: Why would a user open Friday Files instead of Windows Explorer?**
Because Explorer searches by filename. Friday Files searches by meaning. "Find the contract I was working on with Sarah last week" should work — even if the file is named `draft-v3-FINAL2.docx`.

**Q: What backend already exists?**
- `document-ingestion.ts` — PDF/DOCX/image text extraction
- `semantic-search.ts` — Vector embedding search
- `pageindex/` — Page intelligence and indexing
- `context-stream.ts` — File system events already flow through the nervous system

**Q: What's the minimum viable version?**
A panel in the agent's UI that shows:
1. Recent files (from context stream ambient events)
2. A semantic search bar ("find files about...")
3. AI-generated file previews/summaries
4. Quick actions (open, move, rename, delete with confirmation)

**Q: What's the hard part?**
File system indexing at scale. The semantic search needs embeddings for every file. Do we index on startup? On file change? Background worker? How do we handle 100K+ files without eating RAM?

**Q: What's the deeper opportunity?**
Friday Files should know your *project context*. When you're working on a project, it surfaces the relevant files automatically. `project-awareness.ts` already detects git repos — extend to recognize project boundaries.

### First Steps
1. Create `src/renderer/components/FridayFiles.tsx` — file browser panel
2. Wire to `document-ingestion.ts` for content extraction
3. Wire to `semantic-search.ts` for meaning-based search
4. Subscribe to `context-stream` file events for "recent files"
5. Add AI summarization on file hover/preview

---

## 2. Friday Notes — Obsidian-Connected Markdown Editor

**Status:** Planned | **Backend:** `obsidian-memory.ts`

### The Essential Question
*Should the agent have its own notes, or should it operate on the human's notes?*

### Socratic Inquiry

**Q: What already exists?**
`obsidian-memory.ts` connects to the user's Obsidian vault. The agent can read and write markdown notes. But there's no dedicated editor UI — notes go through the chat.

**Q: Is this a text editor or an AI writing assistant?**
Neither alone. It's a *co-authoring surface*. The user writes, the agent suggests, edits, cross-references. The agent should be able to say "I noticed this note contradicts what you wrote in [other note] last week."

**Q: What makes this different from just using Obsidian?**
The agent's memory is woven into the notes. When you write a meeting note, Friday can auto-link it to the calendar event, the attendees' trust profiles, and the commitments made. The notes become a living knowledge graph, not a flat file system.

**Q: What's the hard part?**
Conflict resolution. If the user edits a note in Obsidian AND Friday edits it simultaneously, who wins? Need a merge strategy.

### First Steps
1. Create `src/renderer/components/FridayNotes.tsx` — markdown editor (use a library like `@uiw/react-md-editor`)
2. Wire to `obsidian-memory.ts` for vault read/write
3. Add AI sidebar: suggestions, cross-references, summarization
4. Implement file watcher for external changes (Obsidian edits)

---

## 3. Friday Calc — Natural Language + Traditional Calculator

**Status:** Planned | **Backend:** Gemini/Claude inline

### The Essential Question
*Is "calculate" even the right verb when you have an AI?*

### Socratic Inquiry

**Q: When would someone open a calculator inside an AI OS?**
When they want a quick, trustworthy answer to a math question. The key word is *trustworthy* — LLMs are notoriously bad at arithmetic. The calculator must be deterministic for numerical operations.

**Q: So it's a hybrid?**
Yes. Traditional calculator for arithmetic (deterministic, verifiable), LLM for interpretation ("what's 15% tip on a $87.50 dinner for 4 people, split unevenly where person A had the steak"). The LLM translates intent to math; the math engine executes.

**Q: What's the minimum viable version?**
A panel with:
1. Text input: "What's the compound interest on $50K at 4.5% over 10 years?"
2. The agent decomposes it into a formula
3. A deterministic math engine (not LLM) computes the result
4. The agent explains the answer in natural language

**Q: What library?**
`mathjs` — handles symbolic math, unit conversions, complex expressions. The agent generates `mathjs` expressions, not raw answers.

### First Steps
1. `npm install mathjs`
2. Create `src/renderer/components/FridayCalc.tsx` — calculator panel
3. Agent translates natural language → mathjs expression
4. Execute expression deterministically
5. Agent explains result

---

## 4. Friday Calendar — Google Cal + AI Scheduling

**Status:** Partial | **Backend:** `calendar.ts` + Metorial

### The Essential Question
*What does scheduling look like when your agent knows your energy levels, commitments, and relationships?*

### Socratic Inquiry

**Q: What already exists?**
`calendar.ts` has full Google Calendar OAuth2 read/write. Meeting prep is implemented. The agent can create, update, and delete events through tool calls.

**Q: What's missing?**
A dedicated calendar UI. Currently everything goes through chat ("schedule a meeting with Sarah"). A visual calendar view would let users see their schedule at a glance and interact directly.

**Q: What's the deeper opportunity?**
The agent knows your trust graph (who's reliable), your personality profile (are you an introvert who needs buffer time?), your commitments (are you overcommitted?), and your meeting history (this weekly standup always runs 15 minutes over). Smart scheduling means:
- Auto-decline meetings with people who have low reliability scores (with user approval)
- Buffer time between meetings based on personality profile
- Meeting prep briefings pulled from trust graph + memory
- "You've been in 6 hours of meetings today — I recommend declining this 7th one"

### First Steps
1. Create `src/renderer/components/FridayCalendar.tsx` — visual calendar (use `react-big-calendar` or similar)
2. Wire to existing `calendar.ts` backend
3. Add AI annotations: meeting prep, commitment warnings, energy management
4. Add drag-and-drop rescheduling

---

## 5. Friday Mail — Gmail with AI Triage + Compose

**Status:** Partial | **Backend:** `communications.ts` + Metorial

### The Essential Question
*How should an AI handle the most dangerous thing in your inbox — the email you should have responded to but didn't?*

### Socratic Inquiry

**Q: What exists?**
`communications.ts` handles email via Metorial/Gmail APIs. The agent can draft and send emails through tool calls. But there's no inbox UI.

**Q: What's the core insight?**
Email triage. The agent reads your inbox, scores each email by:
- Sender's trust score (from trust graph)
- Urgency signals (deadlines, keywords, emotional tone)
- Your commitment tracker (did you promise to respond?)
- Relationship context (how long since you last talked to this person?)

Then presents a priority-sorted inbox with draft responses.

**Q: What's the cLaw consideration?**
First Law: Never send an email that could harm the user. Auto-drafts must be reviewed. Auto-send should never be enabled by default. The agent should flag emails that seem risky ("this reply sounds frustrated — are you sure you want to send this?").

### First Steps
1. Create `src/renderer/components/FridayMail.tsx` — inbox panel
2. Wire to `communications.ts` for Gmail access
3. Implement triage scoring (trust graph + urgency + commitments)
4. Add AI compose with tone analysis
5. Draft review before send (cLaw: consent for irreversible actions)

---

## 6. Friday Messages — Unified Messaging

**Status:** Partial | **Backend:** `gateway/` + Metorial

### The Essential Question
*When you have a Telegram bot, SMS, email, and chat all in one place — how does the agent maintain context across channels?*

### Socratic Inquiry

**Q: What exists?**
The `gateway/` directory has a full gateway manager with a Telegram adapter. Trust engine validates inbound messages. Audit log tracks all gateway activity.

**Q: What's the unified inbox challenge?**
A single person might message you on Telegram, email you, and text you — all about the same topic. The agent needs to recognize this is one conversation thread across three channels. `relationship-memory.ts` + trust graph's fuzzy person resolution handles identity. But the UI needs to present it as one unified thread.

**Q: What's the privacy model?**
Messages from external channels enter through the gateway. The gateway applies the trust engine. Messages from untrusted senders get flagged. The agent should never auto-respond to unknown contacts.

### First Steps
1. Create `src/renderer/components/FridayMessages.tsx` — unified inbox
2. Wire to `gateway/gateway-manager.ts` for multi-channel messages
3. Cross-reference with `relationship-memory.ts` for person unification
4. Trust-tier-based UI (trusted senders prominent, unknown senders muted)

---

## 7. Friday Browser — AI-Assisted Web Browsing

**Status:** Partial | **Backend:** `browser.ts` + `pageindex/`

### The Essential Question
*What does browsing look like when the agent has already read the page before you scroll?*

### Socratic Inquiry

**Q: What exists?**
`browser.ts` controls a browser instance. `pageindex/` indexes web pages with semantic search. The agent can navigate, read page content, and extract information.

**Q: What's the AI-first browsing experience?**
- Page summaries before you read
- Key information extraction (prices, dates, contact info)
- "Read this article and give me the 3 key takeaways"
- Cross-page research: "Compare these 5 products across price, reviews, and shipping"
- Page intelligence persists in memory — "What was that article I read last week about quantum computing?"

### First Steps
1. Create `src/renderer/components/FridayBrowser.tsx` — embedded browser with AI sidebar
2. Wire to `browser.ts` for page navigation
3. Wire to `pageindex/` for semantic page intelligence
4. Add AI overlay: summaries, extraction, cross-reference

---

## 8. Friday Monitor — System Health + Resource Usage

**Status:** Planned | **Backend:** `system-management.ts`

### The Essential Question
*Should the agent proactively warn you about system problems, or wait until you ask?*

### Socratic Inquiry

**Q: What's the proactive agent behavior?**
"Your disk is 92% full. I notice you have 15GB of downloads from last month that you haven't opened. Want me to help clean up?" This requires personality calibration — too much nagging violates the user's autonomy; too little risks harm through inaction (First Law).

**Q: What metrics matter?**
CPU, RAM, disk, battery, network, process list. But also: "Your VS Code is using 4GB of RAM — that extension you installed yesterday might have a memory leak."

### First Steps
1. Create `src/renderer/components/FridayMonitor.tsx` — system dashboard
2. Wire to `system-management.ts` for metrics
3. Add AI commentary: explanations, recommendations, trend analysis
4. Proactive alerting (calibrated by personality settings)

---

## 9. Friday Media — Audio/Video with Visualization

**Status:** Planned | **Backend:** Audio viz + `media-streaming.ts`

### The Essential Question
*What does music feel like when your AI's holographic desktop reacts to it?*

### Socratic Inquiry

**Q: What's the connection to the holographic desktop?**
The VoiceOrb already has audio-reactive visualization. The holographic desktop has mood-reactive visuals. Music should flow through the same system — the desktop's bloom, color, and structure respond to the audio, creating an immersive visual experience.

**Q: What exists?**
`media-streaming.ts` handles media services. The `VoiceOrb.tsx` has audio level analysis (`getLevels`). The holographic desktop has mood palettes.

### First Steps
1. Create `src/renderer/components/FridayMedia.tsx` — media player
2. Connect audio analysis to holographic desktop mood system
3. Wire to `media-streaming.ts` for service integration
4. Audio-reactive visualization (extend existing VoiceOrb analysis)

---

## 10. Friday Gallery — AI-Tagged Photo Browser

**Status:** Planned | **Backend:** `document-ingestion.ts`

### The Essential Question
*What if you could search your photos by describing what's in them?*

### Socratic Inquiry

**Q: What makes this different from Google Photos?**
Privacy. All indexing happens locally. No cloud uploads. The agent uses Gemini Vision to describe photos, then stores descriptions in semantic search for natural language queries.

**Q: What already works?**
`document-ingestion.ts` can extract content from images. `semantic-search.ts` can search by meaning. The pipeline exists — it just needs a gallery UI and a batch indexing process.

### First Steps
1. Create `src/renderer/components/FridayGallery.tsx` — photo grid with AI search
2. Batch index photos through `document-ingestion.ts` + Gemini Vision
3. Store descriptions in `semantic-search.ts`
4. Natural language search: "photos from the beach last summer"

---

## 11. Friday Store — MCP Tool/Agent Marketplace

**Status:** Planned | **Backend:** Metorial catalog + A2A

### The Essential Question
*How do you trust a tool written by a stranger to run inside your sovereign agent?*

### Socratic Inquiry

**Q: What's the trust model?**
Every tool in the store must carry a cLaw attestation. Tools that violate the Three Laws are rejected. Tools that access sensitive data require explicit consent (consent gate). The container engine provides sandboxed execution with resource limits.

**Q: What already exists?**
- `container-engine.ts` — Sandboxed execution environments
- `superpower-store.ts` / `superpowers-registry.ts` — Power system with toggle controls
- `git-loader.ts` — Git-based program loading
- `mcp-client.ts` — MCP protocol client

**Q: What's the curation model?**
Community-submitted tools + AI review + cLaw compliance check. The agent itself can evaluate whether a tool is safe before installing it.

### First Steps
1. Create `src/renderer/components/FridayStore.tsx` — app store UI
2. Wire to MCP catalog for available tools
3. cLaw compliance verification before installation
4. Container engine sandboxing for untrusted tools
5. Rating/review system based on trust graph principles

---

## 12. Friday Camera — Webcam + Face Tracking + Holographic

**Status:** Planned | **Backend:** MediaPipe + face-tracker

### The Essential Question
*What happens when your AI can see you?*

### Socratic Inquiry

**Q: What's the privacy constraint?**
Face data NEVER leaves the device. No cloud face recognition. No storing facial features. MediaPipe runs locally. The agent can detect expressions (for mood-reactive responses) but doesn't store biometric data.

**Q: What's the holographic connection?**
The holographic desktop can respond to facial expressions. Smile → warmer palette. Focused expression → minimal distractions. This is the ultimate mood-reactive interface.

**Q: What's the cLaw consideration?**
First Law: facial data is biometric data. Storing it could enable harm. The agent processes face landmarks in real-time but never persists them to disk.

### First Steps
1. Create `src/renderer/components/FridayCamera.tsx` — webcam view with overlays
2. MediaPipe face detection (local only)
3. Expression analysis → mood system integration
4. Holographic desktop reactive to user presence
5. ZERO persistence of facial data (cLaw compliance)

---

## 13. Friday Recorder — Voice Notes with Transcription

**Status:** Planned | **Backend:** AudioWorklet + Gemini

### The Essential Question
*Should a voice note be a recording, or a memory?*

### Socratic Inquiry

**Q: What happens after transcription?**
The transcribed text should flow into the memory system. A voice note about "call Sarah about the contract" should:
1. Create a transcript (human-readable)
2. Extract a commitment (commitment-tracker)
3. Surface in future context ("you mentioned wanting to call Sarah")

**Q: What exists?**
The voice pipeline (AudioWorklet → Gemini) already works for live conversation. Recording just needs to capture the audio stream to a file and run transcription asynchronously.

### First Steps
1. Create `src/renderer/components/FridayRecorder.tsx` — voice recording UI
2. Capture AudioWorklet stream to file
3. Transcription via Gemini
4. Memory integration: commitments, context, searchable notes

---

## 14. Friday Weather — AI Weather Briefings

**Status:** Planned | **Backend:** Metorial (weather API)

### The Essential Question
*What does weather mean to YOUR day specifically?*

### Socratic Inquiry

**Q: What's beyond "it's 72 and sunny"?**
"It's going to rain at 3pm. You have an outdoor meeting at 2:30. I'd suggest moving it inside or rescheduling. Also, Sarah tends to be late when it rains — you might want to add 15 minutes of buffer."

This integrates: weather API + calendar + trust graph (Sarah's timeliness score) + personality calibration (how proactive should the suggestion be?).

### First Steps
1. Create `src/renderer/components/FridayWeather.tsx` — weather panel
2. Wire to weather API via Metorial
3. Cross-reference with calendar for schedule impact
4. AI briefing: contextual, personalized, proactive

---

## 15. Friday News — World Monitor with AI Curation

**Status:** Partial | **Backend:** `world-monitor.ts` + intelligence

### The Essential Question
*How do you curate news without creating a filter bubble?*

### Socratic Inquiry

**Q: What exists?**
`world-monitor.ts` watches configurable RSS/news sources. The intelligence layer can analyze and summarize.

**Q: What's the curation principle?**
The agent should show you what's important to YOUR work and interests, but also occasionally surface things outside your bubble. The personality calibration system can manage this: a "curiosity" dial that controls how much serendipitous content appears.

### First Steps
1. Create `src/renderer/components/FridayNews.tsx` — curated news panel
2. Wire to `world-monitor.ts` for sources
3. AI curation: relevance scoring + serendipity injection
4. Briefing format: "3 things that matter to you today"

---

## 16. Friday Maps — AI-Assisted Navigation

**Status:** Planned | **Backend:** Metorial (Google Maps)

### The Essential Question
*When should the agent tell you it's time to leave?*

### Socratic Inquiry

**Q: What's the proactive behavior?**
"Your meeting at 123 Main St is in 45 minutes. Traffic is heavy right now — I'd suggest leaving in 10 minutes to arrive on time." This needs: calendar integration + maps API + traffic data + personality calibration (how much nagging?).

### First Steps
1. Create `src/renderer/components/FridayMaps.tsx` — embedded map
2. Wire to Google Maps via Metorial
3. Calendar-aware departure suggestions
4. Integrated with meeting prep briefings

---

## 17. Friday Contacts — Relationship Memory + Contact Management

**Status:** Planned | **Backend:** `relationship-memory.ts`

### The Essential Question
*What if your contact book remembered the emotional context of every interaction?*

### Socratic Inquiry

**Q: What exists?**
`relationship-memory.ts` tracks relationship context. The trust graph scores people on 5 dimensions. The agent already knows things like "last spoke 3 weeks ago" and "usually responds within an hour."

**Q: What's the UI?**
A contact card that shows:
- Traditional info (name, email, phone)
- Trust dimensions (radar chart of reliability/expertise/emotional/timeliness/information)
- Relationship timeline (interactions, sentiment trends)
- Pending commitments (what you owe them, what they owe you)
- Agent's assessment: "Strong professional relationship. Very reliable with deadlines."

### First Steps
1. Create `src/renderer/components/FridayContacts.tsx` — contact manager
2. Wire to `relationship-memory.ts` + trust graph
3. Trust dimension visualization (radar chart)
4. Commitment tracking per contact
5. AI relationship summary

---

## 18. Friday Tasks — AI Task Management + Scheduling

**Status:** Partial | **Backend:** `scheduler/` + agents

### The Essential Question
*What's the difference between a task the user assigned and a task the agent inferred?*

### Socratic Inquiry

**Q: What exists?**
`scheduler.ts` handles scheduling. `commitment-tracker.ts` detects promises. The agent already tracks commitments from conversations.

**Q: What's the taxonomy?**
1. Explicit tasks: "Remind me to call Sarah tomorrow"
2. Inferred commitments: "I'll have that report by Friday" (detected from conversation)
3. Suggested tasks: "You haven't responded to that email in 3 days — should I draft a reply?"
4. Recurring patterns: "You usually review the weekly metrics on Monday"

The UI needs to distinguish these clearly.

### First Steps
1. Create `src/renderer/components/FridayTasks.tsx` — task manager
2. Wire to `commitment-tracker.ts` for inferred tasks
3. Wire to `scheduler.ts` for scheduling
4. Task classification: explicit / inferred / suggested / recurring
5. Calendar integration for due dates

---

## 19. Friday Docs — Document Viewer with AI Analysis

**Status:** Partial | **Backend:** `document-ingestion.ts`

### The Essential Question
*What if reading a document meant having the author available to answer questions?*

### Socratic Inquiry

**Q: What exists?**
`document-ingestion.ts` extracts content from PDF, DOCX, and other formats. Semantic search indexes the content.

**Q: What's the AI layer?**
After ingestion, the agent can answer questions about the document: "What are the key terms in this contract?" "Summarize the risk factors." "Compare this with the version from last month."

### First Steps
1. Create `src/renderer/components/FridayDocs.tsx` — document viewer
2. PDF/DOCX rendering (use `react-pdf` or similar)
3. AI sidebar: Q&A about the document, key extraction, summarization
4. Cross-document comparison

---

## 20. Friday Canvas — AI-Assisted Drawing + 3D Modeling

**Status:** Planned | **Backend:** `creative-3d.ts`

### The Essential Question
*What does creativity look like when AI is your collaborator, not your replacement?*

### Socratic Inquiry

**Q: What exists?**
`creative-3d.ts` (connectors) handles 3D content. The multimedia engine has Nano Banana 2 for image generation. The holographic desktop is built on Three.js.

**Q: What's the collaboration model?**
The user sketches rough ideas. The agent refines them. "Make this circle more like a gear." "Add depth to this flat shape." "Turn this 2D sketch into a 3D model." The key is that the human drives creative intent, and the AI handles technical execution.

### First Steps
1. Create `src/renderer/components/FridayCanvas.tsx` — drawing canvas
2. Simple 2D drawing tools
3. AI interpretation: "turn my sketch into..." (via image generation)
4. Three.js integration for 3D from the holographic desktop
5. Text-to-3D pipeline: describe a shape → generate geometry

---

## 21. Friday Code — VS Code Integration + AI Coding

**Status:** Partial | **Backend:** `vscode.ts` + `dev-environments.ts`

### The Essential Question
*When the agent can read your code, run your tests, and understand your architecture — is it pair programming or is it something new?*

### Socratic Inquiry

**Q: What exists?**
`vscode.ts` integrates with VS Code. `dev-environments.ts` manages development environments. `git-devops.ts` handles git analysis, review, and sandboxed operations.

**Q: What makes this different from Copilot/Cursor?**
Context. The agent has your trust graph (who wrote this code and how reliable are they?), your memory (you tried this approach last month and it didn't work), your calendar (the deadline is Friday), and your personality profile (you prefer explicit error handling over try-catch-ignore). It doesn't just complete code — it understands your engineering philosophy.

### First Steps
1. Create `src/renderer/components/FridayCode.tsx` — code editor panel
2. Wire to `vscode.ts` for VS Code control
3. Git integration via `git-devops.ts`
4. Context-aware suggestions (personality + history + architecture knowledge)

---

## 22. Friday Wallet — ACP-Backed Financial Management

**Status:** Planned | **Backend:** `commerce/` (ACP)

### The Essential Question
*How does an AI agent handle money without violating the First Law?*

### Socratic Inquiry

**Q: What's the cLaw constraint?**
Financial harm is explicitly covered by the First Law. The agent must NEVER make financial transactions without explicit consent. EVERY purchase, transfer, or financial commitment requires user approval — no "smart autopay" that surprises you.

**Q: What's ACP?**
Agent Commerce Protocol — a framework for AI agents to handle financial operations with human oversight at every step.

**Q: What's the minimum safe version?**
View-only financial dashboard. The agent can show balances, transactions, and spending patterns. Any action (transfer, purchase, payment) requires the user to physically confirm in a separate consent flow — not just "yes" in chat.

### First Steps
1. Create `src/renderer/components/FridayWallet.tsx` — financial dashboard
2. View-only by default (cLaw: minimize financial risk)
3. Spending analysis and pattern detection
4. Multi-step consent for any financial action
5. NEVER store financial credentials (Sovereign Vault exception: these go through the user's bank's auth, not stored by the agent)

---

## 23. Friday Superpowers — Git-Loaded Program Manager

**Status:** Planned | **Backend:** `git-loader/` + `registry.ts`

### The Essential Question
*How do you give an agent new abilities without compromising its governance?*

### Socratic Inquiry

**Q: What exists?**
`git-loader.ts` can load programs from git repos. `superpowers-registry.ts` manages available powers. `superpower-ecosystem.ts` coordinates the power system. `superpower-store.ts` handles the store.

**Q: What's the governance model?**
Every superpower loaded from git runs through:
1. cLaw compliance check (does it respect the Three Laws?)
2. Container engine sandboxing (resource limits, filesystem restrictions)
3. Consent gate (does it need user permission to act?)
4. Trust scoring (who published it? what's their trust score?)

**Q: What's the UI?**
A power manager where you can:
- Browse available superpowers
- See trust/safety ratings
- Enable/disable powers with toggle controls
- Monitor resource usage per power
- Review what each power can access

### First Steps
1. Create `src/renderer/components/FridaySuperpowers.tsx` — power manager UI
2. Wire to `git-loader.ts` for installation
3. Wire to `superpowers-registry.ts` for available powers
4. Container engine integration for sandboxing
5. Trust/safety visualization per power

---

## Execution Priority

Based on backend maturity and user impact:

### Tier 1 — Backend exists, just needs UI
1. **Friday Calendar** — `calendar.ts` is full-featured
2. **Friday Tasks** — `commitment-tracker.ts` + `scheduler.ts` ready
3. **Friday Contacts** — `relationship-memory.ts` + trust graph ready
4. **Friday News** — `world-monitor.ts` is functional

### Tier 2 — Backend partially exists
5. **Friday Mail** — `communications.ts` needs inbox UI
6. **Friday Messages** — `gateway/` needs unified inbox
7. **Friday Browser** — `browser.ts` + `pageindex/` need AI overlay
8. **Friday Docs** — `document-ingestion.ts` needs viewer UI
9. **Friday Code** — `vscode.ts` + `dev-environments.ts` need editor panel

### Tier 3 — Needs significant new work
10. **Friday Files** — Semantic file indexing at scale
11. **Friday Notes** — Obsidian integration + editor
12. **Friday Gallery** — Batch image indexing
13. **Friday Store** — MCP marketplace with trust model
14. **Friday Superpowers** — Power manager UI

### Tier 4 — Requires new infrastructure
15. **Friday Calc** — Math engine integration
16. **Friday Monitor** — System metrics collection
17. **Friday Media** — Audio pipeline + holographic integration
18. **Friday Weather** — Weather API integration
19. **Friday Maps** — Maps API integration
20. **Friday Camera** — MediaPipe integration + privacy model
21. **Friday Recorder** — Audio capture + async transcription
22. **Friday Canvas** — Drawing + 3D pipeline
23. **Friday Wallet** — Financial APIs + multi-step consent

---

*Each app in this forge is more than a feature — it's a question about what computing looks like when AI is woven into every interaction. The answers will emerge through building.*
