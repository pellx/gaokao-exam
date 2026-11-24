"""Resume the 2026 queue with isolated OpenCode/DeepSeek extraction and review.

Run from the project root with Python -I. Source documents are never executed.
Only this controller writes status.json; do not run the old controller concurrently.
"""
import argparse
import collections
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data/question_extraction/2026'
STATUS = STATE / 'status.json'
WORK = ROOT / '_orchestration'
MODEL = 'opencode-go/deepseek-v4.1-flash#high'
RUN_ID = datetime.now().strftime('%Y%m%d_%H%M%S')
LOCK = threading.RLock()
STOP = threading.Event()
TOOL_FAILURES = 0
MAX_RETRY = 2
TOOL_FAILURE_LIMIT = 8
INVOCATION_TIMEOUT = 14400
OPENCODE = shutil.which('opencode')


class ExecutionFailure(RuntimeError):
    """A retryable CLI/service failure, not a content rejection."""


class BatchPaused(RuntimeError):
    """No new invocation may start until the operator resumes the batch."""


class InvocationTimeout(ExecutionFailure):
    """One invocation ran past the per-stage time budget."""


class ReviewerMutation(RuntimeError):
    """A read-only reviewer changed the output; the paper must be re-extracted."""


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def rel(path):
    return path.relative_to(ROOT).as_posix()


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def existing_output_by_source():
    """Keep regenerated tasks pointed at an existing bundle's actual source file."""
    outputs = {}
    parsed = ROOT / 'parsed'
    if not parsed.is_dir():
        return outputs
    source_file_re = re.compile(r'"source"\s*:\s*\{\s*"file"\s*:\s*"((?:\\\\.|[^"\\\\])*)"', re.DOTALL)
    for paper_path in parsed.rglob('paper.json'):
        text = paper_path.read_text(encoding='utf-8')
        match = source_file_re.search(text)
        if not match:
            continue
        source_ref = json.loads('"' + match.group(1) + '"')
        source_path = (paper_path.parent / source_ref.replace('\\\\', '/')).resolve()
        source_key = str(source_path).casefold()
        output_dir = rel(paper_path.parent)
        previous = outputs.get(source_key)
        if previous is not None and previous != output_dir:
            raise RuntimeError(f'Duplicate processed outputs for source: {source_path}')
        outputs[source_key] = output_dir
    return outputs


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def inventory():
    entries = csv_rows(ROOT / 'data/original_downloads/entries.csv')
    records = csv_rows(ROOT / 'data/original_downloads/files.csv')
    grouped = collections.defaultdict(list)
    for record in records:
        if record['year'] == '2026' and record['file_type'] == '原始附件' and record['status'] != '下载失败':
            grouped[record['entry_key']].append(record)
    diagnostics_path = WORK / 'source_diagnostics_2026.json'
    diagnostics = {r['entry_key']: r['sources'] for r in read_json(diagnostics_path)} if diagnostics_path.is_file() else {}
    tasks, skipped = [], []
    existing_outputs = existing_output_by_source()
    for entry in sorted((e for e in entries if e['year'] == '2026'), key=lambda e: int(e['sequence'])):
        if entry['status'] != '已获得原始附件':
            skipped.append(entry['entry_key'])
            continue
        sources, hashes = [], {}
        for record in grouped[entry['entry_key']]:
            path = ROOT / record['local_path'].replace('\\', '/')
            path.resolve().relative_to(ROOT.resolve())
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError('Missing original: ' + str(path))
            actual = digest(path)
            if record.get('sha256') and actual != record['sha256']:
                raise RuntimeError('Source hash mismatch: ' + str(path))
            sources.append(rel(path))
            hashes[rel(path)] = actual
        if not sources:
            raise RuntimeError('No original: ' + entry['entry_key'])
        output_dir = next((existing_outputs[str((ROOT / source).resolve()).casefold()]
                           for source in sources
                           if str((ROOT / source).resolve()).casefold() in existing_outputs), None)
        if output_dir is None:
            output_dir = (Path('parsed') / entry['subject'] / str(entry['year'])
                          / entry['kind'] / Path(sources[0]).name).as_posix()
        metadata = {'id': entry['entry_key'], 'title': entry['title'], 'year': 2026,
                    'kind': entry['kind'], 'subject': entry['subject'], 'district': entry['district']}
        if entry['region_or_school'] != entry['district']:
            metadata['school'] = entry['region_or_school']
        if entry['variant']:
            metadata['variant'] = entry['variant']
        tasks.append({'entry_key': entry['entry_key'], 'sequence': int(entry['sequence']),
                      'source_files': sources, 'source_hashes': hashes,
                      'answer_files': [], 'auxiliary_files': [],
                      'output_dir': output_dir, 'paper_metadata': metadata,
                      'source_diagnostics': diagnostics.get(entry['entry_key'], []),
                      'complexity': 'complex' if any(r.get('scanned_or_low_text') for r in diagnostics.get(entry['entry_key'], []))
                      or entry['subject'] not in ['语文', '英语', '政治', '历史'] else 'normal'})
    if len({t['output_dir'] for t in tasks}) != len(tasks):
        raise RuntimeError('Output path collision')
    return tasks, skipped


