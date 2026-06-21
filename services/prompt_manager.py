"""
Agent Friday — Composable Prompt Manager
Inspired by patterns in Goose (Apache-2.0). All code is original.

Builds system prompts from pluggable keyed segments with priority and budget.
"""

class PromptSegment:
    def __init__(self, key: str, content: str, priority: int = 50, max_tokens: int = None):
        self.key = key
        self.content = content
        self.priority = priority  # Lower = higher priority (assembled first)
        self.max_tokens = max_tokens

    def __repr__(self):
        return f"<Segment:{self.key} p={self.priority}>"


class PromptManager:
    def __init__(self, total_budget: int = 8000):
        self._segments = {}
        self.total_budget = total_budget

    def set(self, key: str, content: str, priority: int = 50, max_tokens: int = None):
        self._segments[key] = PromptSegment(key, content, priority, max_tokens)

    def remove(self, key: str):
        self._segments.pop(key, None)

    def get(self, key: str) -> str:
        seg = self._segments.get(key)
        return seg.content if seg else ""

    def has(self, key: str) -> bool:
        return key in self._segments

    def build(self) -> str:
        sorted_segments = sorted(self._segments.values(), key=lambda s: s.priority)
        parts = []
        tokens_used = 0
        for seg in sorted_segments:
            if not seg.content.strip():
                continue
            # Rough token estimate (4 chars per token)
            seg_tokens = len(seg.content) // 4
            if seg.max_tokens:
                seg_tokens = min(seg_tokens, seg.max_tokens)
                content = seg.content[:seg.max_tokens * 4]
            else:
                content = seg.content
            if tokens_used + seg_tokens > self.total_budget:
                remaining = (self.total_budget - tokens_used) * 4
                if remaining > 100:
                    parts.append(content[:remaining])
                break
            parts.append(content)
            tokens_used += seg_tokens
        return "\n\n".join(parts)

    def list_segments(self) -> list:
        return [{"key": s.key, "priority": s.priority, "chars": len(s.content)}
                for s in sorted(self._segments.values(), key=lambda s: s.priority)]


# Standard segment keys
SEGMENT_KEYS = {
    "base_personality": 10,
    "claws": 15,
    "workspace_context": 20,
    "hints": 25,
    "connectors_status": 30,
    "emotional_tone": 35,
    "ambient_state": 40,
    "memory_context": 45,
    "session_continuity": 50,
    "tool_descriptions": 60,
    "active_tasks": 70,
}


def create_default_manager() -> PromptManager:
    """Create a PromptManager with standard Friday segments pre-registered."""
    pm = PromptManager(total_budget=12000)
    # Segments get populated by the chat pipeline at request time
    return pm
