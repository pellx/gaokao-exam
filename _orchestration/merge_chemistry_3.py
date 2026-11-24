"""Merge the six disjoint chemistry patches and run the independent review.

The images_3 worker was killed after writing a020/a022 but before submitting
its metadata, so this controller re-renders those two crops itself from the
original PDF and records bbox/sha by direct measurement.
"""
import argparse
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration/targeted_chemistry_3'
CAND = WORK / 'candidates'
spec = importlib.util.spec_from_file_location('controller', ROOT / '_orchestration/run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
spec2 = importlib.util.spec_from_file_location('targeted', ROOT / '_orchestration/targeted_chemistry_resume.py')
t2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(t2)
apply_operation = t2.apply_operation
KEY = '2026|零模|北师大实验中学|化学|'
OUT = ROOT / 'parsed/2026/零模/西城/北师大实验中学/化学'
PAPER = OUT / 'paper.json'
GROUPS = [
    ('images_1', ['a001', 'a002', 'a009']),
    ('images_2', ['a014', 'a017']),
    ('images_4', ['a026', 'a027']),
    ('images_5', ['a032', 'a033']),
]
MANUAL = {
    'a020': {'source': CAND / 'a020_final.png', 'bbox': [225.0, 580.5, 473.0, 712.0]},
    'a022': {'source': CAND / 'a022_final.png', 'bbox': [105.0, 360.0, 545.0, 470.0]},
}


def emit(text):
    print(text, flush=True)


def merge():
    before = m.read_json(WORK / 'paper_before.json')
    if m.read_json(PAPER) != m.read_json(WORK / 'paper_before.json'):
        raise RuntimeError('paper.json changed after the targeted snapshot')
    merged = copy.deepcopy(before)
    for name, ids in GROUPS:
        patch = m.read_json(WORK / name / 'patch.json')
        if set(patch['assets']) != set(ids):
            raise RuntimeError('Unexpected asset patch IDs: ' + name)
        for aid in ids:
            item = patch['assets'][aid]
            path = OUT / before['assets'][aid]['path']
            box = item['bbox']
            if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
                raise RuntimeError('Invalid bbox: ' + aid)
            actual = m.digest(path)
            if item['sha256'] != actual:
                raise RuntimeError('Worker image hash mismatch: ' + aid)
            with Image.open(path) as image:
                image.verify()
            if item.get('page') not in (None, before['assets'][aid]['page']):
                raise RuntimeError('Worker changed page for ' + aid)
            merged['assets'][aid].update(bbox=box, sha256=actual)
    for aid, spec_item in MANUAL.items():
        target = OUT / before['assets'][aid]['path']
        shutil.copyfile(spec_item['source'], target)
        with Image.open(target) as image:
            image.verify()
        merged['assets'][aid].update(bbox=spec_item['bbox'], sha256=m.digest(target))
    text = m.read_json(WORK / 'text' / 'patch.json')
    for operation in text['operations']:
        apply_operation(merged, operation)
    merged['extraction']['status'] = 'needs_review'
    m.atomic_json(PAPER, merged)
    tasks, _ = m.inventory()
    task = next(t for t in tasks if t['entry_key'] == KEY)
    with m.task_lock(task):
        report = m.validate(task, WORK / 'validate_merged3.json')
    emit('MERGE_VALIDATE ' + json.dumps(report, ensure_ascii=False))
    if not report.get('valid'):
        raise RuntimeError('Merged paper failed validation')
    return task


def review(task):
    folder = WORK / 'independent_review_3'
    folder.mkdir(exist_ok=True)
    manifest = m.prepare_review_pages(task, folder)
    prompt = m.review_prompt(task, 'pilot_chemistry_resume/review_2.result.json')
    prompt += (f'\n本轮为定点修复复核。六份补丁在 {m.rel(WORK)} 下（images_1/2/4/5、text 的 patch.json，'
               f'a020/a022 由控制器按原始PDF重新渲染，边界记录见 {m.rel(WORK / "validate_merged3.json")} 同目录说明）；'
               f'旧纸面快照 {m.rel(WORK / "paper_before.json")}。'
               f'控制器按原始PDF生成的页图清单在 {m.rel(manifest)}，必须逐个 read 清单中全部 image 的原路径，'
               f'并逐张 read 当前全部33个assets；不得用报告摘要代替视觉复核。\n')
    before = m.snapshot(task)
    log = folder / 'review.jsonl'
    prompt_path = folder / 'review.md'
    prompt_path.write_text(prompt, encoding='utf-8')
    emit('REVIEW_START')
    with log.open('w', encoding='utf-8') as handle:
        proc = subprocess.Popen([m.OPENCODE, 'run', '--standalone', '--model', m.MODEL, '--format', 'json',
                                 '--title', '2026化学定点修复3独立复核', '--file', str(prompt_path),
                                 '只读复核附件指定的这一卷，禁止联网和修改文件。'],
                                cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        proc.wait(timeout=7200)
    result = m.results(log)
    m.atomic_json(folder / 'result.json', result)
    if proc.returncode or result['errors'] or not result['text']:
        raise RuntimeError('Review execution failure: ' + json.dumps(result['errors'], ensure_ascii=False)[:800])
    if m.snapshot(task) != before:
        raise RuntimeError('Read-only reviewer changed the output')
    decision = m.verdict(result['text'])
    emit('REVIEW_DECISION ' + decision)
    m.atomic_json(folder / 'decision.json', {'decision': decision, 'session_id': result['session_id']})
    if decision != 'accept':
        return decision
    emit('REVIEW_AUDIT ' + json.dumps(m.visual_audit(task, log, manifest), ensure_ascii=False))
    finalized = subprocess.run([sys.executable, '-I', '-X', 'utf8',
                                str(ROOT / '_orchestration/checkpoint_result.py'), KEY, str(log), '--finalize'],
                               cwd=ROOT / '_orchestration', check=False)
    if finalized.returncode:
        raise RuntimeError('Checkpoint finalization failed')
    emit('CHEMISTRY_VERIFIED')
    return decision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--merge-only', action='store_true')
    parser.add_argument('--review-only', action='store_true')
    args = parser.parse_args()
    if args.review_only:
        tasks, _ = m.inventory()
        return 0 if review(next(t for t in tasks if t['entry_key'] == KEY)) == 'accept' else 1
    task = merge()
    if args.merge_only:
        return 0
    return 0 if review(task) == 'accept' else 1


if __name__ == '__main__':
    raise SystemExit(main())