@contextmanager
def state_lock():
    """Share the status write lock with checkpoint finalization processes."""
    import msvcrt
    with LOCK:
        WORK.mkdir(exist_ok=True)
        with (WORK / 'status.write.lock').open('a+b') as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + 300
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Timed out waiting for status write lock')
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def task_lock(task):
    """Serialize a paper writer with external checkpoint finalization."""
    import msvcrt
    directory = WORK / 'paper_locks'
    directory.mkdir(exist_ok=True)
    with (directory / f"{task['sequence']:03d}.lock").open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + 300
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another writer holds this paper: ' + task['entry_key'])
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def update(key=None, **fields):
    with state_lock():
        status = read_json(STATUS)
        batch = status['full_batch']
        if key is None:
            batch.update(fields)
        else:
            batch['tasks'][batch['index'][key]].update(fields, updated_at=now())
        batch['status_counts'] = dict(collections.Counter(t['status'] for t in batch['tasks']))
        batch['updated_at'] = now()
        atomic_json(STATUS, status)


def saved_task(key):
    with LOCK:
        status = read_json(STATUS)['full_batch']
        return status['tasks'][status['index'][key]]


def validate(task, report_path, local_validator=None):
    paper_path = ROOT / task['output_dir'] / 'paper.json'
    if local_validator is None:
        process = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/validate_exam_paper.py'),
                                  str(paper_path), '--schema', str(ROOT / 'docs/question-json-v1.schema.json')],
                                 cwd=WORK, capture_output=True, encoding='utf-8', errors='replace', timeout=300)
        try:
            report = json.loads(process.stdout)
        except ValueError:
            raise RuntimeError('Validator failed: ' + (process.stdout + process.stderr)[-2000:])
        validator_ok = process.returncode == 0
    else:
        report = local_validator(paper_path)
        validator_ok = report['valid']
    data = read_json(paper_path)
    errors = report.setdefault('errors', [])
    for name, expected in task['paper_metadata'].items():
        if data.get('paper', {}).get(name) != expected:
            errors.append('Metadata mismatch: ' + name)
    source = data.get('source', {})
    allowed = {(ROOT / p).resolve() for p in task['source_files']}
    paths = [source.get('file', '')] + [a.get('file', '') for a in source.get('answer_files', [])]
    for value in paths:
        if (paper_path.parent / value).resolve() not in allowed:
            errors.append('Source not in assigned task: ' + value)
    primary = (paper_path.parent / source.get('file', '')).resolve()
    if primary.is_file() and primary.suffix.lower() == '.pdf':
        import fitz
        with fitz.open(primary) as pdf:
            if source.get('page_count') != len(pdf):
                errors.append(f'Source page_count mismatch: {source.get("page_count")} != {len(pdf)}')
    for path, expected in task['source_hashes'].items():
        if digest(ROOT / path) != expected:
            errors.append('Original changed: ' + path)
    base = (paper_path.parent / 'assets').resolve()
    for aid, asset in data.get('assets', {}).items():
        p = (paper_path.parent / asset.get('path', '')).resolve()
        if not p.is_relative_to(base):
            errors.append('Asset outside assets/: ' + aid)
        if not asset.get('sha256'):
            errors.append('Missing asset SHA-256: ' + aid)
    # The legacy validator does not traverse shared option sets for asset references.
    def referenced_assets(value):
        if isinstance(value, dict):
            if isinstance(value.get('asset'), str):
                yield value['asset']
            for nested in value.values():
                yield from referenced_assets(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from referenced_assets(nested)
    for aid in set(referenced_assets(data)) - set(data.get('assets', {})):
        errors.append('Undefined asset anywhere in paper: ' + aid)
    report['valid'] = validator_ok and not errors
    atomic_json(report_path, report)
    if not report['valid']:
        raise RuntimeError('Validation failed: ' + json.dumps(errors, ensure_ascii=False)[:4000])
    return report


def snapshot(task):
    out = ROOT / task['output_dir']
    return {rel(p): digest(p) for p in out.rglob('*') if p.is_file()}


def verdict(text):
    """Parse explicit decisions; ambiguous/negated decisions can never accept."""
    candidates = []
    fenced = False
    for line in text.lower().splitlines():
        line = line.strip()
        if line.startswith('```'):
            fenced = not fenced
            continue
        if fenced or line.startswith('>'):
            continue
        clean = re.sub(r'[*`]', '', line)
        clean = re.sub(r'^#{1,6}\s*', '', clean)
        tagged = re.match(r'^(?:最终结论|最终意见|复核结论|结论)\s*[:：]\s*(.*)$', clean)
        value = tagged.group(1) if tagged else clean
        tokens = re.findall(r'\b(accept|reject|blocked)\b', value)
        direct = re.match(r'^(accept|reject|blocked)(?:\s*$|\s*[:：（(—–-])', value)
        if tagged:
            candidates.append(direct.group(1) if direct and len(tokens) == 1 else 'unknown')
        elif direct and len(tokens) == 1:
            candidates.append(direct.group(1))
    return candidates[-1] if candidates else 'unknown'


def results(log_path):
    texts, errors, sessions, models = [], [], [], set()
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('sessionID'):
            sessions.append(event['sessionID'])
        part = event.get('part', {})
        if event.get('type') == 'text' and part.get('text'):
            texts.append(part['text'])
        if event.get('type') == 'error':
            errors.append(event.get('error', event))
        if part.get('model'):
            models.add(str(part['model']))
    return {'session_id': sessions[0] if sessions else None, 'text': texts[-1] if texts else '',
            'errors': errors, 'models_in_events': sorted(models)}


def prepare_review_pages(task, directory):
    """Render the assigned PDFs here so visual evidence has known provenance."""
    import fitz
    pages = []
    for source_index, source in enumerate(task['source_files'], 1):
        path = ROOT / source
        if path.suffix.lower() != '.pdf':
            continue
        image_dir = directory / 'review_pages' / str(source_index)
        image_dir.mkdir(parents=True, exist_ok=True)
        with fitz.open(path) as pdf:
            for number, page in enumerate(pdf, 1):
                image = image_dir / f'page_{number:03d}.png'
                page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image)
                pages.append({'source': source, 'source_sha256': task['source_hashes'][source],
                              'page': number, 'image': rel(image), 'image_sha256': digest(image)})
    manifest = directory / 'review_pages.json'
    atomic_json(manifest, pages)
    return manifest


