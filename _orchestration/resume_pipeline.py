"""Wait for the in-flight chemical review, then launch the authorized 24-way queue."""
import argparse
import ctypes
import importlib.util
import json
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration'
spec = importlib.util.spec_from_file_location('controller', WORK / 'run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
KEY = '2026|零模|北师大实验中学|化学|'


def wait_process(pid, timeout_ms=7200000):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # Process already exited.
            return
        raise RuntimeError(f'Cannot wait for review process {pid}: Windows error {error}')
    try:
        outcome = kernel.WaitForSingleObject(handle, timeout_ms)
        if outcome != 0:
            raise RuntimeError(f'Review process wait failed/timed out: {outcome}')
    finally:
        kernel.CloseHandle(handle)


def call_agent(task, stage, prompt):
    directory = WORK / 'pilot_chemistry_resume'
    directory.mkdir(exist_ok=True)
    prompt_path = directory / (stage + '.md')
    log_path = directory / (stage + '.jsonl')
    prompt_path.write_text(prompt, encoding='utf-8')
    print('PILOT_START ' + stage, flush=True)
    with log_path.open('w', encoding='utf-8') as log:
        proc = subprocess.Popen([m.OPENCODE, 'run', '--model', m.MODEL, '--format', 'json',
                                 '--title', '2026化学试跑 ' + stage, '--file', str(prompt_path),
                                 '严格按附件处理指定一卷，禁止联网。'],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'],
                           capture_output=True, timeout=30, check=False)
            raise
    result = m.results(log_path)
    result['verdict'] = m.verdict(result['text'])
    result_path = log_path.with_suffix('.result.json')
    m.atomic_json(result_path, result)
    if proc.returncode or result['errors'] or not result['text']:
        raise RuntimeError('Pilot OpenCode execution failed: ' + str(result_path))
    print('PILOT_RESULT ' + stage + ' ' + result['verdict'], flush=True)
    return log_path, result_path, result


def block(reason, report):
    with m.state_lock():
        status = m.read_json(m.STATUS)
        for item in status['tasks']:
            if item['entry_key'] == KEY:
                item.update(status='blocked', blocked_reason=reason, review_report=m.rel(report))
        m.atomic_json(m.STATUS, status)
    print('PILOT_BLOCKED ' + reason + '; ' + str(report), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--review-pid', type=int, required=True)
    parser.add_argument('--review-log', type=Path, required=True)
    parser.add_argument('--parallel', type=int, default=24)
    args = parser.parse_args()
    print(f'PIPELINE_WAIT review_pid={args.review_pid}; authorized_parallel={args.parallel}', flush=True)
    wait_process(args.review_pid)
    tasks, _ = m.inventory()
    task = next(t for t in tasks if t['entry_key'] == KEY)
    result = m.results(args.review_log)
    verdict = m.verdict(result['text'])
    report = args.review_log.with_suffix('.result.json')
    m.atomic_json(report, dict(result, verdict=verdict))
    review_log = args.review_log
    print('PILOT_EXISTING_REVIEW ' + verdict, flush=True)
    if verdict == 'reject' and not result['errors']:
        # The saved pilot is on repair 1. This is its one remaining configured repair.
        with m.state_lock():
            status = m.read_json(m.STATUS)
            item = next(t for t in status['tasks'] if t['entry_key'] == KEY)
            if item.get('retry_count', 0) >= m.MAX_RETRY:
                raise RuntimeError('Chemical pilot repair limit already reached')
            item.update(status='needs_review', retry_count=2, review_report=m.rel(report), review_result='reject')
            m.atomic_json(m.STATUS, status)
        _, _, repair = call_agent(task, 'repair_2', m.extraction_prompt(task, 2, m.rel(report)))
        if m.verdict(repair['text']) == 'blocked':
            block('Chemical pilot extraction blocked', report)
            return 1
        m.validate(task, WORK / 'pilot_chemistry_resume/validate_repair_2.json')
        before = m.snapshot(task)
        review_log, report, result = call_agent(task, 'review_2', m.review_prompt(task, m.rel(report)))
        if m.snapshot(task) != before:
            block('Read-only chemical reviewer modified output', report)
            return 1
        verdict = result['verdict']
    if verdict != 'accept' or result['errors']:
        block('Chemical pilot not accepted: ' + verdict, report)
        return 1
    finalized = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(WORK / 'checkpoint_result.py'),
                                KEY, str(review_log), '--finalize'], cwd=WORK, check=False)
    if finalized.returncode:
        raise RuntimeError('Chemical checkpoint finalization failed')
    print(f'PIPELINE_QUEUE_LAUNCH parallel={args.parallel} model={m.MODEL}', flush=True)
    # This long-running child reuses the shared service and creates a new session per stage.
    queued = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-u', str(WORK / 'run_2026_all.py'),
                             '--parallel', str(args.parallel)], cwd=WORK, check=False)
    print(f'PIPELINE_ENDED exit={queued.returncode}', flush=True)
    return queued.returncode


if __name__ == '__main__':
    raise SystemExit(main())
