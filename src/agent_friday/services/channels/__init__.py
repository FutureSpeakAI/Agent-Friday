"""
Agent Friday — Channel integration.

Connect Friday to messaging platforms (Discord, Telegram, …). Every inbound
message runs through the SAME agent loop + egress gate as the chat UI, so a
channel is just another front-end — never a bypass of governance.

Public surface: ``manager`` (registry + lifecycle + the shared inbound funnel).
Adapters live alongside: ``telegram_bridge``, ``discord_bridge``.
"""
from agent_friday.services.channels import manager  # noqa: F401

__all__ = ["manager"]