def visual_audit(task, log_path, manifest=None):
    """Require logged image reads, rather than trusting the review summary alone."""
    paths = set()
    forbidden = []
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') != 'tool_use':
            continue
        part = event.get('part', {})
        tool = part.get('tool', '').lower()
        if tool in ['webfetch', 'websearch', 'web_fetch', 'web_search', 'browser']:
            forbidden.append(tool)
        state = part.get('state', {})
        if tool == 'read' and state.get('status') == 'completed':
            inputs = state.get('input', {})
            value = inputs.get('path', inputs.get('filePath', inputs.get('file_path', '')))
            if isinstance(value, str) and value:
                p = Path(value)
                paths.add((ROOT / p).resolve())
    paper_path = ROOT / task['output_dir'] / 'paper.json'
    data = read_json(paper_path)
    assets = {(paper_path.parent / a['path']).resolve() for a in data.get('assets', {}).values()}
    missing = sorted(str(p) for p in assets - paths)
    rendered_pages = [p for p in paths - assets if p.suffix.lower() in ['.png', '.jpg', '.jpeg', '.webp']]
    source = (paper_path.parent / data['source']['file']).resolve()
    expected_pages = data['source'].get('page_count', 0) if source.suffix.lower() == '.pdf' else 0
    if forbidden:
        raise RuntimeError('Review used forbidden online tools: ' + ', '.join(forbidden))
    if missing:
        raise RuntimeError('Review lacks actual image reads for assets: ' + ', '.join(missing))
    if manifest is not None:
        for page in read_json(manifest):
            image = ROOT / page['image']
            if digest(image) != page['image_sha256'] or image.resolve() not in paths:
                raise RuntimeError('Review lacks an unchanged assigned page image: ' + page['image'])
    elif source.suffix.lower() == '.pdf' and len(rendered_pages) < expected_pages:
        raise RuntimeError(f'Review lacks page image reads: {len(rendered_pages)}/{expected_pages}')
    if source.suffix.lower() == '.docx' and not paths:
        raise RuntimeError('DOCX review contains no successful file reads')
    return {'asset_reads': len(assets), 'rendered_page_reads': len(rendered_pages), 'source_pdf_read': source in paths}


