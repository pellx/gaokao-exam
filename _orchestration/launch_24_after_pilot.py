"""Guarantee the authorized 24-way launch once the targeted chemistry repair exits."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration'
CONTROLLER_PID = 14044
QUEUE = WORK / 'run_2026_all.py'
STATUS = ROOT / 'data/question_extraction/2026/status.json'


def processes():
    command = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | "
               "Select-Object ProcessId,CommandLine | ConvertTo-Json -Depth 2")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=60)
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    return [rows] if isinstance(rows, dict) else rows


def pid_exists(pid):
    return any(row.get('ProcessId') == pid for row in processes())


def queue_running():
    return any(str(QUEUE).lower().replace('\\', '/') in (row.get('CommandLine') or '').lower().replace('\\', '/')
               for row in processes())


def launch():
    with (WORK / 'launch_24.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', '-u', str(QUEUE),
                                  '--parallel', '24', '--allow-unverified-pilot'],
                                 cwd=WORK, stdout=log, stderr=subprocess.STDOUT)
        log.write(f'LAUNCHER_STARTED pid={child.pid}\n')
        log.flush()
    print('LAUNCHER_STARTED pid=' + str(child.pid), flush=True)


def main():
    print('LAUNCHER_WAIT targeted_pid=' + str(CONTROLLER_PID), flush=True)
    idle = 0
    while True:
        if queue_running():
            print('LAUNCHER_ALREADY_RUNNING', flush=True)
            return 0
        if not pid_exists(CONTROLLER_PID):
            # Confirm the queue is still not starting before firing.
            time.sleep(10)
            if queue_running():
                print('LAUNCHER_ALREADY_RUNNING', flush=True)
                return 0
            launch()
            return 0
        idle += 1
        if idle % 30 == 0:
            print(f'LAUNCHER_STILL_WAITING targeted_pid={CONTROLLER_PID}', flush=True)
        time.sleep(10)


if __name__ == '__main__':
    raise SystemExit(main())
