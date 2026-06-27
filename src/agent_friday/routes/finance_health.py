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
from agent_friday.services.agent import (
    _vault_read_text,
    _vault_write_text,
)  # noqa: E501
from agent_friday.services.misc_engine import (
    FINANCE_DIR,
    HEALTH_DIR,
)  # noqa: E501

fh_bp = Blueprint('finance_health', __name__)


@fh_bp.route('/api/finance/portfolio')
def finance_portfolio():
    """Read portfolio positions from config."""
    path = FINANCE_DIR / "portfolio.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    # Create template if missing — generic placeholders, no personal data.
    template = {"positions": [{"ticker": "EXMPL", "shares": 0, "cost_basis": 0}], "accounts": ["Your Brokerage Account"]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@fh_bp.route('/api/finance/perks')
def finance_perks():
    """Read card perks from config."""
    path = FINANCE_DIR / "amex_perks.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"perks": [{"name": "Perk name", "value": "$X/yr", "used": False, "expires": "", "notes": ""}]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@fh_bp.route('/api/finance/contacts')
def finance_contacts():
    """Financial contacts reference."""
    return jsonify({"status": "ok", "contacts": [
        {"name": "", "role": "Financial Advisor", "firm": "", "phone": "", "email": ""},
        {"name": "", "role": "CPA", "firm": "", "phone": "", "email": ""}
    ]})

@fh_bp.route('/api/finance/quickref')
def finance_quickref():
    """Quick reference for financial accounts."""
    return jsonify({"status": "ok", "accounts": [
        {"name": "Example Bank", "type": "Banking", "notes": ""},
        {"name": "Example Insurance", "type": "Insurance", "notes": ""},
        {"name": "Example Card 1", "type": "Credit Card", "notes": ""},
        {"name": "Example Card 2", "type": "Credit Card", "notes": ""}
    ]})

@fh_bp.route('/api/health/medications')
def health_medications():
    """Read medications from config."""
    path = HEALTH_DIR / "medications.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"medications": [{"name": "Example Medication", "dose": "", "frequency": "", "notes": ""}]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@fh_bp.route('/api/health/appointments')
def health_appointments():
    """Read appointments from config."""
    path = HEALTH_DIR / "appointments.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"appointments": [{"provider": "", "type": "", "email": "", "next": "", "frequency": ""}]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@fh_bp.route('/api/health/insurance')
def health_insurance():
    """Read insurance info from config."""
    path = HEALTH_DIR / "insurance.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"insurance": {"provider": "Your Insurance Provider", "plan": "Add your plan name", "policy_number": "Add your policy number", "group_number": "Add your group number"}}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@fh_bp.route('/api/health/vehicles')
def health_vehicles():
    """Read vehicle fleet data from config."""
    path = HEALTH_DIR / "vehicles.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"vehicles": [{"name": "Your Vehicle", "miles": "", "notes": "", "mechanic": "", "service_history": []}], "mechanics": []}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})