def invoke(task, stage, prompt, directory):
    global TOOL_FAILURES
    if STOP.is_set():
        raise BatchPaused('Batch paused after repeated tool/service failures')
    prompt_path = directory / (stage + '.md')
    log_path = directory / (stage + '.jsonl')
    prompt_path.write_text(prompt, encoding='utf-8')
    # Private server per invocation: killing the process tree must also stop the
    # session, otherwise abandoned sessions keep writing the same output paths.
    command = [OPENCODE, 'run', '--standalone', '--model', MODEL, '--format', 'json',
               '--title', f"2026 {task['paper_metadata']['subject']} {task['sequence']:03d} {stage}",
               '--file', str(prompt_path), '严格执行附件任务，仅处理指定一卷，禁止联网和解题。']
    update(task['entry_key'], active_stage=stage, active_log=rel(log_path), model=MODEL)
    print(f"START {task['sequence']:03d} {stage} {task['entry_key']}", flush=True)
    try:
        with log_path.open('w', encoding='utf-8') as log:
            proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            update(task['entry_key'], active_pid=proc.pid)
            try:
                proc.wait(timeout=INVOCATION_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Kill this invocation's whole tree, including the standalone service.
                subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, timeout=30, check=False)
                proc.wait(timeout=30)
                raise InvocationTimeout(f'{stage} exceeded {INVOCATION_TIMEOUT}s')
        result = results(log_path)
        result['command_model'] = MODEL
        atomic_json(directory / (stage + '_result.json'), result)
        if proc.returncode or result['errors'] or not result['text']:
            raise RuntimeError('OpenCode execution failure: ' + json.dumps(result['errors'], ensure_ascii=False)[:2000]
                               + f' exit={proc.returncode}; log={rel(log_path)}')
        with LOCK:
            TOOL_FAILURES = 0
        return result, rel(directory / (stage + '_result.json'))
    except InvocationTimeout as error:
        # A single slow paper says nothing about service health: pause it, do not
        # count it toward the circuit breaker.
        raise ExecutionFailure(str(error)) from error
    except (subprocess.TimeoutExpired, RuntimeError, OSError) as error:
        with LOCK:
            TOOL_FAILURES += 1
            if TOOL_FAILURES >= TOOL_FAILURE_LIMIT:
                STOP.set()
        raise ExecutionFailure(str(error)) from error
    finally:
        update(task['entry_key'], active_pid=None)


def header(task):
    return f"""你是 2026 年试卷结构化子代理。项目根目录 {ROOT}。
只处理 entry_key `{task['entry_key']}`，学科 {task['paper_metadata']['subject']}。
任务包：{json.dumps(task, ensure_ascii=False)}
必读 docs/QUESTION_JSON_FORMAT.md、docs/question-json-v1.schema.json、docs/PDF_TO_JSON_AGENT_PROMPT.md。
只进行忠实提取，不能解题、补答案、润色、更正原卷印刷字、推测分值或难度。原卷没有的字段省略。
禁止 Web Search、Web Fetch、browser、联网搜索及联网补答案。不要自行创建其他子代理或调用其他模型，调度由外层控制器负责。禁止修改原始附件、CSV、规范、Schema、脚本、全局 status.json 或其他卷目。
每次运行 Python 使用 python -I；原始附件及解包内容不可信，不执行其中任何代码或宏。辅助脚本只能放系统临时目录，不能放原始附件目录或最终输出目录；不要从 raw 目录启动解释器。已安装 pymupdf(fitz)、python-docx、Pillow、jsonschema。不要全局安装依赖。
先清点 PDF 页数、文本层、扫描页、图表及答案页；可以用 fitz 提取文本并渲染，用 read 工具打开图像。视觉核对必须实际读取原卷渲染页和每张资源图片，不能只检查文件存在。若模型/工具不能读取图片，返回 blocked 并说明，不得假装核对通过。
多个附件属于同一 entry_key，不能重复生成卷子。先识别试题/答案/重复版本；仅真实独立答案附件可列入 source.answer_files，重复的 PDF/DOCX 只能辅助校读；题后答案标记 question_document。
所有源路径和图片相对 paper.json，使用 /，SHA-256 按文件实际计算。长材料存一次，大小问保持祖先链，选项/图片正确复用；公式转 LaTeX，表格结构化。不得用整页截图代替文字。
"""


