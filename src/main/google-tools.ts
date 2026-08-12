/**
 * google-tools.ts — FR-6: LLM tool declarations for query_calendar, search_email,
 * and draft_email.
 *
 * Always advertised regardless of whether Stephen has authorized Google yet —
 * calling them when unauthorized returns an honest "not connected" message
 * (see executeToolCall in local-conversation.ts) rather than being silently
 * absent, which is what let personality.ts's prose description of
 * capabilities go unchecked before (see V1 findings in
 * dev/friday-orchestrator-integrity-spec.md). A model that tries to use
 * these tools always gets a real, honest answer either way.
 */

export const GOOGLE_TOOL_DECLARATIONS = [
  {
    name: 'query_calendar',
    description: "Read the user's upcoming Google Calendar events (read-only). Returns real event data if connected, or an honest 'not connected' message if Google hasn't been authorized yet in Settings.",
    parameters: {
      type: 'object',
      properties: {
        count: { type: 'number', description: 'Maximum number of upcoming events to return (default 5).' },
      },
    },
  },
  {
    name: 'search_email',
    description: "Search the user's Gmail (read-only) using Gmail search syntax (e.g. 'is:unread', 'from:someone@example.com', 'subject:invoice'). Returns real results if connected, or an honest 'not connected' message if Google hasn't been authorized yet in Settings.",
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Gmail search query.' },
        maxResults: { type: 'number', description: 'Maximum results to return (default 10, max 25).' },
      },
      required: ['query'],
    },
  },
  {
    name: 'draft_email',
    description: "Create a Gmail draft for the user to review and send themselves. This NEVER sends email — the connected account has no send permission, only draft creation. Returns a link the user can open to review and send it.",
    parameters: {
      type: 'object',
      properties: {
        to: { type: 'string', description: 'Recipient email address.' },
        subject: { type: 'string', description: 'Email subject line.' },
        body: { type: 'string', description: 'Email body text.' },
      },
      required: ['to', 'subject', 'body'],
    },
  },
];
