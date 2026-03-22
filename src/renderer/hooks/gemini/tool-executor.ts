/**
 * Tool call routing logic for Gemini Live.
 *
 * Handles the massive if/else chain that routes Gemini tool calls to
 * the appropriate window.eve.* handlers, browser tools, MCP, connectors, etc.
 */

import type { ToolExecutionContext } from './types';

interface FunctionCall {
  id: string;
  name: string;
  args?: Record<string, unknown>;
}

/**
 * Execute a single Gemini tool call and return the response payload.
 * This function is called once per function call in a `toolCall` message,
 * and all calls are executed in parallel via Promise.all.
 */
export async function executeToolCall(
  fc: FunctionCall,
  ctx: ToolExecutionContext
): Promise<{ response: { result?: string; error?: string }; id: string }> {
  const actionId = `${fc.id}-${Date.now()}`;
  const toolStartTime = Date.now();
  ctx.optionsRef.current.onToolStart?.(actionId, fc.name);
  let success = true;

  try {
    let resultText: string;

    if (fc.name === 'ask_claude') {
      const question = fc.args?.question || '';
      console.log('[GeminiLive] Routing to Claude:', String(question).slice(0, 100));

      const base = await ctx.getApiBase();
      const res = await fetch(`${base}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: question, history: [] }),
      });

      const chatData = await res.json();
      resultText = chatData.response || 'No response from Claude.';
      ctx.optionsRef.current.onClaudeUsed?.(String(question), resultText);
    } else if (fc.name === 'save_memory') {
      const fact = String(fc.args?.fact || '');
      const category = String(fc.args?.category || 'identity');
      await window.eve.memory.addImmediate(fact, category);
      resultText = `Saved to memory: "${fact}"`;
      console.log('[GeminiLive] Memory saved:', fact);
    } else if (fc.name === 'setup_intelligence') {
      const topics = (fc.args?.research_topics || []) as Array<{
        topic: string;
        schedule: string;
        priority: string;
      }>;
      resultText = await window.eve.intelligence.setup(topics);
      console.log('[GeminiLive] Intelligence setup:', resultText);
    } else if (fc.name === 'create_task') {
      const task = await window.eve.scheduler.createTask(fc.args || {});
      resultText = `Task created: "${task.description}" (ID: ${(task as any).id}, type: ${(task as any).type})`;
      console.log('[GeminiLive] Task created:', (task as any).id);
    } else if (fc.name === 'list_tasks') {
      const tasks = await window.eve.scheduler.listTasks();
      if (tasks.length === 0) {
        resultText = 'No scheduled tasks.';
      } else {
        resultText = tasks
          .map(
            (t: any) =>
              `[${t.id}] ${t.description} (${t.type}, action: ${t.action}${t.cronPattern ? `, cron: ${t.cronPattern}` : ''}${t.triggerTime ? `, at: ${new Date(t.triggerTime).toLocaleString()}` : ''})`
          )
          .join('\n');
      }
    } else if (fc.name === 'delete_task') {
      const deleted = await window.eve.scheduler.deleteTask(
        String(fc.args?.task_id || '')
      );
      resultText = deleted ? 'Task deleted.' : 'Task not found.';
    } else if (fc.name === 'read_own_source') {
      const filePath = String(fc.args?.file_path || '');
      console.log('[GeminiLive] Self-improve: reading', filePath);
      resultText = await window.eve.selfImprove.readFile(filePath);
    } else if (fc.name === 'list_own_files') {
      const dirPath = String(fc.args?.dir_path || '.');
      console.log('[GeminiLive] Self-improve: listing', dirPath);
      const files = await window.eve.selfImprove.listFiles(dirPath);
      resultText = (files as string[]).join('\n');
    } else if (fc.name === 'propose_code_change') {
      const filePath = String(fc.args?.file_path || '');
      const newContent = String(fc.args?.new_content || '');
      const description = String(fc.args?.description || '');
      console.log('[GeminiLive] Self-improve: proposing change to', filePath);
      const result = await window.eve.selfImprove.proposeChange(filePath, newContent, description);
      resultText = result.message || (result.approved ? 'Change approved and applied.' : 'Change was denied by user.');
    } else if (fc.name === 'spawn_agent') {
      const agentType = String(fc.args?.agent_type || '');
      const description = String(fc.args?.description || '');
      const input = (fc.args?.input || {}) as Record<string, unknown>;
      console.log('[GeminiLive] Spawning agent:', agentType, description);
      const task = await window.eve.agents.spawn(agentType, description, input);
      resultText = `Agent spawned: "${description}" (type: ${agentType}, ID: ${task.id.slice(0, 8)}, status: ${task.status}). I'll work on this in the background.`;
    } else if (fc.name === 'check_agent') {
      const taskId = String(fc.args?.task_id || '');
      console.log('[GeminiLive] Checking agent:', taskId);
      const task = await window.eve.agents.get(taskId);
      if (!task) {
        resultText = 'Task not found — it may have been cleaned up.';
      } else {
        const parts = [`Status: ${task.status}`, `Progress: ${task.progress}%`];
        if (task.logs.length > 0) {
          parts.push(`Latest log: ${task.logs[task.logs.length - 1]}`);
        }
        if (task.result) {
          parts.push(`\nResult:\n${task.result}`);
        }
        if (task.error) {
          parts.push(`Error: ${task.error}`);
        }
        if (task.completedAt && task.startedAt) {
          const secs = Math.round((task.completedAt - task.startedAt) / 1000);
          parts.push(`Duration: ${secs}s`);
        }
        resultText = parts.join('\n');
      }
    } else if (fc.name === 'read_document') {
      const query = String(fc.args?.query || '');
      console.log('[GeminiLive] Reading document:', query);

      // Try to find by ID first, then search by name
      let doc = await window.eve.documents.get(query);
      if (!doc) {
        const results = await window.eve.documents.search(query);
        doc = results[0] || undefined;
      }

      if (!doc) {
        resultText = `No document found matching "${query}". The user may need to ingest the document first (File > Ingest Document).`;
      } else {
        const preview = doc.content.length > 3000
          ? doc.content.slice(0, 3000) + '\n\n[... content truncated — full document is ' + Math.round(doc.content.length / 1024) + 'KB]'
          : doc.content;
        resultText = `**${doc.filename}** (${doc.mimeType}, ${Math.round(doc.size / 1024)}KB)\n\nSummary: ${doc.summary}\n\nContent:\n${preview}`;
      }
    } else if (fc.name === 'search_documents') {
      const query = String(fc.args?.query || '');
      console.log('[GeminiLive] Searching documents:', query);

      const docs = await window.eve.documents.search(query);
      if (docs.length === 0) {
        resultText = 'No matching documents found. The user may need to ingest documents first.';
      } else {
        resultText = docs
          .map((d: any) =>
            `- **${d.filename}** (${Math.round(d.size / 1024)}KB, ${d.mimeType}): ${d.summary}`
          )
          .join('\n');
      }
    } else if (fc.name === 'watch_project') {
      const rootPath = String(fc.args?.root_path || '');
      console.log('[GeminiLive] Watching project:', rootPath);

      const profile = await window.eve.project.watch(rootPath);
      const parts = [
        `Project: ${profile.name} (${profile.type}${profile.framework ? '/' + profile.framework : ''})`,
      ];
      if (profile.description) parts.push(`Description: ${profile.description}`);
      if (profile.gitBranch) parts.push(`Branch: ${profile.gitBranch} (${profile.gitStatus || 'unknown'})`);
      if (profile.keyFiles.length > 0) parts.push(`Key files: ${profile.keyFiles.join(', ')}`);
      if (profile.recentChanges.length > 0) parts.push(`Recent commits:\n${profile.recentChanges.map((c: string) => `  - ${c}`).join('\n')}`);
      if (profile.structure.length > 0) parts.push(`Structure:\n${profile.structure.slice(0, 15).join('\n')}`);
      resultText = parts.join('\n');
    } else if (fc.name === 'get_project_context') {
      console.log('[GeminiLive] Getting project context');

      const projects = await window.eve.project.list();
      if (projects.length === 0) {
        resultText = 'No projects being watched. Ask the user for a project path to watch.';
      } else {
        resultText = projects
          .map((p: any) => {
            const parts = [`**${p.name}** (${p.type}${p.framework ? '/' + p.framework : ''})`];
            if (p.gitBranch) parts.push(`Branch: ${p.gitBranch} (${p.gitStatus || 'unknown'})`);
            if (p.keyFiles.length > 0) parts.push(`Key files: ${p.keyFiles.join(', ')}`);
            if (p.recentChanges.length > 0) parts.push(`Recent: ${p.recentChanges[0]}`);
            return parts.join(' | ');
          })
          .join('\n');
      }
    } else if (fc.name === 'get_calendar') {
      const count = Number(fc.args?.count || 5);
      console.log('[GeminiLive] Getting calendar events');

      const isAuthed = await window.eve.calendar.isAuthenticated();
      if (!isAuthed) {
        resultText = 'Google Calendar is not connected. The user needs to authenticate in Settings first.';
      } else {
        const events = await window.eve.calendar.getUpcoming(count);
        if (events.length === 0) {
          resultText = 'No upcoming events today.';
        } else {
          resultText = events
            .map((e: any) => {
              const start = new Date(e.start);
              const timeStr = start.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
              const minsUntil = Math.round((start.getTime() - Date.now()) / 60000);
              let line = `- ${timeStr} (in ${minsUntil}m): ${e.summary}`;
              if (e.attendees.length > 0) line += ` [${e.attendees.length} attendees: ${e.attendees.slice(0, 3).join(', ')}${e.attendees.length > 3 ? '...' : ''}]`;
              if (e.hangoutLink) line += ' [has video link]';
              if (e.location) line += ` @ ${e.location}`;
              return line;
            })
            .join('\n');
        }
      }
    } else if (fc.name === 'create_calendar_event') {
      const summary = String(fc.args?.summary || '');
      const startTime = String(fc.args?.start_time || '');
      const endTime = String(fc.args?.end_time || '');
      const description = fc.args?.description ? String(fc.args.description) : undefined;
      const attendees = Array.isArray(fc.args?.attendees) ? fc.args.attendees as string[] : undefined;
      const location = fc.args?.location ? String(fc.args.location) : undefined;
      console.log('[GeminiLive] Creating calendar event:', summary);

      const isAuthed = await window.eve.calendar.isAuthenticated();
      if (!isAuthed) {
        resultText = 'Google Calendar is not connected. The user needs to authenticate in Settings first.';
      } else {
        const event = await window.eve.calendar.createEvent({
          summary,
          startTime,
          endTime,
          description,
          attendees,
          location,
        });
        if (event) {
          const startStr = new Date(event.start).toLocaleString('en-GB', {
            weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
          });
          resultText = `Event created: "${event.summary}" on ${startStr}${event.attendees.length > 0 ? ` with ${event.attendees.length} attendees` : ''}${event.location ? ` @ ${event.location}` : ''}`;
        } else {
          resultText = 'Failed to create calendar event. Check the Google Calendar connection.';
        }
      }
    } else if (fc.name === 'draft_communication') {
      const type = String(fc.args?.type || 'email') as 'email' | 'message' | 'reply' | 'follow-up';
      const to = String(fc.args?.to || '');
      const context = String(fc.args?.context || '');
      const subject = fc.args?.subject ? String(fc.args.subject) : undefined;
      const tone = (fc.args?.tone || 'professional') as 'formal' | 'casual' | 'friendly' | 'professional' | 'urgent';
      const originalMessage = fc.args?.original_message ? String(fc.args.original_message) : undefined;
      const maxLength = (fc.args?.max_length || 'medium') as 'short' | 'medium' | 'long';
      console.log('[GeminiLive] Drafting communication:', type, 'to', to);

      const draft = await window.eve.communications.draft({
        type,
        to,
        context,
        subject,
        tone,
        originalMessage,
        maxLength,
      });

      // Auto-copy to clipboard
      await window.eve.communications.copy(draft.id);

      resultText = `Draft ${type} created and copied to clipboard.\n\n${draft.subject ? `Subject: ${draft.subject}\n\n` : ''}${draft.body}\n\n---\n(Draft ID: ${draft.id} — I can refine this or open it in your email client.)`;
    } else if (fc.name === 'finalize_agent_identity') {
      // Onboarding complete — save agent config and notify renderer
      const agentConfig = {
        agentName: String(fc.args?.agent_name || ''),
        agentVoice: String(fc.args?.voice_name || 'Kore'),
        agentGender: String(fc.args?.gender || 'female'),
        agentAccent: String(fc.args?.accent || ''),
        agentBackstory: String(fc.args?.backstory || ''),
        agentTraits: Array.isArray(fc.args?.personality_traits) ? (fc.args!.personality_traits as string[]) : [],
        agentIdentityLine: String(fc.args?.identity_line || ''),
        userName: String(fc.args?.user_name || ''),
        onboardingComplete: true,
      };
      console.log('[GeminiLive] Finalizing agent identity:', agentConfig.agentName);
      // Save via IPC to main process
      await window.eve.onboarding.finalizeAgent(agentConfig);
      // Notify App.tsx to show creation animation + reconnect
      ctx.optionsRef.current.onAgentFinalized?.(agentConfig);
      resultText = `Agent identity saved. ${agentConfig.agentName} is being created now. Goodbye — and welcome, ${agentConfig.agentName}.`;
    } else if (fc.name === 'play_voice_sample') {
      // Voice audition — generate a voice sample via REST API and play it
      const voiceName = String(fc.args?.voice_name || 'Kore');
      console.log(`[GeminiLive] Playing voice sample: ${voiceName}`);
      try {
        const sample = await window.eve.voiceAudition.generateSample(voiceName);
        if (sample && sample.audio) {
          // Decode base64 audio and play through a temporary Audio element
          // (not the main playback engine, since that expects raw PCM chunks)
          const audioBytes = Uint8Array.from(atob(sample.audio), (c) => c.charCodeAt(0));
          const blob = new Blob([audioBytes], { type: sample.mimeType });
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          // Wait for playback to finish so Gemini can time its next message
          await new Promise<void>((resolveAudio) => {
            audio.onended = () => {
              URL.revokeObjectURL(url);
              resolveAudio();
            };
            audio.onerror = () => {
              URL.revokeObjectURL(url);
              resolveAudio();
            };
            audio.play().catch(() => resolveAudio());
          });
          resultText = `Voice sample for "${voiceName}" played successfully. Ask the user what they think.`;
        } else {
          resultText = `Could not generate a voice sample for "${voiceName}". Describe the voice instead and move on.`;
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn('[GeminiLive] Voice sample error:', msg);
        resultText = `Voice sample generation failed: ${msg}. Describe the voice instead.`;
      }
    } else if (fc.name === 'acknowledge_introduction') {
      // Trust introduction complete — user is ready to proceed to intake
      const userResponse = String(fc.args?.user_response || '');
      const questions = fc.args?.questions_asked as string[] || [];
      console.log('[GeminiLive] Trust introduction acknowledged:', userResponse);
      if (questions.length > 0) {
        console.log('[GeminiLive] User asked questions:', questions.join(', '));
      }
      resultText = 'Trust introduction acknowledged. The user understands the system and is ready for setup. Now transition to the intake phase — ask the three "Her" questions one at a time.';
    } else if (fc.name === 'save_intake_responses') {
      // "Her" intake — save the three raw responses and generate psych profile
      const responses = {
        voicePreference: String(fc.args?.voice_preference || ''),
        socialDescription: String(fc.args?.social_description || ''),
        motherRelationship: String(fc.args?.mother_relationship || ''),
      };
      console.log('[GeminiLive] Saving intake responses');
      await window.eve.psychProfile.generate(responses);
      resultText = 'Intake responses saved and psychological profile generated. You may now transition to agent customization.';
    } else if (fc.name === 'transition_to_customization') {
      // The wizard handles customization flow — this is a no-op during wizard onboarding
      console.log('[GeminiLive] transition_to_customization called (wizard handles flow)');
      resultText = 'Acknowledged. Continue with the personal intake questions.';
    } else if (fc.name === 'mark_feature_setup_step') {
      // Feature configured — record it and continue conversation naturally
      const step = String(fc.args?.step || '');
      const action = String(fc.args?.action || 'complete') as 'complete' | 'skip';
      console.log(`[GeminiLive] Feature configured: ${step} → ${action}`);
      try {
        await window.eve.featureSetup.advance(step, action);
        resultText = `Feature "${step}" ${action === 'complete' ? 'configured successfully' : 'noted as skipped'}.`;
      } catch (fsErr) {
        const fsMsg = fsErr instanceof Error ? fsErr.message : String(fsErr);
        resultText = `Feature setup error: ${fsMsg}`;
      }
    } else if (fc.name === 'start_calendar_auth') {
      // Feature setup — trigger Google Calendar OAuth flow
      console.log('[GeminiLive] Starting Calendar OAuth');
      try {
        const result = await window.eve.calendar.authenticate();
        if (result) {
          resultText = 'Google Calendar connected successfully! The user can now ask about their schedule, and I can create events for them.';
        } else {
          resultText = 'Calendar authentication was cancelled or failed. The user can try again later in Settings.';
        }
      } catch (authErr) {
        const authMsg = authErr instanceof Error ? authErr.message : String(authErr);
        console.warn('[GeminiLive] Calendar auth error:', authMsg);
        if (authMsg.includes('credentials') || authMsg.includes('ENOENT')) {
          resultText = 'Calendar authentication failed — no Google credentials file found. The user needs to set up a Google Cloud project with Calendar API enabled and place the credentials.json file in the app data directory. This is a one-time setup. They can skip this for now and set it up later.';
        } else {
          resultText = `Calendar authentication failed: ${authMsg}. The user can try again later in Settings.`;
        }
      }
    } else if (fc.name === 'save_api_key') {
      // Feature setup — save an API key for a service
      const service = String(fc.args?.service || '');
      const apiKey = String(fc.args?.api_key || '');
      console.log(`[GeminiLive] Saving API key for: ${service}`);
      try {
        const keyMap: Record<string, 'perplexity' | 'firecrawl' | 'openai' | 'elevenlabs'> = {
          perplexity: 'perplexity',
          firecrawl: 'firecrawl',
          openai: 'openai',
          elevenlabs: 'elevenlabs',
        };
        const settingsKey = keyMap[service];
        if (!settingsKey) {
          resultText = `Unknown service "${service}". Supported: perplexity, firecrawl, openai, elevenlabs.`;
        } else if (!apiKey || apiKey.length < 8) {
          resultText = `The API key seems too short or empty. Ask the user to double-check it.`;
        } else {
          await window.eve.settings.setApiKey(settingsKey, apiKey);
          resultText = `${service.charAt(0).toUpperCase() + service.slice(1)} API key saved successfully! The service is now available.`;
        }
      } catch (keyErr) {
        const keyMsg = keyErr instanceof Error ? keyErr.message : String(keyErr);
        resultText = `Failed to save API key: ${keyMsg}`;
      }
    } else if (fc.name === 'set_obsidian_vault_path') {
      // Feature setup — set Obsidian vault path
      const vaultPath = String(fc.args?.vault_path || '');
      console.log(`[GeminiLive] Setting Obsidian vault path: ${vaultPath}`);
      try {
        if (!vaultPath) {
          resultText = 'No vault path provided. Ask the user for the full path to their Obsidian vault folder.';
        } else {
          await window.eve.settings.setObsidianVaultPath(vaultPath);
          resultText = `Obsidian vault path set to "${vaultPath}". I can now read and search notes from this vault.`;
        }
      } catch (vaultErr) {
        const vaultMsg = vaultErr instanceof Error ? vaultErr.message : String(vaultErr);
        resultText = `Failed to set vault path: ${vaultMsg}`;
      }
    } else if (fc.name === 'toggle_screen_capture') {
      // Feature setup — enable/disable screen capture
      const enabled = Boolean(fc.args?.enabled);
      console.log(`[GeminiLive] Screen capture: ${enabled ? 'enabling' : 'disabling'}`);
      try {
        await window.eve.settings.setAutoScreenCapture(enabled);
        resultText = enabled
          ? 'Screen capture enabled. I\'ll periodically capture what\'s on screen to stay contextually aware. All captures stay local and private.'
          : 'Screen capture disabled. I won\'t capture screen content. The user can re-enable this anytime in Settings.';
      } catch (scErr) {
        const scMsg = scErr instanceof Error ? scErr.message : String(scErr);
        resultText = `Failed to toggle screen capture: ${scMsg}`;
      }
    } else if (fc.name === 'search_episodes') {
      const query = String(fc.args?.query || '');
      console.log('[GeminiLive] Searching episodes:', query);

      // Try semantic search first, fall back to text search
      let episodes: any[] = [];
      try {
        const semanticResults = await window.eve.search.query(query, {
          types: ['episode'],
          maxResults: 8,
        });
        if (semanticResults.length > 0) {
          // Fetch full episode details for semantic matches
          const episodePromises = semanticResults.map((r: any) =>
            window.eve.episodic.get(r.id)
          );
          const fetched = await Promise.all(episodePromises);
          episodes = fetched.filter(Boolean);
        }
      } catch {
        // Semantic search unavailable — fall through
      }

      // Fall back to text search if semantic returned nothing
      if (episodes.length === 0) {
        episodes = await window.eve.episodic.search(query);
      }

      if (episodes.length === 0) {
        resultText = 'No matching past conversations found.';
      } else {
        resultText = episodes
          .map((ep: any) => {
            const date = new Date(ep.startTime).toLocaleDateString('en-GB', {
              day: 'numeric',
              month: 'short',
              year: 'numeric',
            });
            const mins = Math.round(ep.durationSeconds / 60);
            const topics = ep.topics.length > 0 ? ` [${ep.topics.join(', ')}]` : '';
            const decisions =
              ep.keyDecisions.length > 0
                ? `\n  Decisions: ${ep.keyDecisions.join('; ')}`
                : '';
            return `- ${date} (${mins}min, ${ep.emotionalTone}): ${ep.summary}${topics}${decisions}`;
          })
          .join('\n');
      }
    } else if (fc.name === 'enable_webcam') {
      // Webcam vision — start streaming camera frames to Gemini
      console.log('[GeminiLive] Enabling webcam');
      try {
        // Clean up any existing webcam session first
        if (ctx.webcamIntervalRef.current) clearInterval(ctx.webcamIntervalRef.current);
        ctx.webcamStreamRef.current?.getTracks().forEach((t) => t.stop());

        const camStream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, facingMode: 'user' },
        });
        ctx.webcamStreamRef.current = camStream;

        // Create hidden video + canvas for frame capture
        const video = document.createElement('video');
        video.srcObject = camStream;
        video.muted = true;
        video.playsInline = true;
        video.style.display = 'none';
        document.body.appendChild(video);
        await video.play();
        ctx.webcamVideoRef.current = video;

        const canvas = document.createElement('canvas');
        canvas.width = 640;
        canvas.height = 480;
        ctx.webcamCanvasRef.current = canvas;
        const ctx2d = canvas.getContext('2d')!;

        // Stream ~1fps JPEG frames via realtime_input.media_chunks
        ctx.webcamIntervalRef.current = setInterval(() => {
          // Guard: don't send frames until Gemini has confirmed setup
          if (!ctx.wsRef.current || ctx.wsRef.current.readyState !== WebSocket.OPEN || !ctx.setupCompleteRef.current) return;
          ctx2d.drawImage(video, 0, 0, 640, 480);
          const dataUrl = canvas.toDataURL('image/jpeg', 0.7);
          const b64 = dataUrl.split(',')[1]; // strip data:image/jpeg;base64, prefix
          ctx.wsRef.current.send(
            JSON.stringify({
              realtime_input: {
                media_chunks: [{ data: b64, mime_type: 'image/jpeg' }],
              },
            })
          );
        }, 1000);

        ctx.setState((s) => ({ ...s, isWebcamActive: true }));
        resultText = 'Webcam enabled — I can now see what your camera shows. I\'ll describe what I see. Remember to call disable_webcam when done.';
      } catch (camErr) {
        const camMsg = camErr instanceof Error ? camErr.message : String(camErr);
        resultText = `Could not access webcam: ${camMsg}. The user may need to grant camera permission.`;
      }
    } else if (fc.name === 'disable_webcam') {
      // Webcam vision — stop camera
      console.log('[GeminiLive] Disabling webcam');
      if (ctx.webcamIntervalRef.current) {
        clearInterval(ctx.webcamIntervalRef.current);
        ctx.webcamIntervalRef.current = null;
      }
      ctx.webcamStreamRef.current?.getTracks().forEach((t) => t.stop());
      ctx.webcamStreamRef.current = null;
      if (ctx.webcamVideoRef.current) {
        ctx.webcamVideoRef.current.remove();
        ctx.webcamVideoRef.current = null;
      }
      ctx.webcamCanvasRef.current = null;
      ctx.setState((s) => ({ ...s, isWebcamActive: false }));
      resultText = 'Webcam disabled.';
    } else if (fc.name === 'join_meeting') {
      // Live call participation — join a video call via virtual audio routing
      const meetingUrl = String(fc.args?.meeting_url || '');
      console.log('[GeminiLive] Joining meeting:', meetingUrl);

      // Check if VB-Cable virtual audio is available
      const vbAvailable = await window.eve.callIntegration.isVirtualAudioAvailable();
      if (!vbAvailable) {
        resultText = 'Cannot join the call — VB-Cable virtual audio driver is not installed. The user needs to install VB-Cable (free) from https://vb-audio.com/Cable/ so I can route my voice into the meeting. Ask them to install it and restart.';
      } else {
        try {
          // Find the VB-Cable device ID for audio output routing
          const devices = await navigator.mediaDevices.enumerateDevices();
          const vbCableOutput = devices.find(
            (d) => d.kind === 'audiooutput' && d.label.toLowerCase().includes('cable input')
          );

          if (vbCableOutput && ctx.playbackEngineRef.current) {
            // Route agent's audio output to VB-Cable (appears as mic in meeting apps)
            const routed = await ctx.playbackEngineRef.current.setOutputDevice(vbCableOutput.deviceId);
            if (!routed) {
              resultText = 'Found VB-Cable but failed to route audio output. The browser may not support audio device switching.';
            } else {
              // Enter call mode in main process (tracks state)
              await window.eve.callIntegration.enterCallMode(meetingUrl);
              // Open the meeting URL
              if (meetingUrl) {
                await window.eve.callIntegration.openMeetingUrl(meetingUrl);
              }
              ctx.setState((s) => ({ ...s, isInCall: true }));
              // Auto-create meeting in Meeting Intelligence
              try {
                await window.eve.meetingIntel.quickStart(meetingUrl || '', `Call at ${new Date().toLocaleTimeString()}`);
              } catch { /* non-critical */ }
              resultText = `Joined call mode — my voice is now routed through VB-Cable virtual microphone. ${meetingUrl ? 'I\'ve opened the meeting link in the browser.' : ''} The user should select "CABLE Output (VB-Audio Virtual Cable)" as the microphone in their meeting app to hear me. I can hear them through the normal microphone. Meeting intelligence is tracking this call. Use meeting_note to capture key points. Call leave_meeting when done.`;
            }
          } else {
            resultText = 'VB-Cable is installed but I couldn\'t find the "CABLE Input" output device. The user may need to restart their computer after installing VB-Cable.';
          }
        } catch (callErr) {
          const callMsg = callErr instanceof Error ? callErr.message : String(callErr);
          resultText = `Failed to join call: ${callMsg}`;
        }
      }
    } else if (fc.name === 'leave_meeting') {
      // Live call participation — leave meeting and restore normal audio
      console.log('[GeminiLive] Leaving meeting');
      try {
        if (ctx.playbackEngineRef.current) {
          await ctx.playbackEngineRef.current.resetOutputDevice();
        }
        await window.eve.callIntegration.exitCallMode();
        ctx.setState((s) => ({ ...s, isInCall: false }));
        // Auto-end meeting in Meeting Intelligence
        try {
          await window.eve.meetingIntel.endActive();
        } catch { /* non-critical */ }
        resultText = 'Left the meeting — audio routing restored to normal speakers. Meeting intelligence will generate a summary and extract action items. I\'m back to regular mode.';
      } catch (leaveErr) {
        const leaveMsg = leaveErr instanceof Error ? leaveErr.message : String(leaveErr);
        resultText = `Error leaving meeting: ${leaveMsg}`;
      }
    } else if (fc.name === 'register_household_member') {
      // Household voice recognition — store member info in long-term memory
      const memberName = String(fc.args?.name || '');
      const relationship = String(fc.args?.relationship || '');
      const voiceDesc = String(fc.args?.voice_description || 'not yet characterized');
      console.log('[GeminiLive] Registering household member:', memberName, relationship);

      const memoryFact = `Household member: ${memberName} (${relationship}). Voice characteristics: ${voiceDesc}. Registered on ${new Date().toLocaleDateString()}.`;
      await window.eve.memory.addImmediate(memoryFact, 'household');
      resultText = `Registered ${memberName} (${relationship}) as a household member. I'll remember their voice for future sessions.`;
    } else if (fc.name === 'create_podcast') {
      // Multimedia — create a multi-speaker podcast
      const topic = String(fc.args?.topic || 'Untitled Podcast');
      const sources = Array.isArray(fc.args?.sources) ? fc.args.sources : [];
      const style = String(fc.args?.style || 'deep-dive');
      const durationMinutes = Number(fc.args?.duration_minutes || 10);
      console.log('[GeminiLive] Creating podcast:', topic, style, durationMinutes + 'min');
      try {
        const result = await window.eve.multimedia.createPodcast({
          topic,
          sources: sources.length > 0 ? sources : [{ type: 'text', content: topic }],
          style,
          durationMinutes,
        });
        if (result.ok) {
          const r = result.result;
          resultText = `Podcast created: "${r.title}"\n` +
            `Duration: ${Math.round(r.durationSeconds / 60)} minutes | ` +
            `Speakers: ${r.speakers.map((s: any) => s.name).join(', ')} | ` +
            `Segments: ${r.segmentCount}\n` +
            `File saved to: ${r.filePath}\n` +
            `The audio file is ready to play.`;
        } else {
          resultText = `Podcast creation failed: ${result.error}`;
        }
      } catch (err) {
        resultText = `Podcast creation error: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'create_visual') {
      // Multimedia — create a visual artifact
      const prompt = String(fc.args?.prompt || '');
      const type = String(fc.args?.type || 'infographic');
      const data = fc.args?.data ? String(fc.args.data) : undefined;
      console.log('[GeminiLive] Creating visual:', type, prompt.slice(0, 60));
      try {
        const result = await window.eve.multimedia.createVisual({
          prompt, type, data,
        });
        if (result.ok) {
          const r = result.result;
          resultText = `Visual created: "${r.title}" (${r.type})\n` +
            `File saved to: ${r.filePath}\n` +
            `The visual is ready to view.`;
        } else {
          resultText = `Visual creation failed: ${result.error}`;
        }
      } catch (err) {
        resultText = `Visual creation error: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'create_audio_message') {
      // Multimedia — create a voice message
      const text = String(fc.args?.text || '');
      const voice = fc.args?.voice ? String(fc.args.voice) : undefined;
      console.log('[GeminiLive] Creating audio message:', text.slice(0, 60));
      try {
        const result = await window.eve.multimedia.createAudioMessage({
          text, voice,
        });
        if (result.ok) {
          const r = result.result;
          resultText = `Audio message created.\n` +
            `Duration: ${Math.round(r.durationSeconds)} seconds | Voice: ${r.voice}\n` +
            `File saved to: ${r.filePath}`;
        } else {
          resultText = `Audio message creation failed: ${result.error}`;
        }
      } catch (err) {
        resultText = `Audio message error: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'create_music') {
      // Multimedia — generate music
      const prompt = String(fc.args?.prompt || '');
      const durationSeconds = Number(fc.args?.duration_seconds || 15);
      console.log('[GeminiLive] Creating music:', prompt.slice(0, 60));
      try {
        const result = await window.eve.multimedia.createMusic({
          prompt, durationSeconds,
        });
        if (result.ok) {
          const r = result.result;
          resultText = `Music created: "${r.title}"\n` +
            `Duration: ${Math.round(r.durationSeconds)} seconds\n` +
            `File saved to: ${r.filePath}`;
        } else {
          resultText = `Music creation failed: ${result.error}`;
        }
      } catch (err) {
        resultText = `Music creation error: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'update_trust') {
      const personName = String(fc.args?.person_name || '');
      const evidenceType = String(fc.args?.evidence_type || 'observed');
      const description = String(fc.args?.description || '');
      const impact = Number(fc.args?.impact || 0);
      const domain = fc.args?.domain ? String(fc.args.domain) : undefined;
      console.log('[GeminiLive] Trust update:', personName, evidenceType, impact);
      const result = await window.eve.trustGraph.updateEvidence(personName, {
        type: evidenceType, description, impact, domain,
      });
      if (result.ok) {
        resultText = `Updated trust profile for ${personName} — recorded ${evidenceType} evidence (impact: ${impact > 0 ? '+' : ''}${impact}).`;
      } else {
        resultText = `Could not update trust for ${personName}: ${result.error || 'unknown error'}`;
      }
    } else if (fc.name === 'lookup_person') {
      const personName = String(fc.args?.person_name || '');
      console.log('[GeminiLive] Trust lookup:', personName);
      const resolution = await window.eve.trustGraph.lookup(personName);
      if (resolution.person) {
        const context = await window.eve.trustGraph.getContext(resolution.person.id);
        resultText = context || `Found ${resolution.person.primaryName} but no detailed context available yet.`;
      } else {
        resultText = `No person named "${personName}" found in the trust graph. They may be someone new — I'll start tracking them when more information comes up.`;
      }
    } else if (fc.name === 'note_interaction') {
      const personName = String(fc.args?.person_name || '');
      const channel = String(fc.args?.channel || 'conversation');
      const direction = String(fc.args?.direction || 'bidirectional') as 'inbound' | 'outbound' | 'bidirectional';
      const summary = String(fc.args?.summary || '');
      const sentiment = Number(fc.args?.sentiment || 0);
      console.log('[GeminiLive] Trust interaction:', personName, channel, direction);
      const result = await window.eve.trustGraph.logComm(personName, {
        channel, direction, summary, sentiment,
      });
      if (result.ok) {
        resultText = `Logged ${channel} interaction with ${personName} (${direction}).`;
      } else {
        resultText = `Could not log interaction with ${personName}: ${result.error || 'unknown error'}`;
      }
    } else if (fc.name === 'create_meeting') {
      // Meeting Intelligence — create a meeting
      const meetingName = String(fc.args?.name || 'New Meeting');
      const description = fc.args?.description ? String(fc.args.description) : undefined;
      const attendees = Array.isArray(fc.args?.attendees) ? fc.args.attendees.map(String) : undefined;
      const meetingUrl = fc.args?.meeting_url ? String(fc.args.meeting_url) : undefined;
      const scheduledStart = fc.args?.scheduled_start ? String(fc.args.scheduled_start) : undefined;
      const scheduledEnd = fc.args?.scheduled_end ? String(fc.args.scheduled_end) : undefined;
      const tags = Array.isArray(fc.args?.tags) ? fc.args.tags.map(String) : undefined;
      const projectName = fc.args?.project_name ? String(fc.args.project_name) : undefined;
      console.log('[GeminiLive] Creating meeting:', meetingName);
      try {
        const meeting = await window.eve.meetingIntel.create({
          name: meetingName, description, attendees, meetingUrl, scheduledStart, scheduledEnd, tags, projectName,
        });
        const attendeeCount = meeting.attendees?.length || 0;
        const intelCount = meeting.attendeeIntel?.filter((a: any) => a.trustProfile)?.length || 0;
        resultText = `Created meeting "${meetingName}" (ID: ${meeting.id}). ${attendeeCount} attendees tracked${intelCount > 0 ? `, ${intelCount} with trust intelligence` : ''}. Status: upcoming. Say "start the meeting" when ready.`;
      } catch (err) {
        resultText = `Failed to create meeting: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'meeting_note') {
      // Meeting Intelligence — add a note to the active meeting
      const content = String(fc.args?.content || '');
      const noteType = (fc.args?.note_type as string) || 'note';
      console.log('[GeminiLive] Meeting note:', noteType, content.slice(0, 50));
      try {
        const note = await window.eve.meetingIntel.addNoteActive(content, noteType);
        if (note) {
          resultText = `Noted${noteType !== 'note' ? ` [${noteType}]` : ''}: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}"`;
        } else {
          resultText = 'No active meeting to add note to. Create and start a meeting first.';
        }
      } catch (err) {
        resultText = `Failed to add note: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'end_current_meeting') {
      // Meeting Intelligence — end the active meeting
      console.log('[GeminiLive] Ending current meeting');
      try {
        const meeting = await window.eve.meetingIntel.endActive();
        if (meeting) {
          const durationMins = meeting.startedAt && meeting.endedAt
            ? Math.round((meeting.endedAt - meeting.startedAt) / 60000)
            : 0;
          const noteCount = meeting.notes?.length || 0;
          resultText = `Meeting "${meeting.name}" ended after ${durationMins} minutes with ${noteCount} notes. Post-meeting processing started — summary and action items will be generated automatically.`;
        } else {
          resultText = 'No active meeting to end.';
        }
      } catch (err) {
        resultText = `Failed to end meeting: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (fc.name === 'get_meeting_history') {
      // Meeting Intelligence — search or list meeting history
      const search = fc.args?.search ? String(fc.args.search) : undefined;
      const count = fc.args?.count ? Number(fc.args.count) : 5;
      console.log('[GeminiLive] Meeting history:', search || 'recent', count);
      try {
        if (search) {
          const results = await window.eve.meetingIntel.search(search, count);
          if (results.length === 0) {
            resultText = `No meetings found matching "${search}".`;
          } else {
            const lines = results.map((m: any) =>
              `- "${m.name}" (${m.status}) — ${m.summary || 'no summary'}${m.actionItems?.length ? ` | Actions: ${m.actionItems.join('; ')}` : ''}`
            );
            resultText = `Found ${results.length} meeting(s):\n${lines.join('\n')}`;
          }
        } else {
          const summaries = await window.eve.meetingIntel.recentSummaries(count);
          if (summaries.length === 0) {
            resultText = 'No meeting history yet.';
          } else {
            const lines = summaries.map((s: any) =>
              `- "${s.name}" (${s.date}, ${s.attendeeCount} attendees) — ${s.summary}${s.actionItems?.length ? ` | Actions: ${s.actionItems.join('; ')}` : ''}`
            );
            resultText = `Recent meetings:\n${lines.join('\n')}`;
          }
        }
      } catch (err) {
        resultText = `Failed to get meeting history: ${err instanceof Error ? err.message : String(err)}`;
      }
    } else if (['operate_computer', 'browser_task', 'take_screenshot', 'click_screen', 'type_text', 'press_keys'].includes(fc.name)) {
      // Route to Self-Operating Computer / Browser-Use tools
      console.log('[GeminiLive] SOC tool:', fc.name);
      try {
        const socResult = await window.eve.soc.callTool(fc.name, fc.args || {});
        if (socResult && typeof socResult === 'object' && 'error' in socResult) {
          resultText = `SOC Error: ${(socResult as any).error}`;
        } else if (fc.name === 'take_screenshot' && socResult && typeof socResult === 'object' && 'image' in socResult) {
          // Send screenshot as image for Gemini to see
          try {
            if (ctx.wsRef.current && ctx.wsRef.current.readyState === WebSocket.OPEN) {
              ctx.wsRef.current.send(JSON.stringify({
                realtime_input: {
                  media_chunks: [{ data: (socResult as any).image, mime_type: 'image/png' }],
                },
              }));
            }
            resultText = `Screenshot captured (${(socResult as any).width}x${(socResult as any).height}). Image sent for visual analysis.`;
          } catch {
            resultText = `Screenshot captured (${(socResult as any).width}x${(socResult as any).height}) but could not send image.`;
          }
        } else {
          resultText = typeof socResult === 'string' ? socResult : JSON.stringify(socResult);
        }
      } catch (socErr: unknown) {
        resultText = `SOC Error: ${socErr instanceof Error ? socErr.message : String(socErr)}`;
      }
    } else if (fc.name.startsWith('git_')) {
      // Route to GitLoader tools
      console.log('[GeminiLive] GitLoader tool:', fc.name);
      try {
        const gitResult = await window.eve.gitLoader.callTool(fc.name, fc.args || {});
        if (gitResult && typeof gitResult === 'object' && 'error' in gitResult) {
          resultText = `GitLoader Error: ${(gitResult as any).error}`;
        } else {
          resultText = typeof gitResult === 'string' ? gitResult : JSON.stringify(gitResult);
        }
        // Truncate very large results (repo trees can be huge)
        if (resultText.length > 30000) {
          resultText = resultText.slice(0, 30000) + '\n\n... [truncated — result too large. Use git_search or git_get_file for specific files]';
        }
      } catch (gitErr: unknown) {
        resultText = `GitLoader Error: ${gitErr instanceof Error ? gitErr.message : String(gitErr)}`;
      }
    } else if (fc.name.startsWith('browser_')) {
      // Route to browser automation tools
      console.log('[GeminiLive] Browser tool:', fc.name);
      resultText = await window.eve.browser.callTool(fc.name, fc.args || {});

      // Special handling: send screenshots as images so Gemini can SEE them
      if (fc.name === 'browser_screenshot' && resultText && resultText.length > 1000) {
        // resultText is base64 JPEG — send as image via realtime_input before tool response
        try {
          if (ctx.wsRef.current && ctx.wsRef.current.readyState === WebSocket.OPEN) {
            ctx.wsRef.current.send(JSON.stringify({
              realtime_input: {
                media_chunks: [{ data: resultText, mime_type: 'image/jpeg' }],
              },
            }));
          }
          resultText = 'Screenshot captured and sent to you for visual analysis. Describe what you see on the page — the layout, text, buttons, forms, and any relevant elements. Use this to decide your next action.';
        } catch (imgErr) {
          console.warn('[GeminiLive] Failed to send screenshot image:', imgErr);
          resultText = 'Screenshot taken but could not send image for analysis. Use browser_read_page to get text content instead.';
        }
      }
    } else {
      // Check if this is a connector tool (dynamic software-mastery tools)
      let isConnector = false;
      try {
        isConnector = await window.eve.connectors.isConnectorTool(fc.name);
      } catch {
        // Connector system not available — fall through to desktop tools
      }

      if (isConnector) {
        // Route to connector registry (PowerShell, VS Code, Git, Office, Adobe, etc.)
        console.log('[GeminiLive] Connector tool:', fc.name);
        const result = await window.eve.connectors.callTool(fc.name, fc.args || {});
        if (result.error) {
          resultText = `Error: ${result.error}`;
        } else {
          resultText = result.result || 'Done.';
        }
      } else if (ctx.mcpToolNamesRef.current.has(fc.name)) {
        // Route to MCP servers (Desktop Commander, user-added servers, etc.)
        console.log('[GeminiLive] MCP tool:', fc.name);
        try {
          const mcpResult = await window.eve.mcp.callTool(fc.name, fc.args || {});
          // MCP returns content array — extract text
          if (Array.isArray(mcpResult)) {
            resultText = mcpResult
              .map((c: any) => (c.type === 'text' ? c.text : JSON.stringify(c)))
              .join('\n');
          } else {
            resultText = typeof mcpResult === 'string' ? mcpResult : JSON.stringify(mcpResult);
          }
        } catch (mcpErr: unknown) {
          const mcpMsg = mcpErr instanceof Error ? mcpErr.message : String(mcpErr);
          resultText = `MCP Error: ${mcpMsg}`;
        }
      } else {
        // Route to desktop tools (includes file system, keyboard sim, screen reading)
        const result = await window.eve.desktop.callTool(fc.name, fc.args || {});
        if (result.error) {
          resultText = `Error: ${result.error}`;
        } else {
          resultText = result.result || 'Done.';
        }
      }
    }

    const durationMs = Date.now() - toolStartTime;
    ctx.optionsRef.current.onToolEnd?.(actionId, fc.name, true);
    // Record tool call metrics for session health
    try { window.eve.sessionHealth.recordToolCall(fc.name, true, durationMs); } catch { /* ignored */ }
    return {
      response: { result: resultText },
      id: fc.id,
    };
  } catch (err: unknown) {
    success = false;
    const durationMs = Date.now() - toolStartTime;
    const msg = err instanceof Error ? err.message : String(err);
    ctx.optionsRef.current.onToolEnd?.(actionId, fc.name, false);
    // Record tool failure for session health
    try {
      window.eve.sessionHealth.recordToolCall(fc.name, false, durationMs);
      window.eve.sessionHealth.recordError(fc.name, msg);
    } catch { /* ignored */ }
    console.error(`[GeminiLive] Tool "${fc.name}" failed (${durationMs}ms):`, msg);
    return { response: { error: `Tool error (${fc.name}): ${msg}` }, id: fc.id };
  }
}