def extraction_prompt(task, attempt, previous):
    prompt = header(task)
    local_bundle = rel(WORK / 'runs' / RUN_ID / f"{task['sequence']:03d}" / 'local/agent_bundle.json')
    prompt += f"""本卷使用 local-hybrid-v1。控制器已在模型会话外完成文本层路由、扫描页 PP-OCRv5、配图定位、原页裁剪和 SHA-256。先读取 {local_bundle}。
若 mode=pdf_local：逐页使用 pages 的 text 和 image，禁止重新运行 OCR、重新渲染整卷或重新裁剪已有候选。assets 中 id/path/page/bbox/sha256 可直接登记到 paper.json；q_hint 只是提示，figure/table candidates 必须打开原页和候选图核对，不得照单全收。表格候选只有框，仍需忠实读取单元格。OCR 不确定处放大原页，不得猜测。只有候选缺失或不完整时才另行补图。
若 mode=agent_only：该主文件不是 PDF，本次 Benchmark 的本地路线不适用，按原规范直接读取源文件；不要伪称已完成本地 OCR。
"""
    if previous:
        prompt += f'这是第 {attempt} 次返修，先完整读取独立复核报告 {previous}，逐项修复，然后重新逐页检查整卷。禁止删题、删材料、删图或删答案以通过验收。\n'
    prompt += f"""实际生成 {task['output_dir']}/paper.json 和确有需要的 assets。输出目录只保留 paper.json 和实际引用的资源，不得在里面放脚本或页图草稿。
逐页逐题提取全卷，保留原题号、最小小问、完整材料、选项、原答案、解析及原卷明确分值。答案与题干的标点必须照原卷：原卷在“（x分）”前没有句号的，不得补句号；原卷有句号、冒号、答空下划线的，不得删改。实际打开并检查每张图的完整性、方向、文字和归属。extraction.status 只能是 draft 或 needs_review，不得自标 verified。
实际运行 python -I scripts/validate_exam_paper.py "{task['output_dir']}/paper.json" --schema docs/question-json-v1.schema.json，修好 Schema、路径、哈希、引用及同级 ID 问题。
最后简短返回输出路径、顶层题数/叶子数/材料数/图片数、校验结果、实际视觉核对范围及疑点；确有工具阻塞返回独立一行 blocked。不要粘回整份 JSON。
"""
    return prompt


def review_prompt(task, previous):
    prompt = header(task)
    prompt += f"""你的角色是独立只读复核者，与提取/返修使用不同会话。不得修改 paper.json、assets 或任何项目文件；只允许在系统临时目录渲染原卷。复核 {task['output_dir']}/paper.json 及全部 assets。
"""
    if previous:
        prompt += f'先读取历史复核报告 {previous}，验证其中全部问题已修复，但仍需完整复核全卷。\n'
    prompt += f"""实际逐页打开原卷渲染图并与 paper.json 对照全部题目、最小小问、选项、完整长材料、公式、表格、原答案及分值。逐张实际打开全部 assets 检查清晰度、裁剪、方向、文字及归属，尤其坐标轴、化学式、图例和跨页文字。严查无自编答案，无改写或静默更正原卷；答案和题干末尾的句号、冒号、答空下划线必须与原卷逐处一致，补句号或删标点都算静默更正。核对每个叶子可继承完整上下文。
独立运行 python -I scripts/validate_exam_paper.py "{task['output_dir']}/paper.json" --schema docs/question-json-v1.schema.json，检查 SHA-256、相对路径、同级 ID 和所有引用。仅通过 Schema 不等于 accept。
最后使用独立一行 accept、reject 或 blocked。accept 必须说明实际核对页数、题目/叶子/图片数及范围；reject 列出具体页码、题号、字段、资源 ID 和原因；工具或模型不能读图则 blocked。严禁仅凭历史报告或提取代理总结验收，严禁修改提取状态。
"""
    return prompt


def prepare_local_extraction(task, directory):
    """Run deterministic OCR/layout outside the model session; never hide failure."""
    local = directory / 'local'
    local.mkdir(parents=True, exist_ok=True)
    task_path = local / 'task.json'
    atomic_json(task_path, task)
    script = ROOT / '_orchestration/local_extraction.py'
    command = [sys.executable, '-I', str(script), '--task', str(task_path),
               '--output', str(local), '--assets-root', str(ROOT / task['output_dir']),
               '--weights', str(ROOT / 'data/models/question_ocr'), '--threads', '4']
    update(task['entry_key'], active_stage='local_prepare', extraction_engine='local_hybrid_v1')
    with (local / 'prepare.log').open('w', encoding='utf-8') as log:
        try:
            process = subprocess.run(command, cwd=WORK, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=INVOCATION_TIMEOUT, check=False)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f'Local OCR exceeded {INVOCATION_TIMEOUT}s; see {rel(local / "prepare.log")}') from error
    if process.returncode:
        raise RuntimeError(f'Local OCR failed with exit={process.returncode}; see {rel(local / "prepare.log")}')
    bundle_path = local / 'source_bundle.json'
    if not bundle_path.is_file():
        raise RuntimeError('Local OCR did not produce source_bundle.json')
    bundle = read_json(bundle_path)
    update(task['entry_key'], extraction_engine=bundle['mode'], local_stats=bundle['stats'])
    return {'mode': bundle['mode'], 'stats': bundle['stats']}


