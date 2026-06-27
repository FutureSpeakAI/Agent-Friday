"""
Routes — Creative Pipeline engine + Scene DNA + QA gates + Take comparison.

  • /api/pipelines/*        — definitions, runs, advance/resume/intervene
  • /api/scene-dna/*        — layer metadata, validate, surgical single-layer edit
  • /api/qa/evaluate        — self-evaluation gate (score content vs intent)
  • /api/takes/*            — generate N candidates and recommend the best

Mutation endpoints return HTTP 200 with status-in-body (project convention).
"""
import traceback
from flask import Blueprint, jsonify, request

from services import creative_pipeline as cp
from services import scene_dna as sd
from services import qa_gates
from services import take_comparison as tc

creative_pipeline_bp = Blueprint('creative_pipeline', __name__)


def _err(e, code=500):
    traceback.print_exc()
    return jsonify({"status": "error", "message": str(e)}), code


# ═══ PIPELINE DEFINITIONS ════════════════════════════════════════
@creative_pipeline_bp.route('/api/pipelines/templates', methods=['GET'])
def pipeline_templates():
    try:
        return jsonify({"status": "ok", "templates": cp.list_templates()})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines', methods=['POST'])
def pipeline_register():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(cp.register_pipeline(data))
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/<pipeline_id>', methods=['GET'])
def pipeline_get(pipeline_id):
    try:
        d = cp.get_pipeline(pipeline_id)
        if not d:
            return jsonify({"status": "error", "message": "not found"}), 404
        return jsonify({"status": "ok", "pipeline": d})
    except Exception as e:
        return _err(e)


# ═══ PIPELINE RUNS ═══════════════════════════════════════════════
@creative_pipeline_bp.route('/api/pipelines/runs', methods=['GET'])
def runs_list():
    try:
        return jsonify({"status": "ok", "runs": cp.list_runs()})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/runs', methods=['POST'])
def run_create():
    data = request.get_json(silent=True) or {}
    pid = (data.get('pipeline_id') or '').strip()
    if not pid:
        return jsonify({"status": "error", "message": "pipeline_id required"}), 200
    try:
        run = cp.create_run(pid, data.get('input') or {},
                            project_id=data.get('project_id') or '')
        if run.get('status') == 'error':
            return jsonify(run), 200
        # Optionally kick it off immediately.
        if data.get('start'):
            sync = bool(data.get('sync'))
            if sync:
                cp.run(run['run_id'], until_checkpoint=True)
                run = cp.get_run(run['run_id'])
            else:
                cp.start_async(run['run_id'])
                run = cp.get_run(run['run_id'])
        return jsonify({"status": "ok", "run": run})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/runs/<run_id>', methods=['GET'])
def run_get(run_id):
    try:
        run = cp.get_run(run_id)
        if not run:
            return jsonify({"status": "error", "message": "not found"}), 404
        return jsonify({"status": "ok", "run": run})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/runs/<run_id>/start', methods=['POST'])
def run_start(run_id):
    data = request.get_json(silent=True) or {}
    try:
        if data.get('sync'):
            run = cp.run(run_id, until_checkpoint=not data.get('run_to_end'))
            return jsonify({"status": "ok", "run": run})
        res = cp.start_async(run_id, until_checkpoint=not data.get('run_to_end'))
        return jsonify(res if res.get('status') == 'error'
                       else {"status": "ok", **res, "run": cp.get_run(run_id)})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/runs/<run_id>/advance', methods=['POST'])
def run_advance(run_id):
    try:
        return jsonify({"status": "ok", "run": cp.advance(run_id)})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/runs/<run_id>/resume', methods=['POST'])
def run_resume(run_id):
    """Resume a checkpoint-paused run, optionally editing the context first."""
    data = request.get_json(silent=True) or {}
    try:
        cp.resume(run_id, data.get('edited_context') or data.get('context'))
        # Continue execution after the checkpoint (sync by default for the API).
        if data.get('sync', True):
            run = cp.run(run_id, until_checkpoint=not data.get('run_to_end'))
        else:
            cp.start_async(run_id, until_checkpoint=not data.get('run_to_end'))
            run = cp.get_run(run_id)
        return jsonify({"status": "ok", "run": run})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/pipelines/runs/<run_id>/intervene', methods=['POST'])
