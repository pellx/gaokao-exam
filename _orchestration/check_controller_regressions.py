"""Exercise review parsing, resume states, service failures and visual provenance.

All state writes go to a temporary test directory; original papers are read-only.
"""
import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('batch_under_test', ROOT / '_orchestration/run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TASKS, SKIPPED = m.inventory()
MATH = next(t for t in TASKS if t['entry_key'] == '2026|零模|北师大实验中学|数学|')
CHEM = next(t for t in TASKS if t['entry_key'] == '2026|零模|北师大实验中学|化学|')


class Regressions(unittest.TestCase):
    def test_decision_negations_and_final_line(self):
        examples = {
            '最终结论：不能 accept，应 reject': 'unknown',
            'accept：已核对13页': 'accept',
            '**accept** — 已核对13页': 'accept',
            '结论：无 blocked': 'unknown',
            '> accept\n最终结论：reject': 'reject',
            'accept\n最终结论：不能 accept，应 reject': 'unknown',
            '```\naccept\n```': 'unknown',
            '## 结论：**reject**（需修复）': 'reject',
            '已完成\naccept': 'accept',
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(m.verdict(text), expected)

    def test_verified_paper_wins_over_stale_block(self):
        state = m.read_json(m.STATUS)
        state['full_batch'] = {'tasks': [dict(entry_key=MATH['entry_key'], status='blocked', retry_count=0)]}
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            status = Path(directory) / 'status.json'
            m.atomic_json(status, state)
            with patch.object(m, 'STATUS', status), patch.object(m, 'RUN_ID', 'regression'), \
                 patch.object(m, 'proven_verified', return_value=True):
                m.initialize([MATH], [], 24)
                result = m.read_json(status)['full_batch']['tasks'][0]
                self.assertEqual(result['status'], 'verified')

    def test_valid_draft_is_reviewed_even_if_old_status_verified(self):
        state = m.read_json(m.STATUS)
        state['full_batch'] = {'tasks': [dict(entry_key=CHEM['entry_key'], status='verified', retry_count=0)]}
        original_reader = m.read_json
        paper = m.ROOT / CHEM['output_dir'] / 'paper.json'
        def draft_reader(path):
            data = original_reader(path)
            if Path(path) == paper:
                data['extraction']['status'] = 'draft'
            return data
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            status = Path(directory) / 'status.json'
            m.atomic_json(status, state)
            with patch.object(m, 'STATUS', status), patch.object(m, 'RUN_ID', 'regression'), patch.object(m, 'read_json', draft_reader):
                m.initialize([CHEM], [], 24)
                result = original_reader(status)['full_batch']['tasks'][0]
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['resume_stage'], 'in_review')

    def test_unproven_verified_flag_does_not_skip_review(self):
        unproven = copy.deepcopy(CHEM)
        # A self-asserted status has no baseline fingerprint or accepted review evidence.
        self.assertFalse(m.proven_verified(unproven, [{'status': 'verified'}]))

    def test_matching_hash_still_runs_validation(self):
        with patch.object(m, 'validate', side_effect=RuntimeError('asset hash mismatch')) as validate:
            result = m.check_paper(MATH, [{'entry_key': MATH['entry_key'], 'status': 'verified',
                                           'accepted_paper_sha256': m.digest(m.ROOT / MATH['output_dir'] / 'paper.json')}])
        self.assertEqual(result['state'], 'failed')
        self.assertIn('asset hash mismatch', result['error'])
        validate.assert_called_once()

    def test_matching_hash_without_review_proof_does_not_skip(self):
        state = m.read_json(m.STATUS)
        state['full_batch'] = {'tasks': [dict(entry_key=CHEM['entry_key'], status='verified', retry_count=0,
                                              accepted_paper_sha256=m.digest(m.ROOT / CHEM['output_dir'] / 'paper.json'))]}
        state['tasks'] = []
        state['chinese_batch'] = {'tasks': []}
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            status = Path(directory) / 'status.json'
            m.atomic_json(status, state)
            paper = {'extraction': {'status': 'verified'}}
            with patch.object(m, 'STATUS', status), patch.object(m, 'RUN_ID', 'regression'), \
                 patch.object(m, 'check_paper', return_value={'state': 'valid', 'data': paper, 'report': {}}), \
                 patch.object(m, 'proven_verified', return_value=False):
                m.initialize([CHEM], [], 24)
                result = m.read_json(status)['full_batch']['tasks'][0]
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['resume_stage'], 'in_review')

    def test_blocked_with_stale_execution_error_is_preserved(self):
        task = next(t for t in TASKS if not (m.ROOT / t['output_dir'] / 'paper.json').exists())
        state = m.read_json(m.STATUS)
        state['full_batch'] = {'tasks': [dict(entry_key=task['entry_key'], status='blocked', retry_count=2,
                                              blocked_reason='Review rejected', error_kind='execution')]}
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            status = Path(directory) / 'status.json'
            m.atomic_json(status, state)
            with patch.object(m, 'STATUS', status), patch.object(m, 'RUN_ID', 'regression'):
                m.initialize([task], [], 24)
                self.assertEqual(m.read_json(status)['full_batch']['tasks'][0]['status'], 'blocked')

    def test_launch_errors_trigger_batch_pause(self):
        m.STOP.clear()
        m.TOOL_FAILURES = 0
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            with patch.object(m, 'update'), patch.object(m.subprocess, 'Popen', side_effect=OSError('WinError 193 test')):
                for attempt in range(m.TOOL_FAILURE_LIMIT):
                    with self.assertRaises(m.ExecutionFailure):
                        m.invoke(CHEM, 'test_' + str(attempt), 'Do not execute', Path(directory))
        self.assertTrue(m.STOP.is_set())
        m.STOP.clear()
        m.TOOL_FAILURES = 0

    def test_slow_invocation_does_not_trip_circuit_breaker(self):
        m.STOP.clear()
        m.TOOL_FAILURES = 0
        calls = {'wait': 0}

        class FakeProc:
            pid = 4242

            def wait(self, timeout=None):
                calls['wait'] += 1
                if calls['wait'] % 2:
                    raise subprocess.TimeoutExpired('opencode', timeout)
                return 0

        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            directory = Path(directory)
            with patch.object(m, 'update'), patch.object(m.subprocess, 'Popen', return_value=FakeProc()), \
                 patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess(args=[], returncode=0)):
                for _ in range(m.TOOL_FAILURE_LIMIT + 2):
                    with self.assertRaises(m.ExecutionFailure):
                        m.invoke(CHEM, 'slow', 'do not execute', directory)
        self.assertFalse(m.STOP.is_set())
        self.assertEqual(m.TOOL_FAILURES, 0)
        m.STOP.clear()

    def test_reviewer_mutation_is_requeued_not_blocked(self):
        task = next(t for t in TASKS if not (m.ROOT / t['output_dir'] / 'paper.json').exists())
        updates = []
        item = {'status': 'pending', 'retry_count': 0, 'active_stage': 'review_0'}
        with patch.object(m, 'saved_task', return_value=item), patch.object(m, 'RUN_ID', 'regression'), \
             patch.object(m, 'validate', return_value={'valid': True}), \
             patch.object(m, 'snapshot', side_effect=[{'a': 1}, {'a': 2}]), \
             patch.object(m, 'prepare_review_pages', return_value=m.WORK / 'manifest.json'), \
             patch.object(m, 'prepare_local_extraction'), patch.object(m, 'prune_local_assets'), \
             patch.object(m, 'invoke', side_effect=[
                 ({'text': 'done', 'session_id': 'ses_extract'}, 'extract_report.json'),
                 ({'text': 'accept', 'session_id': 'ses_review'}, 'review_report.json')]), \
             patch.object(m, 'update', side_effect=lambda key, **fields: updates.append(fields)):
            m.process_locked(task)
        self.assertEqual(updates[-1]['status'], 'needs_review')
        self.assertEqual(updates[-1]['error_kind'], 'reviewer_mutation')
        self.assertEqual(updates[-1]['resume_stage'], 'needs_review')
        # A later resume must not inherit the old block.
        state = m.read_json(m.STATUS)
        state['full_batch'] = {'tasks': [dict(entry_key=task['entry_key'], status='blocked', retry_count=0,
                                              blocked_reason='Read-only reviewer modified output',
                                              error_kind='reviewer_mutation')]}
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            status = Path(directory) / 'status.json'
            m.atomic_json(status, state)
            with patch.object(m, 'STATUS', status), patch.object(m, 'RUN_ID', 'regression'):
                m.initialize([task], [], 24)
                result = m.read_json(status)['full_batch']['tasks'][0]
                self.assertNotEqual(result['status'], 'blocked')

    def test_execution_failure_preserves_resumable_state(self):
        task = next(t for t in TASKS if not (m.ROOT / t['output_dir'] / 'paper.json').exists())
        updates = []
        item = {'status': 'pending', 'retry_count': 0, 'active_stage': 'extract_0'}
        with patch.object(m, 'saved_task', return_value=item), patch.object(m, 'RUN_ID', 'regression'), \
             patch.object(m, 'prepare_local_extraction'), patch.object(m, 'prune_local_assets'), \
             patch.object(m, 'invoke', side_effect=m.ExecutionFailure('temporary outage')), \
             patch.object(m, 'update', side_effect=lambda key, **fields: updates.append(fields)):
            m.process_locked(task)
        self.assertEqual(updates[-1]['status'], 'paused')
        self.assertEqual(updates[-1]['retry_count'], 0)

    def test_page_count_cannot_be_satisfied_with_unrelated_images(self):
        with tempfile.TemporaryDirectory(dir=m.WORK) as directory:
            directory = Path(directory)
            manifest = m.prepare_review_pages(MATH, directory)
            pages = m.read_json(manifest)
            paper = m.read_json(m.ROOT / MATH['output_dir'] / 'paper.json')
            assets = [(m.ROOT / MATH['output_dir'] / a['path']).resolve() for a in paper.get('assets', {}).values()]
            paths = [str(m.ROOT / p['image']) for p in pages] + [str(p) for p in assets]
            def write_log(values):
                log = directory / 'audit.jsonl'
                log.write_text('\n'.join(json.dumps({'type': 'tool_use', 'part': {'tool': 'read', 'state': {
                    'status': 'completed', 'input': {'path': p}}}}) for p in values), encoding='utf-8')
                return log
            m.visual_audit(MATH, write_log(paths), manifest)
            paths[0] = str(directory / 'unrelated.png')
            with self.assertRaisesRegex(RuntimeError, 'assigned page image'):
                m.visual_audit(MATH, write_log(paths), manifest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