def prune_local_assets(task, directory):
    """Remove only unreferenced files created by this local stage, if unchanged."""
    local = directory / 'local'
    bundle = read_json(local / 'source_bundle.json')
    removed, preserved = [], []
    if bundle.get('mode') == 'pdf_local':
        paper_path = ROOT / task['output_dir'] / 'paper.json'
        data = read_json(paper_path)
        referenced = {asset.get('path') for asset in data.get('assets', {}).values()}
        base = (ROOT / task['output_dir']).resolve()
        assets_base = (base / 'assets').resolve()
        for record in bundle.get('assets', []):
            if not record.get('created_by_pipeline') or record['path'] in referenced:
                continue
            path = (base / record['path']).resolve()
            path.relative_to(assets_base)
            if path.is_file() and digest(path) == record['sha256']:
                path.unlink()
                removed.append(record['path'])
            elif path.exists():
                preserved.append(record['path'])
    report = {'removed_unreferenced': removed, 'preserved_modified': preserved}
    atomic_json(local / 'prune_report.json', report)
    return report


def process(task):
    with task_lock(task):
        return process_locked(task)


def process_locked(task):
    key = task['entry_key']
    item = saved_task(key)
    if item['status'] in ['verified', 'blocked']:
        return
    if STOP.is_set():
        return
    directory = WORK / 'runs' / RUN_ID / f"{task['sequence']:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    paper = ROOT / task['output_dir'] / 'paper.json'
    previous = item.get('review_report')
    attempt = item.get('retry_count', 0)
    if attempt > MAX_RETRY:
        update(key, status='blocked', blocked_reason='Prior repair limit reached; explicit targeted repair required')
        print(f'BLOCKED {key} prior repair limit reached', flush=True)
        return
    # Existing drafts are reviewed first; never discard work on restart.
    review_first = paper.is_file() and item.get('resume_stage') != 'needs_review'
    update(key, status='assigned', started_at=now())
    try:
        while attempt <= MAX_RETRY:
            if STOP.is_set():
                update(key, status='paused', resume_stage='in_review' if paper.is_file() else 'assigned')
                return
            if not review_first:
                prepare_local_extraction(task, directory)
                result, _ = invoke(task, f'extract_{attempt}', extraction_prompt(task, attempt, previous), directory)
                if verdict(result['text']) == 'blocked':
                    raise RuntimeError('Extraction tool/vision blocked: ' + result['text'][-2000:])
                prune_local_assets(task, directory)
                update(key, extraction_session=result['session_id'])
            review_first = False
            try:
                report = validate(task, directory / f'validate_{attempt}.json')
            except RuntimeError as error:
                previous = rel(directory / f'validation_feedback_{attempt}.json')
                atomic_json(ROOT / previous, {'verdict': 'reject', 'errors': [str(error)]})
                if attempt == MAX_RETRY:
                    raise
                attempt += 1
                update(key, status='needs_review', retry_count=attempt, review_report=previous, resume_stage='needs_review')
                continue
            update(key, status='in_review', stats=report, retry_count=attempt, resume_stage='in_review')
            before = snapshot(task)
            page_manifest = prepare_review_pages(task, directory)
            prompt = review_prompt(task, previous)
            prompt += f'\n控制器已按本卷原件生成全部 PDF 页图，清单在 {rel(page_manifest)}。必须逐个用 read 工具打开清单中每个 image 原路径，不要只看缩略图、拼图或替代路径。清单记录了来源和图片哈希；仍可另行放大疑点。\n'
            review, previous = invoke(task, f'review_{attempt}', prompt, directory)
            if snapshot(task) != before:
                raise ReviewerMutation('Read-only reviewer modified output; review invalid')
            choice = verdict(review['text'])
            update(key, review_session=review['session_id'], review_result=choice, review_report=previous)
            if choice == 'accept':
                proof = visual_audit(task, directory / f'review_{attempt}.jsonl', page_manifest)
                atomic_json(directory / f'visual_evidence_{attempt}.json', proof)
                data = read_json(paper)
                original_state = data['extraction']['status']
                data['extraction']['status'] = 'verified'
                atomic_json(paper, data)
                try:
                    report = validate(task, directory / 'validate_verified.json')
                except Exception:
                    data['extraction']['status'] = original_state
                    atomic_json(paper, data)
                    raise
                update(key, status='verified', extraction_status='verified', stats=report,
                       accepted_paper_sha256=digest(paper), completed_at=now())
                print(f'VERIFIED {task["sequence"]:03d} {key}', flush=True)
                return
            if choice == 'blocked':
                raise RuntimeError('Independent review blocked: ' + review['text'][-2500:])
            if choice == 'unknown':
                raise RuntimeError('Independent review returned no explicit verdict: ' + previous)
            if attempt == MAX_RETRY:
                update(key, status='blocked', blocked_reason='Review rejected after maximum repairs', error_kind='content', retry_count=attempt)
                print(f'BLOCKED {key} review rejected; {previous}', flush=True)
                return
            attempt += 1
            update(key, status='needs_review', retry_count=attempt, resume_stage='needs_review')
    except (ExecutionFailure, BatchPaused) as error:
        stage = saved_task(key).get('active_stage', '')
        update(key, status='paused', execution_error=str(error), error_kind='execution',
               resume_stage='in_review' if stage.startswith('review_') else 'assigned', retry_count=attempt)
        print(f'PAUSED {key} {str(error)[:500]}', flush=True)
    except ReviewerMutation as error:
        # The draft can no longer be trusted: re-extract it on the next resume.
        update(key, status='needs_review', blocked_reason=str(error), error_kind='reviewer_mutation',
               resume_stage='needs_review', retry_count=attempt)
        print(f'REVIEWER_MUTATED {key} {str(error)[:300]}', flush=True)
    except Exception as error:
        update(key, status='blocked', blocked_reason=str(error), error_kind='content', retry_count=attempt)
        print(f'BLOCKED {key} {str(error)[:500]}', flush=True)


