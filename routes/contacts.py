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
import core
from core import (
    FRIDAY_DIR,
    VIBE_TERMINALS,
    _HAS_TRUST_GRAPHS,
    get_people_graph,
)  # noqa: E501
from services.misc_engine import (
    _contacts_list,
    _contacts_research_dir,
    _load_trust_graph,
)  # noqa: E501

contacts_bp = Blueprint('contacts', __name__)



@contacts_bp.route('/api/trust')
def get_trust():
    """Return the people (contacts) graph. Backward-compat route; delegates to
    PeopleGraph internally now that source/media trust lives elsewhere."""
    try:
        data = _load_trust_graph()
        return jsonify({"status": "ok", **data})
    except Exception:
        return jsonify({"status": "ok", "people": {}})


@contacts_bp.route('/api/people')
def get_people():
    """The human-contact trust graph (PeopleGraph). New canonical route."""
    try:
        data = _load_trust_graph()
        return jsonify({"status": "ok", **data})
    except Exception:
        return jsonify({"status": "ok", "people": {}})


@contacts_bp.route('/api/personality')
def get_personality():
    """Return personality traits and maturity."""
    pfile = FRIDAY_DIR / "personality.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "maturity": 0.5,
        "traits": {
            "curiosity": 0.8, "skepticism": 0.7, "humor": 0.75,
            "loyalty": 0.9, "directness": 0.85, "empathy": 0.8,
            "contrarianism": 0.7
        },
        "style": {
            "formality": 0.3, "verbosity": 0.4, "technicality": 0.6,
            "humor_frequency": 0.5, "emoji_usage": 0.1
        },
        "temperature": 0.7
    })


@contacts_bp.route('/api/epistemic')
def get_epistemic():
    """Return epistemic scoring data."""
    try:
        from epistemic_engine import get_epistemic_engine
        data = get_epistemic_engine().get_scores()
        if 'overall' in data and 'overall_score' not in data:
            data['overall_score'] = data['overall']
        return jsonify({"status": "ok", **data})
    except Exception:
        pass
    efile = FRIDAY_DIR / "epistemic_scores.json"
    if not efile.exists():
        efile = FRIDAY_DIR / "epistemic.json"
    if efile.exists():
        try:
            data = json.loads(efile.read_text(encoding='utf-8'))
            if 'overall' in data and 'overall_score' not in data:
                data['overall_score'] = data['overall']
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "overall_score": 0.0,
        "total_turns_scored": 0,
        "dimensions": {
            "information_gain": 0.0, "pushback_rate": 0.0,
            "socratic_ratio": 0.0, "independence_fostering": 0.0
        }
    })


# ═══════════════════════════════════════════════════════════════
#  PERSONALITY & TRUST EDITING ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@contacts_bp.route('/api/personality/set', methods=['POST'])
def set_personality():
    """Update a personality trait or style dimension."""
    data = request.get_json(silent=True) or {}
    trait = data.get('trait', '')
    value = data.get('value', 0.5)

    if not trait:
        return jsonify({"status": "error", "message": "No trait specified"}), 400

    pfile = FRIDAY_DIR / "personality.json"
    try:
        pdata = {}
        if pfile.exists():
            pdata = json.loads(pfile.read_text(encoding='utf-8'))

        if trait.startswith('style.'):
            style_key = trait.split('.', 1)[1]
            if 'style' not in pdata:
                pdata['style'] = {}
            pdata['style'][style_key] = float(value)
        elif trait == 'temperature':
            pdata['temperature'] = float(value)
        else:
            if 'traits' not in pdata:
                pdata['traits'] = {}
            pdata['traits'][trait] = float(value)

        pfile.write_text(json.dumps(pdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "trait": trait, "value": float(value)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@contacts_bp.route('/api/trust/edit', methods=['POST'])
def edit_trust():
    """Edit trust scores for a person or add evidence."""
    data = request.get_json(silent=True) or {}
    person_key = data.get('person', '')
    scores = data.get('scores', None)
    add_evidence = data.get('add_evidence', None)

    if not person_key:
        return jsonify({"status": "error", "message": "No person specified"}), 400

    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "people graph unavailable"}), 501
    try:
        person, err = get_people_graph(friday_dir=FRIDAY_DIR).edit(
            person_key, scores=scores, add_evidence=add_evidence)
        if err:
            code = 404 if 'not found' in err else 400
            return jsonify({"status": "error", "message": err}), code
        return jsonify({"status": "ok", "person": person_key})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@contacts_bp.route('/api/trust/add-person', methods=['POST'])
