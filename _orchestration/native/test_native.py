"""Native/Python parity and isolated scheduler integration tests. No API calls.

python -I -X utf8 _orchestration/native/test_native.py
All writers, mock agents and state files are placed in temporary fixture roots.
"""
import copy
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(__file__).resolve().parent
EXE = NATIVE / 'run_2026_all.exe'
spec = importlib.util.spec_from_file_location('compat', ROOT / '_orchestration/run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def cli(*args, check=True, timeout=120):
    p = subprocess.run([str(EXE), *map(str, args)], capture_output=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    if check and p.returncode:
        raise AssertionError(f'exit={p.returncode}\n{p.stdout}\n{p.stderr}')
    return p


MOCK = r'''
import json, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8')
root = pathlib.Path.cwd()
args = sys.argv[1:]
stage = args[args.index('--title') + 1].split()[-1]
prompt = pathlib.Path(args[args.index('--file') + 1]).read_text(encoding='utf-8')
tasks = json.loads((root / '_orchestration/tasks_2026.json').read_text(encoding='utf-8'))
task = next(t for t in tasks if '`' + t['entry_key'] + '`' in prompt)
scenario = (root / 'scenario').read_text(encoding='utf-8')
paper = root / task['output_dir'] / 'paper.json'
session = ('same' if scenario == 'same_session' else stage) + str(task['sequence'])
def event(value): print(json.dumps(value, ensure_ascii=False), flush=True)
if scenario == 'timeout':
    import time
    time.sleep(3)
if scenario == 'service_error':
    event({'type': 'error', 'error': {'type': 'provider.transport', 'message': 'fixture outage'}})
    sys.exit(1)
if stage.startswith('extract_'):
    paper.parent.mkdir(parents=True, exist_ok=True)
    source = root / task['source_files'][0]
    import os
    data = {'format': 'exam-questions/v1', 'paper': task['paper_metadata'],
            'source': {'file': os.path.relpath(source, paper.parent).replace('\\', '/'),
                       'sha256': task['source_hashes'][task['source_files'][0]]},
            'extraction': {'status': 'draft'}, 'questions': []}
    paper.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    event({'type': 'text', 'sessionID': session, 'part': {'text': 'draft written'}})
else:
    if scenario == 'mutation':
        data = json.loads(paper.read_text(encoding='utf-8'))
        data['extraction']['notes'] = ['unexpected reviewer write']
        paper.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    event({'type': 'tool_use', 'part': {'tool': 'read', 'state': {
        'status': 'completed', 'input': {'path': str(root / task['source_files'][0])}}}})
    if scenario == 'web':
        event({'type': 'tool_use', 'part': {'tool': 'webfetch', 'state': {'status': 'completed'}}})
    text = 'reject' if scenario == 'reject' else 'accept\n最终结论：不能 accept，应 reject' if scenario == 'ambiguous' else 'accept'
    event({'type': 'text', 'sessionID': session, 'part': {'text': text}})
'''


def fixture(base, scenario='accept', count=1):
    for name in ['_orchestration/native', 'scripts', 'docs', 'data/original_downloads', 'data/question_extraction/2026']:
        (base / name).mkdir(parents=True, exist_ok=True)
    for source, target in [
        (NATIVE / 'bridge.py', base / '_orchestration/native/bridge.py'),
        (ROOT / '_orchestration/run_2026_all.py', base / '_orchestration/run_2026_all.py'),
        (ROOT / '_orchestration/local_extraction.py', base / '_orchestration/local_extraction.py'),
        (ROOT / 'scripts/validate_exam_paper.py', base / 'scripts/validate_exam_paper.py'),
        (ROOT / 'docs/question-json-v1.schema.json', base / 'docs/question-json-v1.schema.json'),
    ]:
        shutil.copy2(source, target)
    write_json(base / 'data/question_extraction/2026/status.json', {'tasks': []})
    write_json(base / 'data/question_extraction/2026/pilot_tasks.json', [])
    entries, files = [], []
    for i in range(count):
        key = f'2026|零模|测试 区{i}|语文|'
        source = base / f'raw/原件 空格{i}.docx'
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(b'fixture bytes only, never opened as document')
        entries.append(dict(year='2026', sequence=str(i+1), entry_key=key, status='已获得原始附件',
                            kind='零模', district=f'测试 区{i}', region_or_school=f'测试 区{i}', subject='语文',
                            variant='', title=f'测试试卷{i}'))
        files.append(dict(year='2026', file_type='原始附件', status='下载成功', entry_key=key,
                          local_path=source.relative_to(base).as_posix(), sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    for name, rows in [('entries', entries), ('files', files)]:
        with (base / f'data/original_downloads/{name}.csv').open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (base / 'run').write_text(MOCK, encoding='utf-8')
    (base / 'scenario').write_text(scenario, encoding='utf-8')


def batch(base):
    return cli('--root', base, '--python', sys.executable, '--opencode', sys.executable, '--parallel', 3)


def state(base):
    return json.loads((base / 'data/question_extraction/2026/status.json').read_text(encoding='utf-8'))['full_batch']


class NativeTests(unittest.TestCase):
    def test_verdict_parity(self):
        values = ['最终结论：不能 accept，应 reject', 'accept：已核对13页', '**accept** — 已核对13页',
                  '结论：无 blocked', '> accept\n最终结论：reject', 'accept\n最终结论：不能 accept，应 reject',
                  '```\naccept\n```', '## 结论：**reject**（需修复）', '已完成\naccept',
                  'accepted', 'reject: accept', '结论：acceptance', 'accept（已核对）', 'REJECT',
                  'accept\n结论：unknown', 'accept, reject', '###accept', 'accept：无 reject',
                  'accept。', 'accept — done', 'reject [reason]', '结论：accept or reject']
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'values.json'
            write_json(p, values)
            actual = json.loads(cli('--verdict-file', p).stdout)
        self.assertEqual(actual, [m.verdict(v) for v in values])

    def test_event_parser_parity(self):
        rows = ['noise', json.dumps({'sessionID': '首个', 'type': 'text', 'part': {'text': 'first'}}),
                json.dumps({'type': 'tool_use', 'part': {'state': {'output': 'x' * 200000}}}),
                json.dumps({'type': 'error', 'error': {'message': '错误'}}),
                json.dumps({'type': 'text', 'sessionID': 'later', 'part': {'text': 'last 😄'}})]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / '日志 文件.jsonl'
            p.write_text('\n'.join(rows), encoding='utf-8')
            self.assertEqual(json.loads(cli('--parse-log', p).stdout), m.results(p))

    def test_corrupt_event_cannot_reuse_earlier_accept(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            first = json.dumps({'type': 'text', 'part': {'text': 'accept'}}) + '\n'
            for bad in ['{broken', '{"type":"text","part":{"text":"accept\\u0000reject"}}', '\x00']:
                path.write_text(first + bad, encoding='utf-8')
                self.assertNotEqual(cli('--parse-log', path, check=False).returncode, 0)

    def test_accept_parallel_and_resume(self):
        with tempfile.TemporaryDirectory(prefix='native 中文 ') as d:
            base = Path(d)
            fixture(base, count=3)
            batch(base)
            first = state(base)
            self.assertEqual(first['status_counts'], {'verified': 3})
            for task in first['tasks']:
                self.assertIsNone(task['active_pid'])
                self.assertNotEqual(task['extraction_session'], task['review_session'])
                paper = base / task['output_dir'] / 'paper.json'
                self.assertEqual(task['accepted_paper_sha256'], hashlib.sha256(paper.read_bytes()).hexdigest())
            batch(base)
            self.assertEqual(state(base)['status_counts'], {'verified': 3})
            self.assertTrue(all(t['review_result'] == 'resume_skip' for t in state(base)['tasks']))

    def test_reject_exhausts_two_repairs(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base, 'reject')
            batch(base)
            item = state(base)['tasks'][0]
            self.assertEqual((item['status'], item['retry_count']), ('blocked', 2))

    def test_mutation_requeues(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base, 'mutation')
            batch(base)
            item = state(base)['tasks'][0]
            self.assertEqual((item['status'], item['error_kind'], item['resume_stage']),
                             ('needs_review', 'reviewer_mutation', 'needs_review'))

    def test_service_failure_pauses_without_repair(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base, 'service_error')
            batch(base)
            item = state(base)['tasks'][0]
            self.assertEqual((item['status'], item['retry_count'], item['error_kind']), ('paused', 0, 'execution'))

    def test_timeout_preserves_retry_budget(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base, 'timeout')
            cli('--root', base, '--python', sys.executable, '--opencode', sys.executable,
                '--parallel', 1, '--timeout', 1)
            item = state(base)['tasks'][0]
            self.assertEqual((item['status'], item['retry_count']), ('paused', 0))
            self.assertFalse(state(base)['paused'])

    def test_service_circuit_breaker(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base, 'service_error', count=12)
            p = cli('--root', base, '--python', sys.executable, '--opencode', sys.executable,
                    '--parallel', 1, check=False)
            self.assertEqual(p.returncode, 3)
            self.assertTrue(state(base)['paused'])
            self.assertEqual(state(base)['status_counts'], {'paused': 8, 'pending': 4})

    def test_ambiguous_web_and_same_session_cannot_verify(self):
        for scenario in ['ambiguous', 'web', 'same_session']:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                fixture(base, scenario)
                batch(base)
                self.assertEqual(state(base)['tasks'][0]['status'], 'blocked')

    def test_shared_controller_lock_with_python(self):
        import msvcrt
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base)
            lock = base / '_orchestration/controller.lock'
            with lock.open('w+b') as f:
                f.write(b'0')
                f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                p = cli('--root', base, '--prepare-only', check=False)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn('already running', p.stderr)
                self.assertFalse((base / '_orchestration/tasks_2026.json').exists())

    def test_prepare_only_preserves_status(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            fixture(base)
            path = base / 'data/question_extraction/2026/status.json'
            before = path.read_bytes()
            cli('--root', base, '--prepare-only')
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(json.loads((base / '_orchestration/tasks_2026.json').read_text(encoding='utf-8'))), 1)

    def test_visual_provenance_requires_each_assigned_image(self):
        with tempfile.TemporaryDirectory(prefix='native 图像 ') as d:
            base = Path(d)
            out = base / 'parsed/test'
            out.mkdir(parents=True)
            pages = []
            for i in range(2):
                image = base / f'review_pages/页{i}.png'
                image.parent.mkdir(exist_ok=True)
                image.write_bytes(f'image fixture {i}'.encode())
                pages.append({'image': image.relative_to(base).as_posix(),
                              'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest()})
            write_json(out / 'paper.json', {'source': {'file': '../../source.pdf', 'page_count': 2}, 'assets': {}})
            manifest, log, req = base / 'manifest.json', base / 'events.jsonl', base / 'request.json'
            write_json(manifest, pages)
            def events(paths):
                log.write_text('\n'.join(json.dumps({'type': 'tool_use', 'part': {'tool': 'read', 'state': {
                    'status': 'completed', 'input': {'path': str(p)}}}}, ensure_ascii=False) for p in paths), encoding='utf-8')
            paths = [base / p['image'] for p in pages]
            events(paths)
            write_json(req, {'root': str(base), 'task': {'output_dir': 'parsed/test'},
                             'log': str(log), 'manifest': str(manifest)})
            good = json.loads(cli('--audit-file', req).stdout)
            self.assertEqual(good['rendered_page_reads'], 2)
            events(paths[1:] + [base / 'unrelated.png'])
            self.assertNotEqual(cli('--audit-file', req, check=False).returncode, 0)
            events(paths)
            paths[0].write_bytes(b'changed')
            self.assertNotEqual(cli('--audit-file', req, check=False).returncode, 0)

    def test_cli_invalid_parallel(self):
        for value in ['0', '97', 'garbage', '-1']:
            self.assertEqual(cli('--parallel', value, check=False).returncode, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