def run_intervene(run_id):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok",
                        "run": cp.intervene(run_id, data.get('context_updates') or {})})
    except Exception as e:
        return _err(e)


# ═══ SCENE DNA ═══════════════════════════════════════════════════
@creative_pipeline_bp.route('/api/scene-dna/layers', methods=['GET'])
def scene_dna_layers():
    try:
        return jsonify({"status": "ok", "layers": sd.layers(),
                        "labels": sd.describe_layers(), "empty": sd.empty()})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/scene-dna/validate', methods=['POST'])
def scene_dna_validate():
    data = request.get_json(silent=True) or {}
    try:
        clean = sd.validate(data.get('scene_dna') or data)
        return jsonify({"status": "ok", "scene_dna": clean,
                        "prompt": sd.render(clean)})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/scene-dna/edit', methods=['POST'])
def scene_dna_edit():
    """Surgical single-layer edit — change one layer, keep the rest, re-render."""
    data = request.get_json(silent=True) or {}
    layer = (data.get('layer') or '').strip()
    if layer not in sd.layers():
        return jsonify({"status": "error",
                        "message": f"unknown layer; valid: {sd.layers()}"}), 200
    try:
        updated = sd.edit_layer(data.get('scene_dna') or {}, layer, data.get('value'))
        return jsonify({"status": "ok", "scene_dna": updated,
                        "prompt": sd.render(updated)})
    except Exception as e:
        return _err(e)


# ═══ QA GATE ═════════════════════════════════════════════════════
@creative_pipeline_bp.route('/api/qa/config', methods=['GET'])
def qa_config():
    try:
        return jsonify({"status": "ok", "config": qa_gates.qa_config()})
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/qa/evaluate', methods=['POST'])
def qa_evaluate():
    data = request.get_json(silent=True) or {}
    content = data.get('content') or ''
    intent = data.get('intent') or ''
    try:
        if data.get('kind') == 'image' or data.get('image_path'):
            verdict = qa_gates.evaluate_image(data.get('image_path') or content, intent)
        else:
            verdict = qa_gates.evaluate_text(content, intent,
                                            workspace=data.get('workspace') or '')
        return jsonify({"status": "ok", "verdict": verdict})
    except Exception as e:
        return _err(e)


# ═══ TAKE COMPARISON ═════════════════════════════════════════════
@creative_pipeline_bp.route('/api/takes/images', methods=['POST'])
def takes_images():
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({"status": "error", "message": "prompt required"}), 200
    try:
        res = tc.compare_images(
            prompt, n=data.get('n', 3), model=data.get('model'),
            style=data.get('style'), aspect_ratio=data.get('aspect_ratio') or '1:1',
            intent=data.get('intent') or '')
        return jsonify(res)
    except Exception as e:
        return _err(e)


@creative_pipeline_bp.route('/api/takes/text', methods=['POST'])
def takes_text():
    """Generate N text candidates (varied temperature) and recommend the best."""
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    intent = data.get('intent') or prompt
    workspace = data.get('workspace') or 'content'
    if not prompt:
        return jsonify({"status": "error", "message": "prompt required"}), 200
    try:
        from agent_friday.services.model_router import _generate_text
        # Diversify takes by nudging temperature per candidate.
        temps = [0.4, 0.7, 0.95]

        def _gen(i):
            return _generate_text([{"role": "user", "content": prompt}],
                                  max_tokens=data.get('max_tokens', 1200),
                                  temperature=temps[i % len(temps)],
                                  workspace=workspace)
        res = tc.compare_text(intent, _gen, n=data.get('n', 3), workspace=workspace)
        return jsonify(res)
    except Exception as e:
        return _err(e)
