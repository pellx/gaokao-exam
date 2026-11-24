"""Read-only audit of every text field against the scanned pages.

One audit pass that lists all remaining character-level infidelities is cheaper
than letting each independent review discover exactly one more.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration/targeted_chemistry_3'
spec = importlib.util.spec_from_file_location('controller', ROOT / '_orchestration/run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
KEY = '2026|零模|北师大实验中学|化学|'
FOLDER = WORK / 'text_audit'

PROMPT = """你是文字保真审计员，只读，不改任何文件。项目根目录 {root}。
只审计 entry_key `{key}` 这一卷的文本字段：{paper}。
原始 PDF：{pdf}。控制器已按原 PDF 渲染 13 页：{pages}（清单 {manifest}）。

任务：把 paper.json 里所有面向原卷的文字——题干 stem、选项 options.content、答案 answer.content、
材料 materials 的文字、表格 header/rows 的文字——逐条与原卷渲染页对照，找出“静默改写原卷”的差异：
1. 标点不同（自加/漏掉的句号、冒号、分号、括号、答空下划线）；
2. 全角/半角不同（～ vs ~、∶ vs :、（）vs ()）；
3. 符号变体（＝ vs =、⇌ vs =、< 与 < 的空格、类似字母）；
4. 增字、漏字、改字、语序调整、把两处说明合并或拆分；
5. 把原卷印刷疑点“更正”为看起来正确的写法。

不要管 LaTeX 排版本身（\\mathrm、上下标写法允许），只判断可读文字是否与原卷一致。
不要提出 schema/路径/图片裁剪问题，这些另有复核。不要修改 paper.json。

必须实际 read 每一页渲染图，并对疑点用 python -I + PyMuPDF 以 600-1200 dpi 放大裁剪核对
（脚本和中间图只能放 {folder}）。看不清就写 unclear，不要猜。

输出一个 JSON 数组，写 {out}，每项：
{{"path":"JSON pointer","kind":"punctuation|width|symbol|text|layout","current":"当前值片段",
"expected":"原卷值片段","page":页码,"evidence":"放大核对的说明"}}
没有发现就写 []。最后只回一行 AUDIT_DONE 和条目数。不要输出 accept/reject。
"""


def main():
    tasks, _ = m.inventory()
    task = next(t for t in tasks if t['entry_key'] == KEY)
    FOLDER.mkdir(exist_ok=True)
    manifest = m.prepare_review_pages(task, FOLDER)
    prompt = PROMPT.format(root=ROOT, key=KEY,
                           paper='parsed/2026/零模/西城/北师大实验中学/化学/paper.json',
                           pdf=task['source_files'][0], pages=m.rel(FOLDER / 'review_pages'),
                           manifest=m.rel(manifest), folder=m.rel(FOLDER),
                           out=m.rel(FOLDER / 'findings.json'))
    prompt_path = FOLDER / 'prompt.md'
    prompt_path.write_text(prompt, encoding='utf-8')
    log = FOLDER / 'audit.jsonl'
    print('AUDIT_START', flush=True)
    with log.open('w', encoding='utf-8') as handle:
        proc = subprocess.Popen([m.OPENCODE, 'run', '--standalone', '--model', m.MODEL, '--format', 'json',
                                 '--title', '2026化学文字保真审计', '--file', str(prompt_path),
                                 '只读审计附件指定的一卷，禁止联网和修改文件。'],
                                cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        proc.wait(timeout=14400)
    result = m.results(log)
    m.atomic_json(FOLDER / 'result.json', result)
    print('AUDIT_EXIT', proc.returncode, 'errors', json.dumps(result['errors'], ensure_ascii=False)[:300], flush=True)
    findings = FOLDER / 'findings.json'
    if findings.is_file():
        data = json.loads(findings.read_text(encoding='utf-8'))
        print('AUDIT_FINDINGS', len(data), flush=True)
        for item in data:
            print(json.dumps(item, ensure_ascii=False), flush=True)
    else:
        print('AUDIT_NO_FINDINGS_FILE', flush=True)
    return 0 if not result['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
