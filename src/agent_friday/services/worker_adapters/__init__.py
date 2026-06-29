"""Worker adapter package — pluggable backends for the orchestration engine."""
from agent_friday.services.worker_adapters.base import BaseAdapter, WorkerStatus  # noqa: F401
from agent_friday.services.worker_adapters.ollama_adapter import OllamaAdapter, _probe_ollama  # noqa: F401
from agent_friday.services.worker_adapters.claude_code_adapter import ClaudeCodeAdapter  # noqa: F401
