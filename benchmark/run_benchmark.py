"""Measure local extraction without loading reference.json or contacting a service."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from time import perf_counter

import pymupdf as fitz
from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parent
WORK = BASE / '.work'
ROOT = BASE.parent
QUESTION = re.compile(r'^\s*(\d{1,2})\s*[.．、](?!\s*\d)')


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run(args, timeout=180, check=True):
    return subprocess.run(args, timeout=timeout, check=check, capture_output=True)


def windows_compile():
    win = Path(os.environ['WINDIR'])
    gcc = win / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    gac = win / 'Microsoft.NET/assembly/GAC_MSIL'
    md = win / 'System32/WinMetadata'
    refs = [next((gac / x).glob(f'*/{x}.dll')) for x in ('System.Runtime', 'System.Runtime.WindowsRuntime')]
    refs += [md / f'Windows.{x}.winmd' for x in ('Foundation', 'Storage', 'Media', 'Graphics', 'Globalization')]
    binary = WORK / 'WindowsOcr.exe'
    command = [str(gcc), '/nologo', '/target:exe', '/optimize+', f'/out:{binary}', *(f'/r:{x}' for x in refs), str(BASE / 'WindowsOcr.cs')]
    proc = run(command, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stdout.decode(errors='replace') + proc.stderr.decode(errors='replace'))
    return binary


def normalize_box(bbox, width, height):
    return [round(float(v) * 1000 / (width if i % 2 == 0 else height), 3) for i, v in enumerate(bbox)]


def get_question_anchors(page):
    anchors = []
    for block in page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block['lines']:
            text = ''.join(span['text'] for span in line['spans'])
            m = QUESTION.match(text)
            if m and 1 <= int(m[1]) <= 30:
                anchors.append((line['bbox'][1], int(m[1])))
    return sorted(anchors)


def measure_figures(item, page, save_png):
    dest = WORK / 'crops'
    if save_png:
        dest.mkdir(exist_ok=True)
    start = perf_counter()
    rectangles = {}
    area = page.rect.get_area()
    for metadata in page.get_image_info():
        rect = fitz.Rect(metadata['bbox']) * page.rotation_matrix
        rect &= page.rect
        if rect.is_empty or not .001 <= rect.get_area() / area <= .50 or min(rect.width, rect.height) < 15:
            continue  # suppress whole-page scans, backgrounds, and tiny marks
        key = tuple(round(x, 1) for x in rect)
        rectangles[key] = rect
    anchors = get_question_anchors(page) if not page.rotation else []
    assets = []
    for index, r in enumerate(sorted(rectangles.values(), key=lambda r: (r.y0, r.x0)), 1):
        q = next((q for y, q in reversed(anchors) if y <= r.y0 + 5), None)
        record = {'bbox': normalize_box(r, page.rect.width, page.rect.height), 'page_bbox_pt': list(r), 'page': item['page'], 'q': q}
        if save_png:
            name = f"{item['id']}-{index:02d}.png"
            path = dest / name
            page.get_pixmap(dpi=item['dpi'], clip=r, alpha=False, annots=False).save(path)
            with Image.open(path) as image:
                image.verify()
            with path.open('rb') as stream:
                record.update(path=f'crops/{name}', sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
        assets.append(record)
    return {'figure_s': perf_counter() - start, 'figures': assets}


def measure_tables(page):
    start = perf_counter()
    tables = [{'bbox': normalize_box(t.bbox, page.rect.width, page.rect.height), 'cells': t.extract()} for t in page.find_tables().tables]
    return {'table_s': perf_counter() - start, 'tables': tables}


def run_round(items, pdftotext, binary, assets, round_no):
    runs = {}
    for mode in ('pymupdf', 'pdftotext'):
        if mode == 'pdftotext' and not pdftotext:
            continue
        began = perf_counter()
        rows = []
        for item in items:
            source = ROOT / item['source']
            start = perf_counter()
            if mode == 'pymupdf':
                with fitz.open(source) as doc:
                    text = doc[item['page'] - 1].get_text(sort=True)
            else:
                n = str(item['page'])
                text = run([pdftotext, '-f', n, '-l', n, '-enc', 'UTF-8', '-layout', '-nopgbrk', str(source), '-']).stdout.decode('utf-8-sig')
            rows.append({'id': item['id'], 'text': text, 'seconds': perf_counter() - start})
        runs[mode] = {'pages': rows, 'wall_s': perf_counter() - began}
    for mode, folder in [('windows', 'pages'), ('windows_gray', 'gray')]:
        if not binary:
            continue
        inputs = [{'id': x['id'], 'path': str(WORK / folder / (x['id'] + '.png'))} for x in items]
        inpath = WORK / f'{mode}_input.json'
        outpath = WORK / f'{mode}_{round_no}.json'
        save(inpath, inputs)
        start = perf_counter()
        run([str(binary), str(inpath), str(outpath), 'zh-Hans-CN'])
        wall = perf_counter() - start
        obj = load(outpath)
        obj['wall_s'] = wall
        pre = {x['id']: x for x in items}
        for r in obj['pages']:
            src = pre[r['id']]
            r['seconds'] = r['decode_s'] + r['infer_s'] + src['render_s'] + src['encode_s']
            if mode == 'windows_gray':
                r['seconds'] += src['autocontrast_s']
        runs[mode] = obj
    if 'windows' in runs:
        # A one-stage gain estimate: derive the route from the actual measurements
        # without executing either bench arm a second time. Never read the GT tags.
        base = {x['id']: x for x in runs['pymupdf']['pages']}
        win = {x['id']: x for x in runs['windows']['pages']}
        hybrid = []
        for item in items:
            key = item['id']
            text = base[key]['text']
            chars = sum(c.isalnum() for c in text)
            broken = sum(c in '�\x00' or '' <= c <= '' for c in text)
            enough = chars >= 80 and broken <= .02 * chars
            page = dict(base[key] if enough else win[key])
            if not enough:
                page['seconds'] += base[key]['seconds']
            page['route'] = 'pymupdf' if enough else 'windows'
            hybrid.append(page)
        runs['hybrid'] = {'pages': hybrid, 'modeled_from_measured_stages': True}
    if assets:
        for item in items:
            with fitz.open(ROOT / item['source']) as doc:
                page = doc[item['page'] - 1]
                image_data = measure_figures(item, page, save_png=True)
                table_data = measure_tables(page)
            runs.setdefault('native_layout', {'pages': []})['pages'].append({'id': item['id'], **image_data, **table_data})
    return runs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--no-windows', action='store_true')
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    items = load(WORK / 'inventory.json')
    binary = None if args.no_windows else windows_compile()
    if binary:
        (WORK / 'gray').mkdir(exist_ok=True)
        for item in items:
            name = item['id'] + '.png'
            start = perf_counter()
            with Image.open(WORK / 'pages' / name) as raw:
                enhanced = ImageOps.autocontrast(raw.convert('L'), cutoff=1)
                enhanced.save(WORK / 'gray' / name, compress_level=1)
            item['autocontrast_s'] = perf_counter() - start
    pdftotext = shutil.which('pdftotext')
    version = run([pdftotext, '-v'], check=False) if pdftotext else None
    data = {'python': platform.python_version(), 'os': platform.platform(), 'logical_cpus': os.cpu_count(),
            'pymupdf_version': importlib.metadata.version('pymupdf'), 'pillow_version': importlib.metadata.version('pillow'),
            'pdftotext_version': ((version.stderr or version.stdout).decode(errors='replace').splitlines() or [''])[0] if version else None,
            'round_count': args.rounds, 'pages': items, 'rounds': []}
    for n in range(1, args.rounds + 1):
        result = run_round(items, pdftotext, binary, n == 1, n)
        data['rounds'].append(result)
        print('round', n, [(k, round(v.get('wall_s', sum(x.get('seconds', 0) for x in v['pages'])), 3)) for k, v in result.items()], flush=True)
    save(WORK / 'measurements.json', data)


if __name__ == '__main__':
    main()
