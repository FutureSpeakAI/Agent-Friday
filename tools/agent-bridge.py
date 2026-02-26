#!/usr/bin/env python3
"""
Agent Friday — Python Tool Bridge
Accepts JSON commands over stdin, executes SOC or browser-use tasks,
returns JSON results over stdout.

Protocol: One JSON object per line (JSONL).
Input:  {"id": "...", "tool": "soc"|"browser", "action": "...", "params": {...}}
Output: {"id": "...", "status": "ok"|"error", "result": ..., "error": "..."}
"""

import json
import sys
import os
import asyncio
import traceback
import base64
import importlib.util
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
TOOLS_DIR = Path(__file__).parent
SOC_DIR = TOOLS_DIR / "self-operating-computer"
BROWSER_USE_DIR = TOOLS_DIR / "browser-use"

# Add to Python path
sys.path.insert(0, str(SOC_DIR))
sys.path.insert(0, str(BROWSER_USE_DIR))


def send_response(msg_id: str, status: str, result=None, error=None):
    """Send a JSON response line to stdout."""
    resp = {"id": msg_id, "status": status}
    if result is not None:
        resp["result"] = result
    if error is not None:
        resp["error"] = error
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def send_event(msg_id: str, event_type: str, data=None):
    """Send a streaming event (progress update) to stdout."""
    resp = {"id": msg_id, "event": event_type}
    if data is not None:
        resp["data"] = data
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


# ── Self-Operating Computer Integration ────────────────────────────────────

async def handle_soc(msg_id: str, action: str, params: dict):
    """Handle self-operating-computer commands."""
    if action == "operate":
        objective = params.get("objective", "")
        model = params.get("model", "gpt-4-with-ocr")
        max_steps = params.get("max_steps", 10)

        send_event(msg_id, "started", {"objective": objective, "model": model})

        try:
            from operate.operate import main as soc_main
            from operate.config import Config

            # Configure
            config = Config()
            config.verbose = params.get("verbose", False)

            # Run the self-operating loop
            result = soc_main(
                model=model,
                terminal_prompt=objective,
                voice_mode=False,
                verbose=config.verbose,
            )

            send_response(msg_id, "ok", result={"completed": True, "summary": str(result)})
        except ImportError as e:
            send_response(msg_id, "error", error=f"SOC not installed: {e}. Run: pip install -e tools/self-operating-computer")
        except Exception as e:
            send_response(msg_id, "error", error=f"{type(e).__name__}: {e}")

    elif action == "screenshot":
        """Take a screenshot and return as base64."""
        try:
            import pyautogui
            from io import BytesIO

            screenshot = pyautogui.screenshot()
            buffer = BytesIO()
            screenshot.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            send_response(msg_id, "ok", result={
                "image": b64,
                "width": screenshot.width,
                "height": screenshot.height,
            })
        except ImportError:
            send_response(msg_id, "error", error="pyautogui not installed. Run: pip install pyautogui")
        except Exception as e:
            send_response(msg_id, "error", error=str(e))

    elif action == "click":
        """Click at screen coordinates."""
        try:
            import pyautogui
            x = params.get("x", 0)
            y = params.get("y", 0)
            pyautogui.click(x, y)
            send_response(msg_id, "ok", result={"clicked": True, "x": x, "y": y})
        except Exception as e:
            send_response(msg_id, "error", error=str(e))

    elif action == "type":
        """Type text."""
        try:
            import pyautogui
            text = params.get("text", "")
            pyautogui.write(text, interval=0.02)
            send_response(msg_id, "ok", result={"typed": True, "length": len(text)})
        except Exception as e:
            send_response(msg_id, "error", error=str(e))

    elif action == "press":
        """Press keyboard keys."""
        try:
            import pyautogui
            keys = params.get("keys", [])
            if isinstance(keys, str):
                keys = [keys]
            pyautogui.hotkey(*keys)
            send_response(msg_id, "ok", result={"pressed": True, "keys": keys})
        except Exception as e:
            send_response(msg_id, "error", error=str(e))

    elif action == "check":
        """Check if SOC dependencies are installed."""
        deps = {}
        for pkg in ["pyautogui", "easyocr", "PIL", "cv2"]:
            try:
                importlib.import_module(pkg)
                deps[pkg] = True
            except ImportError:
                deps[pkg] = False

        send_response(msg_id, "ok", result={"dependencies": deps})

    else:
        send_response(msg_id, "error", error=f"Unknown SOC action: {action}")


# ── Browser-Use Integration ────────────────────────────────────────────────

async def handle_browser(msg_id: str, action: str, params: dict):
    """Handle browser-use commands."""
    if action == "run":
        task = params.get("task", "")
        model_name = params.get("model", "gpt-4o")
        max_steps = params.get("max_steps", 20)
        headless = params.get("headless", False)

        send_event(msg_id, "started", {"task": task, "model": model_name})

        try:
            from browser_use import Agent as BrowserAgent, Browser, BrowserConfig
            from langchain_openai import ChatOpenAI

            # Configure browser
            browser = Browser(config=BrowserConfig(headless=headless))

            # Configure LLM
            llm = ChatOpenAI(model=model_name)

            # Create and run agent
            agent = BrowserAgent(
                task=task,
                llm=llm,
                browser=browser,
                max_steps=max_steps,
            )

            result = await agent.run()
            await browser.close()

            send_response(msg_id, "ok", result={
                "completed": True,
                "final_result": str(result),
                "steps": getattr(result, 'n_steps', 0),
            })
        except ImportError as e:
            send_response(msg_id, "error", error=f"browser-use not installed: {e}. Run: pip install -e tools/browser-use")
        except Exception as e:
            send_response(msg_id, "error", error=f"{type(e).__name__}: {e}")

    elif action == "check":
        """Check if browser-use dependencies are installed."""
        deps = {}
        for pkg in ["browser_use", "langchain_openai", "playwright"]:
            try:
                importlib.import_module(pkg)
                deps[pkg] = True
            except ImportError:
                deps[pkg] = False

        send_response(msg_id, "ok", result={"dependencies": deps})

    else:
        send_response(msg_id, "error", error=f"Unknown browser action: {action}")


# ── Main Loop ──────────────────────────────────────────────────────────────

async def process_message(line: str):
    """Parse and dispatch a single JSONL command."""
    try:
        msg = json.loads(line.strip())
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Invalid JSON: {e}\n")
        return

    msg_id = msg.get("id", "unknown")
    tool = msg.get("tool", "")
    action = msg.get("action", "")
    params = msg.get("params", {})

    try:
        if tool == "soc":
            await handle_soc(msg_id, action, params)
        elif tool == "browser":
            await handle_browser(msg_id, action, params)
        elif tool == "ping":
            send_response(msg_id, "ok", result={"pong": True, "pid": os.getpid()})
        elif tool == "exit":
            send_response(msg_id, "ok", result={"exiting": True})
            sys.exit(0)
        else:
            send_response(msg_id, "error", error=f"Unknown tool: {tool}")
    except Exception as e:
        send_response(msg_id, "error", error=f"Unhandled: {type(e).__name__}: {e}\n{traceback.format_exc()}")


def main():
    """Read JSONL commands from stdin, process each one."""
    # Signal ready
    send_response("_init", "ok", result={"ready": True, "pid": os.getpid()})

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            loop.run_until_complete(process_message(line))
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
