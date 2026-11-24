"""Round 5: verify the punctuation fix locally, then run a fresh read-only review."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration/targeted_chemistry_3'
spec = importlib.util.spec_from_file_location('controller', ROOT / '_orchestration/run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
KEY = '2026|零模|北师大实验中学|化学|'
PAPER = ROOT / 'parsed/2026/零模/西城/北师大实验中学/化学/paper.json'
PREVIOUS = '_orchestration/targeted_chemistry_3/independent_review_4/result.json'


def main():
    tasks, _ = m.inventory()
    task = next(t for t in tasks if t['entry_key'] == KEY)
    data = m.read_json(PAPER)
    value = data['questions'][15]['parts'][1]['parts'][1]['answer']['content']
    if value.endswith('。'):
        raise RuntimeError('Punctuation fix was not applied')
    report = m.validate(task, WORK / 'validate_round5.json')
    print('ROUND5_VALIDATE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not report.get('valid'):
        raise RuntimeError('Round-5 paper failed validation')
    folder = WORK / 'independent_review_5'
    folder.mkdir(exist_ok=True)
    manifest = m.prepare_review_pages(task, folder)
    prompt = m.review_prompt(task, PREVIOUS)
    prompt += ('\n本轮只改一处：Q16(2)② 答案删去 JSON 自行添加的句末句号，与第 11 页原卷一致；'
               '其余内容与上一轮完全相同。控制器按原始PDF生成的页图清单在 '
               f'{m.rel(manifest)}，必须逐个 read 清单中全部 image 的原路径，并逐张 read 当前全部33个assets。\n')
    before = m.snapshot(task)
    log = folder / 'review.jsonl'
    prompt_path = folder / 'review.md'
    prompt_path.write_text(prompt, encoding='utf-8')
    print('ROUND5_REVIEW_START', flush=True)
    with log.open('w', encoding='utf-8') as handle:
        proc = subprocess.Popen([m.OPENCODE, 'run', '--standalone', '--model', m.MODEL, '--format', 'json',
                                 '--title', '2026化学第5轮独立复核', '--file', str(prompt_path),
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
    print('ROUND5_VERDICT ' + decision, flush=True)
    if decision != 'accept':
        return 1
    print('ROUND5_AUDIT ' + json.dumps(m.visual_audit(task, log, manifest), ensure_ascii=False), flush=True)
    finalized = subprocess.run([sys.executable, '-I', '-X', 'utf8',
                                str(ROOT / '_orchestration/checkpoint_result.py'), KEY, str(log), '--finalize'],
                               cwd=ROOT / '_orchestration', check=False)
    if finalized.returncode:
        raise RuntimeError('Checkpoint finalization failed')
    print('ROUND5_VERIFIED', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
