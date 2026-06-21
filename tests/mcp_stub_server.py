"""A minimal MCP server stub for testing mcp_client.py.

Speaks just enough of the protocol over stdio (newline-delimited JSON-RPC) to
exercise the client: initialize, tools/list, tools/call. No external deps.
"""
import json
import sys


TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the provided text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "boom",
        "description": "Always returns an error result.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub", "version": "0.1"},
            }})
        elif method == "notifications/initialized":
            continue  # notification, no reply
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo":
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": "echo: " + str(args.get("text", ""))}],
                }})
            elif name == "boom":
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": "kaboom"}],
                    "isError": True,
                }})
            else:
                _send({"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32601, "message": f"unknown tool {name}"}})
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid,
                   "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
