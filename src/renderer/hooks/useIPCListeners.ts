import { useEffect } from 'react';
import { useAppStore } from '../store';
import type { ActionItem } from '../components/ActionFeed';
import {
  playNotificationBell,
} from '../audio/sound-effects';

/**
 * Registers all IPC event listeners that feed data into the app store.
 * Covers: scheduler, predictor, notifications, clipboard, agents,
 * agent voice, meetings, confirmations, code proposals, and API health push.
 */
export function useIPCListeners(
  sendText: (text: string) => void,
) {
  const setActiveActions = useAppStore((s) => s.setActiveActions);
  const setPendingConfirmation = useAppStore((s) => s.setPendingConfirmation);
  const setCodeProposal = useAppStore((s) => s.setCodeProposal);
  const setApiStatus = useAppStore((s) => s.setApiStatus);
  const setMessages = useAppStore((s) => s.setMessages);

  // Listen for scheduler task-fired events
  useEffect(() => {
    const cleanup = window.eve.scheduler.onTaskFired((task) => {
      console.log('[Agent] Task fired:', task.description);
      playNotificationBell();

      if (task.action === 'remind') {
        sendText(
          `[SYSTEM REMINDER — speak this naturally to the user] Reminder: ${task.payload}`
        );
      } else if (task.action === 'launch_app') {
        window.eve.desktop
          .callTool('launch_app', { app_name: task.payload })
          .then(() => {
            sendText(
              `[SYSTEM] I just launched ${task.payload} as scheduled. Let the user know briefly.`
            );
          })
          .catch((err) => console.warn('[Friday] Scheduled launch failed:', err));
      } else if (task.action === 'run_command') {
        window.eve.desktop
          .callTool('run_command', { command: task.payload })
          .then((result) => {
            sendText(
              `[SYSTEM] Scheduled command executed: ${task.description}. Result: ${result.result || result.error || 'Done'}`
            );
          })
          .catch((err) => console.warn('[Friday] Scheduled command failed:', err));
      }
    });

    return cleanup;
  }, [sendText]);

  // Listen for predictive suggestions
  useEffect(() => {
    const cleanup = window.eve.predictor.onSuggestion((suggestion) => {
      console.log(`[Friday] Prediction: ${suggestion.type} (${suggestion.confidence})`);
      playNotificationBell();

      sendText(
        `[SYSTEM SUGGESTION — speak this naturally in character, keep it brief and charming] ${suggestion.message}`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for captured notifications
  useEffect(() => {
    const cleanup = window.eve.notifications.onCaptured((notif) => {
      console.log(`[Friday] Notification captured: ${notif.app} — ${notif.title}`);

      sendText(
        `[SYSTEM NOTIFICATION from ${notif.app}] Title: ${notif.title}${notif.body ? `. Body: ${notif.body}` : ''}. Mention this naturally and briefly — don't read it out verbatim.`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for clipboard changes
  useEffect(() => {
    const cleanup = window.eve.clipboard.onChanged((entry) => {
      if (entry.type === 'empty') return;

      sendText(
        `[SYSTEM CLIPBOARD — ${entry.type.toUpperCase()}] User just copied: "${entry.preview}". You don't need to mention this unless it's relevant to the conversation or they ask about it.`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for agent task completions
  useEffect(() => {
    const cleanup = window.eve.agents.onUpdate((task) => {
      if (task.status === 'completed' && task.result) {
        const resultText = String(task.result);
        const preview = resultText.length > 300 ? resultText.slice(0, 300) + '...' : resultText;
        sendText(
          `[SYSTEM — AGENT COMPLETE] Background task "${task.description}" (${task.agentType}) just finished. Result preview: ${preview}. Mention this proactively if relevant.`
        );
      } else if (task.status === 'failed' && task.error) {
        sendText(
          `[SYSTEM — AGENT FAILED] Background task "${task.description}" failed: ${task.error}. Let the user know briefly.`
        );
      }

      if (task.status === 'running') {
        setActiveActions((prev) => {
          const existing = prev.find((a) => a.id === task.id);
          if (existing) {
            return prev.map((a) =>
              a.id === task.id
                ? { ...a, progress: task.progress, windowTitle: task.windowTitle }
                : a
            );
          }
          return [
            ...prev,
            {
              id: task.id,
              name: task.agentType,
              status: 'running' as const,
              startTime: task.startedAt || Date.now(),
              isAgent: true,
              description: task.description,
              progress: task.progress,
              windowTitle: task.windowTitle,
            },
          ];
        });
      }

      if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
        setActiveActions((prev) =>
          prev.map((a) =>
            a.id === task.id
              ? ({ ...a, status: task.status === 'completed' ? 'success' : 'error' } as ActionItem)
              : a
          )
        );
        setTimeout(() => {
          setActiveActions((prev) => prev.filter((a) => a.id !== task.id));
        }, 5000);
      }
    });

    return cleanup;
  }, [sendText, setActiveActions]);

  // Listen for sub-agent voice delivery (ElevenLabs TTS)
  useEffect(() => {
    const cleanup = window.eve.agents.onSpeak((data) => {
      console.log(`[Agent] ${data.personaName || 'Unknown'} (${data.personaRole || 'agent'}) speaking — ~${Math.round(data.durationEstimate || 0)}s`);

      try {
        const binaryString = atob(data.audioBase64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }

        const blob = new Blob([bytes.buffer], { type: data.contentType });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);

        const speakId = `speak-${data.taskId}`;
        setActiveActions((prev) => [
          ...prev,
          {
            id: speakId,
            name: `${data.personaName} speaking`,
            status: 'running' as const,
            startTime: Date.now(),
            isAgent: true,
            description: (data.spokenText || '').slice(0, 100) + ((data.spokenText || '').length > 100 ? '...' : ''),
          },
        ]);

        audio.onended = () => {
          URL.revokeObjectURL(url);
          setActiveActions((prev) =>
            prev.map((a) =>
              a.id === speakId ? ({ ...a, status: 'success' } as ActionItem) : a
            )
          );
          setTimeout(() => {
            setActiveActions((prev) => prev.filter((a) => a.id !== speakId));
          }, 3000);
        };

        audio.onerror = () => {
          URL.revokeObjectURL(url);
          console.warn(`[Agent] Failed to play ${data.personaName}'s audio`);
          setActiveActions((prev) => prev.filter((a) => a.id !== speakId));
        };

        audio.play().catch((err) => {
          console.warn(`[Agent] Audio play failed for ${data.personaName}:`, err);
          URL.revokeObjectURL(url);
          setActiveActions((prev) => prev.filter((a) => a.id !== speakId));
        });
      } catch (err) {
        console.warn('[Agent] Failed to decode agent voice audio:', err);
      }
    });

    return cleanup;
  }, [setActiveActions]);

  // Listen for meeting briefings
  useEffect(() => {
    const cleanup = window.eve.meetingPrep.onBriefing((briefing) => {
      console.log(`[Friday] Meeting briefing: "${briefing.eventTitle}" in ${briefing.minutesUntil}m`);

      const attendeeInfo = (briefing.attendeeContext || [])
        .map((a) => {
          const parts = [a.name];
          if (a.memories?.length > 0) parts.push(`(${a.memories.join('; ')})`);
          return parts.join(' ');
        })
        .join(', ');

      const projects = briefing.relevantProjects || [];
      const topics = briefing.suggestedTopics || [];

      sendText(
        `[MEETING BRIEFING] "${briefing.eventTitle}" starts in ${briefing.minutesUntil} minutes.` +
        (attendeeInfo ? ` Attendees: ${attendeeInfo}.` : '') +
        (projects.length > 0 ? ` Related projects: ${projects.join(', ')}.` : '') +
        (topics.length > 0 ? ` Topics: ${topics.slice(0, 3).join(', ')}.` : '') +
        ` Mention this naturally — give the user a heads-up about the meeting and any useful context about the attendees.`
      );
    });

    return cleanup;
  }, [sendText]);

  // Listen for desktop tool confirmation requests
  useEffect(() => {
    const cleanup = window.eve.confirmation.onRequest((req) => {
      setPendingConfirmation(req);
    });
    return cleanup;
  }, [setPendingConfirmation]);

  // Listen for self-improvement code proposals
  useEffect(() => {
    const cleanup = window.eve.selfImprove.onProposal((proposal) => {
      setCodeProposal(proposal);
    });
    return cleanup;
  }, [setCodeProposal]);

  // API health push — main process polls and pushes only on change
  useEffect(() => {
    const cleanup = window.eve.onApiHealthChange((health) => {
      setApiStatus((prev) => ({
        ...prev,
        gemini: prev.gemini === 'connected' ? 'connected' : (health.gemini as any) || prev.gemini,
        claude: (health.claude as any) || prev.claude,
        openrouter: (health.openrouter as any) || prev.openrouter,
        elevenlabs: (health.elevenlabs as any) || prev.elevenlabs,
      }));
    });
    return cleanup;
  }, [setApiStatus]);
}
