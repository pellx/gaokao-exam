"""Finalize one externally launched read-only checkpoint after its process exits."""
import argparse
import collections
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('controller', ROOT / '_orchestration/run_2026_all.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('key')
    parser.add_argument('log', type=Path)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    tasks, _ = controller.inventory()
    task = next(t for t in tasks if t['entry_key'] == args.key)
    result = controller.results(args.log)
    result['verdict'] = controller.verdict(result['text'])
    result_path = args.log.with_suffix('.result.json')
    controller.atomic_json(result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.finalize:
        return
    if result['verdict'] != 'accept' or result['errors']:
        raise SystemExit('Cannot finalize: checkpoint was not accepted')
    proof = controller.visual_audit(task, args.log)
    controller.validate(task, args.log.with_suffix('.validation.json'))
    with controller.task_lock(task), controller.state_lock():
        status = controller.read_json(controller.STATUS)
        if any(t['status'] in ['assigned', 'extracted', 'in_review'] for t in status.get('chinese_batch', {}).get('tasks', [])):
            raise SystemExit('Wait for old Chinese controller before writing global state')
        paper = ROOT / task['output_dir'] / 'paper.json'
        data = controller.read_json(paper)
        original_status = data['extraction']['status']
        data['extraction']['status'] = 'verified'
        controller.atomic_json(paper, data)
        try:
            report = controller.validate(task, args.log.with_suffix('.verified_validation.json'))
        except Exception:
            data['extraction']['status'] = original_status
            controller.atomic_json(paper, data)
            raise
        groups = [status.get('tasks', [])]
        for batch_name in ['chinese_batch', 'full_batch']:
            if batch_name in status:
                groups.append(status[batch_name].get('tasks', []))
        for group in groups:
            for item in group:
                if item['entry_key'] != args.key:
                    continue
                item.update(status='verified', extraction_status='verified', review_result='accept',
                            review_report=controller.rel(result_path), stats=report, completed_at=controller.now(),
                            accepted_paper_sha256=controller.digest(paper))
                item.setdefault('review_sessions', []).append(result['session_id'])
                item.setdefault('history', []).append('Checkpoint independently accepted after targeted repair/resume')
                item.pop('blocked_reason', None)
        for name in ['chinese_batch', 'full_batch']:
            if name in status:
                status[name]['status_counts'] = dict(collections.Counter(t['status'] for t in status[name]['tasks']))
                status[name]['updated_at'] = controller.now()
        controller.atomic_json(controller.STATUS, status)
    controller.atomic_json(args.log.with_suffix('.visual_evidence.json'), proof)
    print('FINALIZED ' + args.key)


if __name__ == '__main__':
    main()
