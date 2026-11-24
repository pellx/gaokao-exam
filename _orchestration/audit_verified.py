"""Report whether each verified paper still matches its accepted fingerprint.

A verified entry proves nothing on its own: the paper file must still hash to
the accepted paper fingerprint or to the trusted restored baseline.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / 'data/question_extraction/2026/status.json'
BASE = ROOT / '_orchestration/verified_baseline_manifest.json'


def main():
    status = json.loads(STATUS.read_text(encoding='utf-8'))
    base = json.loads(BASE.read_text(encoding='utf-8')) if BASE.is_file() else {}
    verified = unexplained = 0
    problems = []
    for task in status['full_batch']['tasks']:
        if task['status'] != 'verified':
            continue
        verified += 1
        paper = ROOT / task['output_dir'] / 'paper.json'
        digest = hashlib.sha256(paper.read_bytes()).hexdigest() if paper.is_file() else None
        trusted = {task.get('accepted_paper_sha256'), base.get(task['entry_key'], {}).get('paper_sha256')}
        if digest not in trusted:
            unexplained += 1
            problems.append({'entry_key': task['entry_key'], 'paper_sha256': digest,
                             'accepted': sorted(x for x in trusted if x)})
    print(json.dumps({'verified': verified, 'unexplained': unexplained, 'problems': problems},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
