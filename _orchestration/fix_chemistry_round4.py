"""Apply the four concrete review_2 blockers and re-run the independent review.

1-3: three answer strings where the JSON added a full stop the paper does not
print after the （x分） marker.
4: Q19 table row I repeated the wording that is already printed inside a027.
"""
import argparse
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
OUT = ROOT / 'parsed/2026/零模/西城/北师大实验中学/化学'
PAPER = OUT / 'paper.json'
PREVIOUS = '_orchestration/runs/20261114_081924/011/review_2_result.json'
DROPPED = 'i. 先加试剂a；ii. 再加试剂b；20滴0.002 mol·L−1 KMnO4溶液'
EXPECTED = [
    ('/questions/17/parts/1/parts/1/answer/content', '60℃后，H2O2受热分解。', '60℃后，H2O2受热分解'),
    ('/questions/17/parts/1/parts/2/answer/content',
     '加入NaCl，Pb2+生成[PbCl3]−的速率快于生成PbSO4的速率，阻止了铅膏被PbSO4包裹；但Pb2+生成PbSO4的限度大于生成[PbCl3]−的限度，[PbCl3]−又转化为PbSO4，同时释放Cl−。该过程循环往复，促使PbSO4含量不断升高。',
     '加入NaCl，Pb2+生成[PbCl3]−的速率快于生成PbSO4的速率，阻止了铅膏被PbSO4包裹；但Pb2+生成PbSO4的限度大于生成[PbCl3]−的限度，[PbCl3]−又转化为PbSO4，同时释放Cl−。该过程循环往复，促使PbSO4含量不断升高'),
    ('/questions/17/parts/4/answer/content',
     '相同浓度的Na2CO3溶液碱性强于(NH4)2CO3溶液，可能会导致Pb(OH)2产生，影响PbCO3的纯度。',
     '相同浓度的Na2CO3溶液碱性强于(NH4)2CO3溶液，可能会导致Pb(OH)2产生，影响PbCO3的纯度'),
]


def emit(text):
    print(text, flush=True)


def resolve(data, pointer):
    node = data
    for part in pointer.split('/')[1:]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def apply_text_fixes(data):
    for pointer, expected, value in EXPECTED:
        current = resolve(data, pointer)
        if current != expected:
            raise RuntimeError('Unexpected current value at ' + pointer + ': ' + repr(current))
        parent = data
        parts = pointer.split('/')[1:]
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        parent[key] = value
    cell = data['questions'][18]['stem'][5]['table']['rows'][0][0]
    if cell != [{'asset': 'a027'}, DROPPED]:
        raise RuntimeError('Unexpected Q19 first cell: ' + json.dumps(cell, ensure_ascii=False))
    data['questions'][18]['stem'][5]['table']['rows'][0][0] = [{'asset': 'a027'}]
    notes = data['questions'][18].setdefault('notes', [])
    note = ('原表“实验”列为跨 I–IV 的合并单元格，装置图 a027 内已印“i. 先加试剂a；ii. 再加试剂b；'
            '20滴0.002 mol·L−1 KMnO4溶液”，JSON 不再重复该文字，只保留图片引用。')
    if not any(note in item for item in notes):
        notes.append(note)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply-only', action='store_true')
    parser.add_argument('--review-only', action='store_true')
    args = parser.parse_args()
    tasks, _ = m.inventory()
    task = next(t for t in tasks if t['entry_key'] == KEY)
    if not args.review_only:
        data = m.read_json(PAPER)
        apply_text_fixes(data)
        data['extraction']['status'] = 'needs_review'
        with m.task_lock(task):
            m.atomic_json(PAPER, data)
            report = m.validate(task, WORK / 'validate_round4.json')
        emit('ROUND4_VALIDATE ' + json.dumps(report, ensure_ascii=False))
        if not report.get('valid'):
            raise RuntimeError('Round-4 paper failed validation')
    if args.apply_only:
        return 0
    folder = WORK / 'independent_review_4'
    folder.mkdir(exist_ok=True)
    manifest = m.prepare_review_pages(task, folder)
    prompt = m.review_prompt(task, PREVIOUS)
    prompt += (f'\n本轮只做了四处定点修改：Q18(2)②、Q18(2)③、Q18(5) 三处答案删去 JSON 自行添加的句末句号；'
               f'Q19 表格第一行第一列删去与 a027 图内文字重复的字符串，只保留图片引用。'
               f'其它内容与上一轮完全一致。控制器按原始PDF生成的页图清单在 {m.rel(manifest)}，'
               f'必须逐个 read 清单中全部 image 的原路径，并逐张 read 当前全部33个assets。\n')
    before = m.snapshot(task)
    log = folder / 'review.jsonl'
    prompt_path = folder / 'review.md'
    prompt_path.write_text(prompt, encoding='utf-8')
    emit('ROUND4_REVIEW_START')
    with log.open('w', encoding='utf-8') as handle:
        proc = subprocess.Popen([m.OPENCODE, 'run', '--standalone', '--model', m.MODEL, '--format', 'json',
                                 '--title', '2026化学第4轮独立复核', '--file', str(prompt_path),
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
    emit('ROUND4_VERDICT ' + decision)
    if decision != 'accept':
        return 1
    emit('ROUND4_AUDIT ' + json.dumps(m.visual_audit(task, log, manifest), ensure_ascii=False))
    finalized = subprocess.run([sys.executable, '-I', '-X', 'utf8',
                                str(ROOT / '_orchestration/checkpoint_result.py'), KEY, str(log), '--finalize'],
                               cwd=ROOT / '_orchestration', check=False)
    if finalized.returncode:
        raise RuntimeError('Checkpoint finalization failed')
    emit('ROUND4_VERIFIED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