def add_trust_person():
    """Add a new person to the trust graph."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    aliases = data.get('aliases', [])
    entity_type = data.get('entity_type', 'human')

    if not name:
        return jsonify({"status": "error", "message": "No name specified"}), 400

    if not _HAS_TRUST_GRAPHS:
        return jsonify({"status": "error", "message": "people graph unavailable"}), 501
    try:
        key, err = get_people_graph(friday_dir=FRIDAY_DIR).add_person(
            name, aliases=aliases, entity_type=entity_type)
        if err:
            code = 409 if 'already exists' in err else 400
            return jsonify({"status": "error", "message": err}), code
        return jsonify({"status": "ok", "key": key, "name": name})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@contacts_bp.route('/api/contacts')
def get_contacts():
    """Merged contact list built from trust_graph.json."""
    contacts = _contacts_list()
    return jsonify({"status": "ok", "contacts": contacts, "count": len(contacts)})


@contacts_bp.route('/api/contacts/<path:name>')
def get_contact(name):
    """Full trust dimensions + evidence for a single contact (case-insensitive name)."""
    graph = _load_trust_graph()
    raw = graph.get('people') or {}
    target = (name or '').strip().lower()
    match = None
    if isinstance(raw, dict):
        if target in raw:
            match = raw[target]
        else:
            for k, v in raw.items():
                if not isinstance(v, dict):
                    continue
                cand = (v.get('name') or k or '').strip().lower()
                aliases = [a.lower() for a in (v.get('aliases') or [])]
                if cand == target or target in aliases:
                    match = v
                    break
    else:
        for v in raw:
            if not isinstance(v, dict):
                continue
            cand = (v.get('name') or '').strip().lower()
            aliases = [a.lower() for a in (v.get('aliases') or [])]
            if cand == target or target in aliases:
                match = v
                break
    if not match:
        return jsonify({"status": "error", "message": "Contact not found"}), 404

    # Look for a stored research file.
    research_file = _contacts_research_dir() / f"{target.replace(' ', '_')}.md"
    research = research_file.read_text(encoding='utf-8') if research_file.exists() else ''

    return jsonify({"status": "ok", "contact": match, "research": research})


@contacts_bp.route('/api/contacts/research', methods=['POST'])
def contacts_research():
    """Kick off web research on a contact. Writes a stub and launches a background terminal."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 400
    key = name.lower().replace(' ', '_')
    research_file = _contacts_research_dir() / f"{key}.md"
    stamp = datetime.now().isoformat()
    if not research_file.exists():
        research_file.write_text(
            f"# Research: {name}\n\n_Initialized {stamp}_\n\n"
            f"- Public profile search: pending\n"
            f"- LinkedIn / GitHub: pending\n"
            f"- Recent news mentions: pending\n",
            encoding='utf-8'
        )
    try:
        tid = str(uuid.uuid4())[:8]
        VIBE_TERMINALS[tid] = {
            "id": tid, "task": f"Research contact: {name}",
            "status": "pending", "cwd": str(FRIDAY_DIR),
            "started": stamp, "log_file": None
        }
    except Exception:
        tid = None
    return jsonify({
        "status": "ok", "name": name,
        "research_file": str(research_file),
        "task_id": tid,
        "message": f"Research queued for {name}"
    })
