"""Repair the concrete review_2 findings with six disjoint DeepSeek workers.

Image workers own disjoint assets and submit metadata. The text worker submits
checked JSON-pointer operations. Only this controller merges paper.json.
"""
import concurrent.futures
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration/targeted_chemistry_3'
WORK.mkdir(exist_ok=True)
spec = importlib.util.spec_from_file_location('controller', ROOT / '_orchestration/run_2026_all.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
KEY = '2026|零模|北师大实验中学|化学|'
OUT = ROOT / 'parsed/2026/零模/西城/北师大实验中学/化学'
PAPER = OUT / 'paper.json'
REPORT = ROOT / '_orchestration/pilot_chemistry_resume/review_2.result.json'
GROUPS = [
    ('images_1', ['a001', 'a002', 'a009'], 'p1 a001 左侧 H 球被裁，需完整留白；a002 顶部 H3C 笔画被裁。p3 a009 顶部 OH/COOC2H5 上缘被裁。'),
    ('images_2', ['a014', 'a017'], 'p5 a014 顶部第一行试剂文字被裁。p6 a017 左侧 As2S3 白球被裁。'),
    ('images_3', ['a020', 'a022'], 'p7 a020 顶部 y轴100、%和箭头被裁。p8 a022 必须完整保留“已知：”、①②及两条完整反应示意，顶底均不得裁字；总控文字补丁将删除 JSON 中单独重复的“已知：”，不要自行改 paper.json。'),
    ('images_4', ['a026', 'a027'], 'p9 a026 底部混入下一行“pH在3~5时”残字。p10 a027 必须完整保留 KMnO4 溶液标签，底部不截断，不带入右侧无关单元格残字。'),
    ('images_5', ['a032', 'a033'], 'p12 a032 右侧半截“（2分）”应完整排除，或确需保留则保留完整但不得裁半；a033 左侧 KMnO4 的 K 不完整，须完整留白，不带入左侧(5)残字。'),
]


def emit(text):
    print(text, flush=True)


def run_agent(name, prompt):
    folder = WORK / name
    folder.mkdir(exist_ok=True)
    prompt_path = folder / 'prompt.md'
    prompt_path.write_text(prompt, encoding='utf-8')
    log_path = folder / 'events.jsonl'
    emit('TARGET_START ' + name)
    with log_path.open('w', encoding='utf-8') as log:
        proc = subprocess.Popen([m.OPENCODE, 'run', '--standalone', '--model', m.MODEL, '--format', 'json',
                                 '--title', '2026化学定点修复3 ' + name, '--file', str(prompt_path),
                                 '只执行附件分配的局部任务，禁止联网及修改未分配文件。'],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=30, check=False)
            raise
    result = m.results(log_path)
    m.atomic_json(folder / 'result.json', result)
    if proc.returncode or result['errors'] or not result['text']:
        raise RuntimeError('Worker failed: ' + name + '; ' + str(log_path))
    emit('TARGET_DONE ' + name)
    return log_path


def common(task):
    return f"""项目根目录 {ROOT}。只处理 entry_key {KEY} 的指定局部，不扫描或返修其他范围。
原始 PDF：{task['source_files'][0]}。
只读参考 paper.json：{m.rel(PAPER)}。最新独立复核报告：{m.rel(REPORT)}。
必须忠实原卷，不解题、不补答案、不更正原卷；禁止联网、Web Search、Web Fetch、其他模型或嵌套子代理。
禁止修改原始附件、全局状态、CSV、规范、Schema、脚本或其他卷目。Python 必须 python -I。辅助脚本和中间页图只能放你自己的工作目录 {WORK} 或系统临时目录，不得放 raw 或 assets 中。
本轮是基于具体 review_2 问题的定点修复，不要重做全卷，不要检查所有13页，不要修改未分配对象。
"""


def image_prompt(task, name, ids, problems, base):
    folder = WORK / name
    metadata = {aid: base['assets'][aid] for aid in ids}
    return common(task) + f"""
你是图片裁剪修复者。只可覆写这些图片：{json.dumps([str(OUT / base['assets'][aid]['path']) for aid in ids], ensure_ascii=False)}。
只查看它们对应的原卷页及必要放大区域，原始元数据：{json.dumps(metadata, ensure_ascii=False)}。
具体问题：{problems}
禁止修改 paper.json，即使哈希需要更新也由总控统一处理。不得修改其他 assets。
先打开当前图片和原卷相邻上下文，核实有用内容的完整边界；重新从原始PDF渲染裁剪。完整保留有用文字、上下标、球体、坐标轴和箭头，四边留下实际空白，不能仅把原已裁掉的图片加白边冒充补全。不得把图形重绘成猜测内容，不得遮白、涂改或去水印。不得用整页代替局部图，不得裁到半个标签或邻行文字。
必须实际逐张 read 打开修复后的图片，放大检查四边及原卷上下文。原卷自带模糊不能通过改字伪造清晰度。
将图片实际保存到分配的原路径。然后写 {folder / 'patch.json'}，格式为 {{"assets":{{"a001":{{"bbox":[x0,y0,x1,y1],"sha256":"实际sha256"}}}},"notes":["逐图说明实际核对范围"]}}；assets 必须恰好包含分配的全部ID，bbox为原PDF坐标，page和路径保持原样。不要写 accept 或 verified；简洁返回工作完成情况。
"""


def text_prompt(task):
    return common(task) + f"""
你是文字定点修复者，只提交补丁，不得修改 paper.json 或任何 assets。读取 docs/QUESTION_JSON_FORMAT.md 和 Schema；读取最新复核报告中全部文字问题，并用原始页图核实：
1. Q19(2)① answer.content 删除原卷没有的“锰元素”，其他原答案保持原样。
2. 删除 Q12/Q18/Q19 表格原卷没有的、新增或重复的 caption；保留表格本体、所有单元格及必须的共有题干，编辑说明如需留存应放 notes 而非伪装成原卷表题。
3. Q15(1)② 原卷“下列说法正确的是________。”恢复答空与原标点。
4. Q6、Q9、Q10、Q13、Q14 的图和设问句依原卷顺序组织，不改写任何文字，仅按原卷位置拆开前文/图/设问。
5. Q12 原卷“正确的是：”恢复冒号，Q16(1)② 删去原卷没有的句末句号。
6. Q17 stem 中单独的“已知：”字符串删除。并行图片任务会把完整“已知：”及①②保存在 a022 图内，避免重复；其他 stem 内容不动。
不能改动其他字段、答案、资源、分值或状态。不能把原卷印刷错误静默纠正。保留 Q15 未配平示意式和 Q18 原卷电荷不平衡等已记录疑点。
将补丁写 {WORK / 'text' / 'patch.json'}，格式为 {{"operations":[{{"op":"replace","path":"/questions/18/parts/1/parts/0/answer/content","expected":"当前字段完整值","value":"修正后完整值"}}],"notes":["逐项对照原卷的证据页码"]}}。
支持 replace/remove/add；replace和remove必须携带 expected 当前值，使用标准 JSON pointer。路径仅能在 /questions/ 下，不得修改其他部分。数组操作按你列出的顺序执行，expected 应与该操作执行时的值相符。不要输出accept/verified，简短返回补丁路径和实际核实范围。
"""


def apply_operation(data, operation):
    path = operation['path']
    if not path.startswith('/questions/'):
        raise RuntimeError('Text patch outside assigned questions: ' + path)
    parts = [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]]
    parent = data
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
    kind = operation['op']
    if kind in ['replace', 'remove'] and parent[key] != operation['expected']:
        raise RuntimeError('Text patch precondition failed: ' + path)
    if kind == 'replace':
        parent[key] = operation['value']
    elif kind == 'remove':
        if isinstance(parent, list):
            parent.pop(key)
        else:
            del parent[key]
    elif kind == 'add':
        if isinstance(parent, list):
            parent.insert(key, operation['value'])
        else:
            if key in parent:
                raise RuntimeError('add would overwrite existing field: ' + path)
            parent[key] = operation['value']
    else:
        raise RuntimeError('Unsupported patch operation: ' + kind)


