/**
 * builtin-agents.ts — Pre-built background agents for EVE OS.
 *
 * Each agent is a self-contained task that runs in the background,
 * powered by Claude Sonnet for reasoning and web search for research.
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { settingsManager } from '../settings';

const researchAgent: AgentDefinition = {
  name: 'research',
  description:
    'Deep research on a topic. Searches the web, synthesises findings, and returns a comprehensive briefing with sources.',
  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    const topic = String(input.topic || input.query || '');
    if (!topic) throw new Error('No topic provided');

    ctx.log(`Researching: "${topic}"`);
    ctx.setProgress(10);

    // Step 1: Generate search queries
    const queriesPrompt = `Generate 3-5 specific web search queries to thoroughly research this topic: "${topic}"

Return ONLY a JSON array of strings, no other text.
Example: ["query 1", "query 2", "query 3"]`;

    const queriesRaw = await ctx.callClaude(queriesPrompt, 256);
    let queries: string[];
    try {
      const match = queriesRaw.match(/\[[\s\S]*\]/);
      queries = match ? JSON.parse(match[0]) : [topic];
    } catch {
      queries = [topic];
    }

    ctx.log(`Generated ${queries.length} search queries`);
    ctx.setProgress(25);

    if (ctx.isCancelled()) return 'Cancelled';

    // Step 2: Search using Gemini grounding (via fetch to Gemini API)
    const { settingsManager } = require('../settings');
    const apiKey = settingsManager.getGeminiApiKey();
    const searchResults: string[] = [];

    for (let i = 0; i < queries.length; i++) {
      if (ctx.isCancelled()) return 'Cancelled';

      const query = queries[i];
      ctx.log(`Searching: "${query}"`);

      try {
        const response = await fetch(
          'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'x-goog-api-key': apiKey,
            },
            body: JSON.stringify({
              contents: [{ parts: [{ text: query }] }],
              tools: [{ googleSearch: {} }],
            }),
          }
        );

        if (response.ok) {
          const data = await response.json();
          const text = data.candidates?.[0]?.content?.parts
            ?.map((p: any) => p.text)
            .filter(Boolean)
            .join('\n');
          if (text) searchResults.push(`## Search: "${query}"\n${text}`);
        }
      } catch (err) {
        ctx.log(`Search failed for "${query}": ${err}`);
      }

      ctx.setProgress(25 + Math.round((i + 1) / queries.length * 40));
    }

    if (searchResults.length === 0) {
      ctx.log('No search results found, using Claude knowledge only');
    }

    ctx.setProgress(70);
    ctx.log('Synthesising findings...');

    // Step 3: Synthesise with Claude
    const synthesisPrompt = `You are a research analyst preparing a briefing for a busy executive.

TOPIC: ${topic}

SEARCH RESULTS:
${searchResults.join('\n\n') || 'No web results available — use your own knowledge.'}

Write a comprehensive but concise research briefing (500-800 words) that:
1. Summarises the key findings
2. Highlights what matters most for someone in AI/tech leadership
3. Notes any controversies or caveats
4. Ends with 2-3 actionable takeaways

Use clear headers and bullet points. Be direct and insightful.`;

    const briefing = await ctx.callClaude(synthesisPrompt, 2048);
    ctx.setProgress(100);
    ctx.log('Research complete');

    return briefing;
  },
};

const summarizeAgent: AgentDefinition = {
  name: 'summarize',
  description:
    'Summarise a long text, document, or set of notes into a concise briefing with key points.',
  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    const text = String(input.text || input.content || '');
    const style = String(input.style || 'executive briefing');
    if (!text) throw new Error('No text provided to summarise');

    ctx.log(`Summarising ${text.length} characters as "${style}"`);
    ctx.setProgress(20);

    if (ctx.isCancelled()) return 'Cancelled';

    const prompt = `Summarise the following text as a ${style}. Be concise, highlight key points, and use bullet points where appropriate.

TEXT:
${text.slice(0, 12000)}

${text.length > 12000 ? `[Truncated — original was ${text.length} characters]` : ''}

Provide a clear, well-structured summary.`;

    const result = await ctx.callClaude(prompt, 1500);
    ctx.setProgress(100);
    ctx.log('Summary complete');
    return result;
  },
};

const codeReviewAgent: AgentDefinition = {
  name: 'code-review',
  description:
    'Review code for bugs, security issues, performance, and best practices. Provides actionable feedback.',
  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    const code = String(input.code || '');
    const language = String(input.language || 'auto-detect');
    const focus = String(input.focus || 'bugs, security, performance, and best practices');
    if (!code) throw new Error('No code provided for review');

    ctx.log(`Reviewing ${code.split('\n').length} lines of ${language} code`);
    ctx.setProgress(20);

    if (ctx.isCancelled()) return 'Cancelled';

    const prompt = `You are a senior software engineer conducting a thorough code review. Language: ${language}. Focus areas: ${focus}.

CODE:
\`\`\`
${code.slice(0, 15000)}
\`\`\`

Provide a detailed code review covering:
1. **Bugs & Errors** — Logic errors, edge cases, null/undefined risks
2. **Security** — Injection, XSS, auth issues, data exposure
3. **Performance** — Unnecessary loops, memory leaks, optimisation opportunities
4. **Best Practices** — Naming, structure, patterns, maintainability
5. **Suggestions** — Specific improvements with code examples

Rate overall quality: Excellent / Good / Needs Work / Critical Issues.
Be direct and actionable.`;

    const result = await ctx.callClaude(prompt, 3000);
    ctx.setProgress(100);
    ctx.log('Review complete');
    return result;
  },
};

const draftEmailAgent: AgentDefinition = {
  name: 'draft-email',
  description:
    'Draft a professional email based on context, tone, and key points. Returns the draft to clipboard.',
  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    const to = String(input.to || input.recipient || 'the recipient');
    const subject = String(input.subject || input.topic || '');
    const keyPoints = String(input.key_points || input.points || input.content || '');
    const tone = String(input.tone || 'professional but warm');
    if (!subject && !keyPoints) throw new Error('Need a subject or key points for the email');

    ctx.log(`Drafting email to ${to} about "${subject || keyPoints.slice(0, 50)}..."`);
    ctx.setProgress(30);

    if (ctx.isCancelled()) return 'Cancelled';

    const prompt = `Draft a professional email with these parameters:

TO: ${to}
SUBJECT: ${subject}
KEY POINTS: ${keyPoints}
TONE: ${tone}

Write a clear, well-structured email that sounds natural (not template-like). Include subject line.
The sender is ${settingsManager.getAgentConfig().userName || 'the user'}.`;

    const result = await ctx.callClaude(prompt, 1000);
    ctx.setProgress(100);
    ctx.log('Email drafted');
    return result;
  },
};

// Import orchestrate agent (multi-agent task decomposition)
import { orchestrateAgent } from './orchestrator';

export const builtinAgents: AgentDefinition[] = [
  researchAgent,
  summarizeAgent,
  codeReviewAgent,
  draftEmailAgent,
  orchestrateAgent,
];
