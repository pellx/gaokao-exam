"""Keep the 2026 queue running across service outages and long-paper pauses.

Each controller run rebuilds state from status.json, so a restart only loses the
in-flight invocations. Leftover per-invocation opencode trees are killed before
each relaunch so nothing keeps writing a paper after the controller gave up on it.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'pipeline'
STATUS = WORK / 'state/status.json'
CONTROLLER = WORK / 'run_2026_all.py'
NATIVE_CONTROLLER = WORK / 'native/run_2026_all.exe'
LOG = WORK / 'supervisor.log'
PARALLEL = 3
MAX_RESTARTS = 60
SETTLED = {'verified', 'blocked'}


def controller_command():
    """Prefer the compiled scheduler; keep Python usable before the first build."""
    if NATIVE_CONTROLLER.is_file():
        return [str(NATIVE_CONTROLLER)]
    return [sys.executable, '-I', '-X', 'utf8', '-u', str(CONTROLLER)]


def emit(text):
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(text + '\n')
    print(text, flush=True)


def counts():
    data = json.loads(STATUS.read_text(encoding='utf-8'))['full_batch']
    result = {}
    for task in data['tasks']:
        result[task['status']] = result.get(task['status'], 0) + 1
    return result


def kill_stale_agents():
    command = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'opencode.exe' -and "
               "$_.CommandLine -match 'run .*(pipeline|_orchestration|backup/orchestration)[./]runs' } | ForEach-Object { "
               "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                   capture_output=True, timeout=120, check=False)


def main():
    restarts = 0
    while True:
        before = counts()
        command = controller_command()
        engine = 'C' if command[0] == str(NATIVE_CONTROLLER) else 'Python'
        emit(f'SUPERVISOR_START parallel={PARALLEL} restart={restarts} engine={engine} counts={json.dumps(before, ensure_ascii=False)}')
        with (WORK / 'supervisor_run.log').open('a', encoding='utf-8') as log:
            code = subprocess.call([*command, '--parallel', str(PARALLEL), '--allow-unverified-pilot'],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        kill_stale_agents()
        after = counts()
        emit(f'SUPERVISOR_EXIT code={code} counts={json.dumps(after, ensure_ascii=False)}')
        if not (set(after) - SETTLED):
            emit('SUPERVISOR_DONE all tasks settled')
            return 0
        if restarts >= MAX_RESTARTS:
            emit('SUPERVISOR_GIVE_UP restart limit reached')
            return 1
        restarts += 1
        time.sleep(60)


if __name__ == '__main__':
    raise SystemExit(main())
