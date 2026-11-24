"""PDF/Schema and startup compatibility boundary for the native scheduler.

Invoked by the C controller as `python -I bridge.py request.json response.json`.
No OpenCode sessions or scheduler threads are launched here. Requests/responses
live under the controller's run directory, never beside untrusted source files.
"""
import functools
import importlib.util
import json
import sys
import traceback
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute(req):
    root = Path(req['root']).resolve()
    m = load('native_compat', root / 'pipeline/run_2026_all.py')
    m.ROOT = root
    m.WORK = Path(req['work']).resolve()
    m.STATUS = Path(req['status']).resolve()
    m.RUN_ID = req['run_id']
    action = req['action']
    if action in ('validate', 'initialize'):
        v = load('native_validator', root / 'pipeline/validate_exam_paper.py')
        schema_path = root / 'pipeline/docs/question-json-v1.schema.json'
        validator = v.Draft202012Validator(m.read_json(schema_path))
        local = functools.partial(v.validate_paper, schema=schema_path, validator=validator)
        m.validate = functools.partial(m.validate, local_validator=local)
    if action == 'inventory':
        tasks, skipped = m.inventory()
        prompts = {}
        for task in tasks:
            prompts[task['entry_key']] = {
                'extract': m.extraction_prompt(task, 0, None),
                'review': m.review_prompt(task, None),
            }
        return {'tasks': tasks, 'skipped': skipped, 'prompts': prompts}
    if action == 'initialize':
        tasks, skipped = req['tasks'], req['skipped']
        m.initialize(tasks, skipped, req['parallel'])
        return {'initialized': True}
    if action == 'preflight':
        # The main C controller already owns the shared OS controller lock.
        m.guard_existing_writers()
        for pilot in m.read_json(root / 'pipeline/state/pilot_tasks.json'):
            paper = root / pilot['output_dir'] / 'paper.json'
            if not paper.is_file() or m.read_json(paper).get('extraction', {}).get('status') != 'verified':
                if not req.get('allow_unverified_pilot'):
                    raise RuntimeError('Pilot not verified: ' + pilot['entry_key'])
        return {'ready': True}
    if action == 'validate':
        return m.validate(req['task'], Path(req['report']))
    if action == 'local_prepare':
        return m.prepare_local_extraction(req['task'], Path(req['directory']))
    if action == 'prune_assets':
        return m.prune_local_assets(req['task'], Path(req['directory']))
    if action == 'pages':
        manifest = m.prepare_review_pages(req['task'], Path(req['directory']))
        return {'manifest': str(manifest)}
    raise ValueError('Unknown bridge action: ' + action)


def main():
    request_path, response_path = map(Path, sys.argv[1:])
    try:
        result = {'ok': True, 'value': execute(json.loads(request_path.read_text(encoding='utf-8')))}
    except Exception as error:
        traceback.print_exc()
        result = {'ok': False, 'error': str(error)}
    response_path.write_text(json.dumps(result, ensure_ascii=False) + '\n', encoding='utf-8')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
