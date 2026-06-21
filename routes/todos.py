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
from services.misc_engine import (
    _load_todos,
    _save_todos,
)  # noqa: E501

todos_bp = Blueprint('todos', __name__)



@todos_bp.route('/api/todos', methods=['GET'])
def get_todos():
    """Return all todos from ~/.friday/todos.json."""
    todos = _load_todos()
    return jsonify({"status": "ok", "todos": todos, "count": len(todos)})


@todos_bp.route('/api/todos', methods=['POST'])
def add_todo():
    """Add an AI-proposed (or user) task with optional deadline."""
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({"status": "error", "message": "No title provided"}), 400

    todos = _load_todos()
    todo = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": data.get('description', ''),
        "deadline": data.get('deadline', None),
        "priority": data.get('priority', 'medium'),
        "status": data.get('status', 'proposed'),
        "category": data.get('category', 'general'),
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "source": data.get('source', 'user'),
    }
    todos.append(todo)
    _save_todos(todos)
    return jsonify({"status": "ok", "todo": todo})


@todos_bp.route('/api/todos/<todo_id>/approve', methods=['POST'])
def approve_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'approved'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@todos_bp.route('/api/todos/<todo_id>/reject', methods=['POST'])
def reject_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'rejected'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@todos_bp.route('/api/todos/<todo_id>/complete', methods=['POST'])
def complete_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'completed'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@todos_bp.route('/api/todos/<todo_id>', methods=['DELETE'])
def delete_todo(todo_id):
    todos = _load_todos()
    before = len(todos)
    todos = [t for t in todos if t['id'] != todo_id]
    _save_todos(todos)
    return jsonify({"status": "ok", "removed": before - len(todos)})
