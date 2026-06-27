import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.services.ambient_awareness import (
    get_ambient_state,
    record_signal,
)  # noqa: E501
from agent_friday.services.predictive_workspaces import (
    predict_workspaces,
    prewarm_predicted,
    record_workspace_usage,
)  # noqa: E501

ambient_bp = Blueprint('ambient', __name__)


# ═══ AMBIENT AWARENESS ════════════════════════════════════════
@ambient_bp.route('/api/ambient/state')
def ambient_state():
    """Live read of the user's working state (energy / focus / stress / flow),
    with behavior hints and a suggested holographic scene mood."""
    try:
        return jsonify({"status": "ok", "state": get_ambient_state()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══ PREDICTIVE WORKSPACES ════════════════════════════════════
@ambient_bp.route('/api/workspace/visit', methods=['POST'])
def workspace_visit():
    """Record that a workspace was opened. Feeds both the usage model (for
    predictions / pre-warming) and the ambient switch-rate signal."""
    data = request.get_json(silent=True) or {}
    ws = (data.get('workspace') or '').strip().lower()
    if not ws:
        return jsonify({"status": "error", "message": "workspace required"}), 400
    try:
        record_workspace_usage(ws)
    except Exception as e:
        print(f"  [ambient] usage record failed: {e}")
    try:
        record_signal('workspace_switch', workspace=ws)
    except Exception:
        pass
    return jsonify({"status": "ok", "workspace": ws})


@ambient_bp.route('/api/workspace/predictions')
def workspace_predictions():
    """Ranked workspaces Friday predicts the user will want right now. Drives the
    dock's 'Suggested' glow. Optional ?dow=&hour= override for previewing."""
    try:
        dow = request.args.get('dow', type=int)
        hour = request.args.get('hour', type=int)
        top = request.args.get('top', default=6, type=int)
        preds = predict_workspaces(dow=dow, hour=hour, top=top)
        return jsonify({"status": "ok", "predictions": preds})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@ambient_bp.route('/api/workspace/prewarm', methods=['POST'])
def workspace_prewarm():
    """Manually trigger a pre-warm of the currently-predicted workspaces."""
    try:
        warmed = prewarm_predicted()
        return jsonify({"status": "ok", "warmed": warmed})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