def main():
    tasks, _ = m.inventory()
    task = next(t for t in tasks if t['entry_key'] == KEY)
    before = m.read_json(PAPER)
    before_hash = m.digest(PAPER)
    snapshot = WORK / 'paper_before.json'
    if snapshot.exists():
        raise RuntimeError('Targeted round already exists; inspect its checkpoints before restarting')
    m.atomic_json(snapshot, before)
    with m.state_lock():
        state = m.read_json(m.STATUS)
        item = next(t for t in state['tasks'] if t['entry_key'] == KEY)
        item.update(status='needs_review', targeted_repair_round=3)
        item.pop('blocked_reason', None)
        item.setdefault('history', []).append('Controller-directed targeted repair of concrete review_2 findings with six disjoint workers; ordinary retry limit remains 2')
        m.atomic_json(m.STATUS, state)
    jobs = [(name, image_prompt(task, name, ids, problems, before)) for name, ids, problems in GROUPS]
    jobs.append(('text', text_prompt(task)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run_agent, name, prompt) for name, prompt in jobs]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    if m.digest(PAPER) != before_hash:
        raise RuntimeError('A worker changed paper.json instead of submitting a patch')
    merged = copy.deepcopy(before)
    for name, ids, _ in GROUPS:
        patch = m.read_json(WORK / name / 'patch.json')
        if set(patch['assets']) != set(ids):
            raise RuntimeError('Unexpected asset patch IDs: ' + name)
        for aid in ids:
            item = patch['assets'][aid]
            path = OUT / before['assets'][aid]['path']
            if len(item['bbox']) != 4 or item['bbox'][2] <= item['bbox'][0] or item['bbox'][3] <= item['bbox'][1]:
                raise RuntimeError('Invalid bbox: ' + aid)
            actual = m.digest(path)
            if item['sha256'] != actual:
                raise RuntimeError('Worker image hash mismatch: ' + aid)
            with Image.open(path) as image:
                image.verify()
            merged['assets'][aid].update(bbox=item['bbox'], sha256=actual)
    text = m.read_json(WORK / 'text/patch.json')
    for operation in text['operations']:
        apply_operation(merged, operation)
    merged['extraction']['status'] = 'needs_review'
    with m.task_lock(task):
        if m.digest(PAPER) != before_hash:
            raise RuntimeError('Paper changed before merge')
        m.atomic_json(PAPER, merged)
        report = m.validate(task, WORK / 'validate_merged.json')
    emit('TARGET_MERGED ' + json.dumps(report, ensure_ascii=False))
    review_folder = WORK / 'independent_review'
    review_folder.mkdir(exist_ok=True)
    manifest = m.prepare_review_pages(task, review_folder)
    prompt = m.review_prompt(task, m.rel(REPORT))
    prompt += f'\n本轮变更仅限 {m.rel(WORK)} 下六份 patch.json；旧纸面快照在 {m.rel(snapshot)}。先逐项验证这些改动，然后完整复核整卷。控制器按原始PDF生成的页图清单在 {m.rel(manifest)}，必须逐个 read 清单中全部 image 的原路径，并逐张 read 当前全部33个assets；不得用报告摘要代替视觉复核。\n'
    before_review = m.snapshot(task)
    log = run_agent('independent_review', prompt)
    if m.snapshot(task) != before_review:
        raise RuntimeError('Read-only reviewer changed the output')
    result = m.results(log)
    decision = m.verdict(result['text'])
    emit('TARGET_VERDICT ' + decision)
    if decision != 'accept':
        with m.state_lock():
            state = m.read_json(m.STATUS)
            item = next(t for t in state['tasks'] if t['entry_key'] == KEY)
            item.update(status='blocked', blocked_reason='Targeted repair 3 review: ' + decision,
                        review_report=m.rel(WORK / 'independent_review/result.json'))
            m.atomic_json(m.STATUS, state)
        return 1
    m.visual_audit(task, log, manifest)
    finalized = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(ROOT / '_orchestration/checkpoint_result.py'),
                                KEY, str(log), '--finalize'], cwd=WORK, check=False)
    if finalized.returncode:
        raise RuntimeError('Targeted checkpoint finalization failed')
    emit('TARGET_QUEUE_LAUNCH parallel=24')
    queued = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-u', str(ROOT / '_orchestration/run_2026_all.py'),
                             '--parallel', '24'], cwd=WORK, check=False)
    emit('TARGET_ENDED exit=' + str(queued.returncode))
    return queued.returncode


if __name__ == '__main__':
    raise SystemExit(main())