def proven_verified(task, candidates):
    paper = ROOT / task['output_dir'] / 'paper.json'
    current_hash = digest(paper)
    baseline_path = WORK / 'verified_baseline_manifest.json'
    baseline = read_json(baseline_path) if baseline_path.is_file() else {}
    if baseline.get(task['entry_key'], {}).get('paper_sha256') == current_hash:
        return True
    for candidate in candidates:
        if candidate.get('accepted_paper_sha256') != current_hash:
            continue
        path = candidate.get('review_report')
        if not path or not (ROOT / path).is_file():
            continue
        review = read_json(ROOT / path)
        if verdict(review.get('text', '')) == 'accept' and review.get('session_id'):
            if review['session_id'] != candidate.get('extraction_session'):
                return True
    return False


def check_paper(task, candidates):
    """Validate each existing paper, including its source and assets."""
    paper = ROOT / task['output_dir'] / 'paper.json'
    if not paper.is_file():
        return {'state': 'missing'}
    try:
        report_path = WORK / 'runs' / RUN_ID / f"{task['sequence']:03d}" / 'validate_existing.json'
        return {'state': 'valid', 'data': read_json(paper), 'report': validate(task, report_path)}
    except Exception as error:
        return {'state': 'failed', 'error': str(error)}


def initialize(tasks, skipped, parallel):
    status = read_json(STATUS)
    old = {t['entry_key']: t for t in status.get('full_batch', {}).get('tasks', [])}
    chinese = {t['entry_key']: t for t in status.get('chinese_batch', {}).get('tasks', [])}
    pilot = {t['entry_key']: t for t in status.get('tasks', [])}
    batch_tasks = []
    prepared = {}
    for task in tasks:
        prepared[task['entry_key']] = [group[task['entry_key']] for group in [old, chinese, pilot]
                                       if task['entry_key'] in group]
    # Validation spawns a subprocess per paper; run them together instead of serially.
    checked = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(check_paper, task, prepared[task['entry_key']]): task['entry_key'] for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            checked[futures[future]] = future.result()
    for task in tasks:
        key = task['entry_key']
        candidates = prepared[key]
        previous = candidates[0] if candidates else {}
        # A completed checkpoint can be newer than a stale all-subject record.
        accepted = [c for c in candidates if c.get('status') == 'verified' and c.get('accepted_paper_sha256')]
        if accepted:
            previous = accepted[-1]
        item = {'entry_key': key, 'sequence': task['sequence'], 'subject': task['paper_metadata']['subject'],
                'output_dir': task['output_dir'], 'status': 'pending', 'retry_count': previous.get('retry_count', 0)}
        for name in ['review_report', 'review_result', 'blocked_reason', 'resume_stage',
                     'accepted_paper_sha256', 'extraction_session', 'review_session', 'error_kind']:
            if name in previous:
                item[name] = previous[name]
        check = checked[key]
        validation_failed = False
        if check['state'] == 'valid':
            if check['data']['extraction']['status'] == 'verified' and proven_verified(task, candidates):
                item.update(status='verified', extraction_status='verified', stats=check['report'], review_result='resume_skip')
                item.pop('blocked_reason', None)
            else:
                item.update(resume_stage='in_review')
        elif check['state'] == 'failed':
            validation_failed = True
            item.update(resume_stage='needs_review', resume_error=check['error'])
        if (previous.get('status') == 'blocked' and item['status'] != 'verified'
                and not (previous.get('error_kind') == 'reviewer_mutation'
                         and previous.get('blocked_reason', '').startswith('Read-only reviewer modified output'))):
            item.update(status='blocked', blocked_reason=previous.get('blocked_reason', 'Preserved prior block'))
        if previous.get('status') == 'verified' and validation_failed:
            item.update(status='blocked', blocked_reason='Previously verified output failed local validation')
        batch_tasks.append(item)
    status['full_batch'] = {'model': MODEL, 'effort': 'high', 'max_parallel': parallel,
                            'max_retry': MAX_RETRY, 'run_id': RUN_ID, 'total': len(tasks),
                            'started_at': now(), 'updated_at': now(), 'paused': False,
                            'skipped_no_original': skipped, 'index': {t['entry_key']: i for i, t in enumerate(tasks)},
                            'tasks': batch_tasks,
                            'status_counts': dict(collections.Counter(t['status'] for t in batch_tasks))}
    # Keep the original pilot/chinese history intact. full_batch is the current all-subject state.
    atomic_json(STATUS, status)


