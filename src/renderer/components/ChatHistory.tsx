import React, { useRef, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage } from '../store';

interface ChatHistoryProps {
  messages: ChatMessage[];
}

function getModelInfo(model?: string): { label: string; color: string; rgb: string } {
  if (!model) return { label: 'Friday', color: '#a855f7', rgb: '168,85,247' };
  if (model.includes('claude')) return { label: 'Claude', color: '#d4a574', rgb: '212,165,116' };
  return { label: 'Friday', color: '#a855f7', rgb: '168,85,247' };
}

/** Inline copy button for code blocks */
function CodeBlock({ children, className }: { children: React.ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false);
  const lang = className?.replace('language-', '') || '';
  const code = String(children).replace(/\n$/, '');

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={mdStyles.codeBlockWrapper}>
      <div style={mdStyles.codeHeader}>
        <span style={mdStyles.codeLang}>{lang}</span>
        <button onClick={handleCopy} style={mdStyles.copyBtn}>
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <pre style={mdStyles.pre}>
        <code style={mdStyles.code}>{code}</code>
      </pre>
    </div>
  );
}

/** Markdown components override map */
const markdownComponents = {
  code({ className, children, ...props }: any) {
    const isBlock = className || String(children).includes('\n');
    if (isBlock) {
      return <CodeBlock className={className}>{children}</CodeBlock>;
    }
    return <code style={mdStyles.inlineCode} {...props}>{children}</code>;
  },
  p({ children }: any) {
    return <p style={mdStyles.paragraph}>{children}</p>;
  },
  ul({ children }: any) {
    return <ul style={mdStyles.ul}>{children}</ul>;
  },
  ol({ children }: any) {
    return <ol style={mdStyles.ol}>{children}</ol>;
  },
  li({ children }: any) {
    return <li style={mdStyles.li}>{children}</li>;
  },
  h1({ children }: any) {
    return <h1 style={{ ...mdStyles.heading, fontSize: 18 }}>{children}</h1>;
  },
  h2({ children }: any) {
    return <h2 style={{ ...mdStyles.heading, fontSize: 16 }}>{children}</h2>;
  },
  h3({ children }: any) {
    return <h3 style={{ ...mdStyles.heading, fontSize: 14 }}>{children}</h3>;
  },
  blockquote({ children }: any) {
    return <blockquote style={mdStyles.blockquote}>{children}</blockquote>;
  },
  a({ href, children }: any) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" style={mdStyles.link}>
        {children}
      </a>
    );
  },
  table({ children }: any) {
    return (
      <div style={mdStyles.tableWrapper}>
        <table style={mdStyles.table}>{children}</table>
      </div>
    );
  },
  th({ children }: any) {
    return <th style={mdStyles.th}>{children}</th>;
  },
  td({ children }: any) {
    return <td style={mdStyles.td}>{children}</td>;
  },
  strong({ children }: any) {
    return <strong style={mdStyles.strong}>{children}</strong>;
  },
};

/** Maximum number of messages to render without explicit user request. */
const VISIBLE_LIMIT = 100;