def guard_existing_writers():
    command = "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(opencode|python|pythonw)\\.exe$' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Depth 2"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=30)
    if result.returncode:
        raise RuntimeError('Cannot check existing paper writers: ' + result.stderr[-1000:])
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    rows = [rows] if isinstance(rows, dict) else rows
    blocked = []
    for row in rows:
        text = (row.get('CommandLine') or '').lower().replace('\\', '/')
        if 'opencode' in text and ' run ' in text and '_orchestration/runs/' in text:
            if str(ROOT).lower().replace('\\', '/') in text:
                blocked.append(row['ProcessId'])
        if 'run_2026_chinese_batch.py' in text:
            blocked.append(row['ProcessId'])
    if blocked:
        raise RuntimeError('Existing paper writers must finish before resuming: ' + str(blocked))


def main():
    while (WORK / 'launch_hold').exists():
        print('QUEUE_WAIT controller fixes are being verified', flush=True)
        time.sleep(5)
    parser = argparse.ArgumentParser()
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--keys', nargs='*')
    parser.add_argument('--exclude-keys', nargs='*')
    parser.add_argument('--allow-unverified-pilot', action='store_true',
                        help='Start despite a pilot paper still awaiting targeted repair; that paper is reviewed in the queue.')
    args = parser.parse_args()
    if not 1 <= args.parallel <= 96:
        parser.error('--parallel must be 1..96')
    WORK.mkdir(exist_ok=True)
    lock_file = WORK / 'controller.lock'
    # Windows releases the OS lock if the controller crashes; stale files are harmless.
    import msvcrt
    with lock_file.open('a+b') as controller_lock:
        controller_lock.seek(0)
        if lock_file.stat().st_size == 0:
            controller_lock.write(b'0')
            controller_lock.flush()
        controller_lock.seek(0)
        try:
            msvcrt.locking(controller_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Another all-subject controller is already running')
        tasks, skipped = inventory()
        atomic_json(WORK / 'tasks_2026.json', tasks)
        counts = dict(collections.Counter(t['paper_metadata']['subject'] for t in tasks))
        print(f'INVENTORY {len(tasks)} eligible; {len(skipped)} skipped; {counts}', flush=True)
        if args.prepare_only:
            return
        if not OPENCODE:
            raise SystemExit('OpenCode missing')
        # Check real processes, rather than refusing forever on stale saved states.
        guard_existing_writers()
        # The structural pilot must be complete before broad processing.
        pilots = read_json(STATE / 'pilot_tasks.json')
        for pilot in pilots:
            p = ROOT / pilot['output_dir'] / 'paper.json'
            if not p.is_file() or read_json(p).get('extraction', {}).get('status') != 'verified':
                if args.allow_unverified_pilot:
                    print('PILOT_DEFERRED_TO_QUEUE ' + pilot['entry_key'], flush=True)
                    continue
                raise SystemExit('Pilot not verified: ' + pilot['entry_key'])
        backup = WORK / f'status_before_full_{RUN_ID}.json'
        shutil.copy2(STATUS, backup)
        with state_lock():
            initialize(tasks, skipped, args.parallel)
        excluded = set(args.exclude_keys or [])
        selected = [t for t in tasks if (not args.keys or t['entry_key'] in args.keys)
                    and t['entry_key'] not in excluded]
        print(f'QUEUE parallel={args.parallel} model={MODEL}', flush=True)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futures = [pool.submit(process, task) for task in selected]
                for future in concurrent.futures.as_completed(futures):
                    future.result()
        except KeyboardInterrupt:
            STOP.set()
            raise
        finally:
            update(paused=STOP.is_set(), finished_at=now())
        current = read_json(STATUS)['full_batch']
        atomic_json(WORK / 'latest_summary.json', {'run_id': RUN_ID, 'model': MODEL,
                    'max_parallel': args.parallel, 'total': len(tasks), 'counts': current['status_counts'],
                    'blocked': [t for t in current['tasks'] if t['status'] == 'blocked']})
        print('BATCH_ENDED ' + json.dumps(current['status_counts']), flush=True)


if __name__ == '__main__':
    main()