export default function ChatHistory({ messages }: ChatHistoryProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showAll, setShowAll] = useState(false);

  // Reset showAll when messages are cleared (e.g. new conversation)
  useEffect(() => {
    if (messages.length <= VISIBLE_LIMIT) setShowAll(false);
  }, [messages.length]);

  const visibleMessages = showAll ? messages : messages.slice(-VISIBLE_LIMIT);
  const hiddenCount = messages.length - visibleMessages.length;

  // Inject thinking-dot animation keyframes once
  useEffect(() => {
    if (!document.getElementById('thinking-dot-keyframes')) {
      const style = document.createElement('style');
      style.id = 'thinking-dot-keyframes';
      style.textContent = `
        @keyframes thinkingDotPulse {
          0%, 80%, 100% { opacity: 0.25; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1.1); }
        }
      `;
      document.head.appendChild(style);
    }
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.headerIcon}>⬡</span>
        <span style={styles.headerText}>Conversation</span>
        <span style={styles.count}>{messages.length}</span>
      </div>
      <div ref={scrollRef} style={styles.messages}>
        {messages.length === 0 && (
          <div style={styles.empty}>
            <div style={styles.emptyIcon}>◇</div>
            <div style={styles.emptyText}>No messages yet</div>
            <div style={styles.emptyHint}>Type a message to begin</div>
          </div>
        )}
        {hiddenCount > 0 && (
          <button
            onClick={() => setShowAll(true)}
            style={styles.showEarlierBtn}
          >
            Show {hiddenCount} earlier message{hiddenCount === 1 ? '' : 's'}
          </button>
        )}
        {visibleMessages.map((msg) => {
          const info = getModelInfo(msg.model);
          return (
            <div key={msg.id} className="hover-lift" style={styles.messageRow}>
              <div
                style={{
                  ...styles.roleTag,
                  color: msg.role === 'user' ? '#00f0ff' : info.color,
                  borderColor:
                    msg.role === 'user'
                      ? 'rgba(0, 240, 255, 0.2)'
                      : `rgba(${info.rgb}, 0.2)`,
                  background:
                    msg.role === 'user'
                      ? 'rgba(0, 240, 255, 0.06)'
                      : `rgba(${info.rgb}, 0.06)`,
                }}
              >
                {msg.role === 'user' ? 'You' : info.label}
              </div>
              <div style={styles.messageContent}>
                {msg.role === 'user' ? (
                  msg.content
                ) : msg.pending && !msg.content ? (
                  <span style={styles.thinkingDots}>
                    <span style={styles.dot1} />
                    <span style={styles.dot2} />
                    <span style={styles.dot3} />
                  </span>
                ) : (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={markdownComponents}
                  >
                    {msg.content}
                  </ReactMarkdown>
                )}
              </div>
              <div style={styles.timestamp}>
                {new Date(msg.timestamp).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* --- Layout styles --- */
const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '16px 20px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  headerIcon: {
    color: '#00f0ff',
    fontSize: 16,
  },
  headerText: {
    fontSize: 13,
    fontWeight: 600,
    color: '#e0e0e8',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
  },
  count: {
    marginLeft: 'auto',
    fontSize: 11,
    color: '#555568',
    background: 'rgba(255,255,255,0.05)',
    padding: '2px 8px',
    borderRadius: 10,
  },
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: '12px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  empty: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
    gap: 8,
    opacity: 0.4,
  },
  emptyIcon: {
    fontSize: 32,
    color: '#00f0ff',
  },
  emptyText: {
    fontSize: 14,
    fontWeight: 500,
  },
  emptyHint: {
    fontSize: 12,
    color: '#555568',
  },
  showEarlierBtn: {
    display: 'block',
    width: '100%',
    padding: '8px 0',
    margin: '0 0 8px 0',
    background: 'rgba(0, 240, 255, 0.06)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 6,
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 500,
    cursor: 'pointer',
    textAlign: 'center' as const,
    transition: 'background 0.15s',
  },
  messageRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    padding: '8px 10px',
    borderRadius: 8,
    borderLeft: '2px solid transparent',
    transition: 'border-color 0.2s, background 0.2s',
  },
  roleTag: {
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    padding: '3px 8px',
    borderRadius: 4,
    border: '1px solid',
    alignSelf: 'flex-start',
  },
  messageContent: {
    fontSize: 13,
    lineHeight: 1.6,
    color: '#d0d0d8',
    wordBreak: 'break-word' as const,
  },
  timestamp: {
    fontSize: 10,
    color: '#444458',
  },
  thinkingDots: {
    display: 'inline-flex',
    gap: 4,
    alignItems: 'center',
    height: 20,
  },
  dot1: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#a855f7',
    animation: 'thinkingDotPulse 1.4s ease-in-out infinite',
    animationDelay: '0s',
  } as React.CSSProperties,
  dot2: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#a855f7',
    animation: 'thinkingDotPulse 1.4s ease-in-out infinite',
    animationDelay: '0.2s',
  } as React.CSSProperties,
  dot3: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#a855f7',
    animation: 'thinkingDotPulse 1.4s ease-in-out infinite',
    animationDelay: '0.4s',
  } as React.CSSProperties,
};

/* --- Markdown-specific styles --- */
const mdStyles: Record<string, React.CSSProperties> = {
  paragraph: {
    margin: '4px 0',
    lineHeight: 1.6,
  },
  strong: {
    color: '#e8e8ec',
    fontWeight: 600,
  },
  heading: {
    color: '#e8e8ec',
    fontWeight: 700,
    margin: '12px 0 4px 0',
    lineHeight: 1.3,
  },
  ul: {
    margin: '4px 0',
    paddingLeft: 20,
  },
  ol: {
    margin: '4px 0',
    paddingLeft: 20,
  },
  li: {
    margin: '2px 0',
    lineHeight: 1.5,
  },
  inlineCode: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.12)',
    borderRadius: 4,
    padding: '1px 5px',
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
    color: '#00f0ff',
  },
  codeBlockWrapper: {
    margin: '8px 0',
    borderRadius: 8,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.08)',
    background: 'rgba(0,0,0,0.3)',
  },
  codeHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '6px 12px',
    background: 'rgba(255,255,255,0.04)',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  codeLang: {
    fontSize: 10,
    fontWeight: 600,
    color: '#666680',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
  },
  copyBtn: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 4,
    color: '#00f0ff',
    fontSize: 10,
    fontWeight: 600,
    padding: '2px 8px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  pre: {
    margin: 0,
    padding: '12px 14px',
    overflow: 'auto',
    maxHeight: 400,
  },
  code: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    lineHeight: 1.5,
    color: '#d0d0d8',
  },
  blockquote: {
    borderLeft: '3px solid rgba(168, 85, 247, 0.4)',
    margin: '8px 0',
    padding: '4px 12px',
    color: '#aaa0b8',
    background: 'rgba(168, 85, 247, 0.04)',
    borderRadius: '0 4px 4px 0',
  },
  link: {
    color: '#00f0ff',
    textDecoration: 'none',
    borderBottom: '1px solid rgba(0, 240, 255, 0.3)',
    transition: 'border-color 0.15s',
  },
  tableWrapper: {
    overflowX: 'auto',
    margin: '8px 0',
  },
  table: {
    borderCollapse: 'collapse',
    width: '100%',
    fontSize: 12,
  },
  th: {
    textAlign: 'left',
    padding: '6px 10px',
    borderBottom: '1px solid rgba(255,255,255,0.12)',
    color: '#e0e0e8',
    fontWeight: 600,
    fontSize: 11,
  },
  td: {
    padding: '6px 10px',
    borderBottom: '1px solid rgba(255,255,255,0.05)',
    color: '#c0c0c8',
  },
};
