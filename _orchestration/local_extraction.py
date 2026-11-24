"""Prepare deterministic OCR text and figure candidates for one exam task.

Run with Python -I. Source documents are data only. No network, reference JSON,
or production paper.json is read by this program.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_VERSION = 'local-hybrid-v1'
DPI = 210
QUESTION = re.compile(r'^\s*(\d{1,2})\s*[.．、](?!\s*\d)')


def load_ocr():
    spec = importlib.util.spec_from_file_location('production_local_ocr', Path(__file__).with_name('local_ocr.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def text_route(text):
    chars = sum(c.isalnum() for c in text)
    broken = sum(c in '�\x00' or '' <= c <= '' for c in text)
    return 'text_layer' if chars >= 80 and broken <= .02 * chars else 'paddle_ocr'


def question_anchors(lines, width):
    anchors = []
    for line in lines:
        match = QUESTION.match(line['text'])
        x0, y0, _, _ = line['bbox']
        if match and x0 <= .29 * width and 1 <= int(match[1]) <= 30:
            anchors.append((y0, int(match[1])))
    return sorted(anchors)


def assign_question(box, anchors):
    return next((number for y, number in reversed(anchors) if y <= box[1] + 20), None)


def pdf_lines(page, scale_x, scale_y):
    import pymupdf as fitz
    lines = []
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    for block in page.get_text('dict', flags=flags)['blocks']:
        for line in block.get('lines', []):
            text = ''.join(span['text'] for span in line['spans'])
            if text.strip():
                x0, y0, x1, y1 = line['bbox']
                lines.append({'text': text, 'bbox': [x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y]})
    return lines


def native_boxes(page, scale_x, scale_y):
    import pymupdf as fitz
    rectangles = {}
    area = page.rect.get_area()
    for metadata in page.get_image_info():
        rect = fitz.Rect(metadata['bbox']) * page.rotation_matrix
        rect &= page.rect
        if rect.is_empty or not .001 <= rect.get_area() / area <= .50 or min(rect.width, rect.height) < 15:
            continue
        key = tuple(round(value, 1) for value in rect)
        rectangles[key] = rect
    return [(r.x0 * scale_x, r.y0 * scale_y, r.x1 * scale_x, r.y1 * scale_y) for r in rectangles.values()]


def agent_view(bundle):
    asset_fields = ('id', 'path', 'kind', 'source', 'page', 'bbox', 'q_hint', 'sha256', 'detector', 'confidence')
    return {key: value for key, value in bundle.items() if key not in ('pages', 'assets')} | {
        'pages': [{key: value for key, value in page.items() if key != 'lines'} for page in bundle['pages']],
        'assets': [{key: value for key, value in asset.items() if key in asset_fields} for asset in bundle['assets']],
        'instructions': [
            'text 已按 route 提取；不要重新运行 OCR。',
            'figure_candidates 是候选而非已验收配图；q_hint 只供定位，必须按原卷核对归属。',
            '引用候选资产时使用给定 id/path/page/bbox/sha256，不要重新裁图。',
            'table_candidates 只有框，必须依据原页提取单元格，不得把表框当成表格结构化完成。',
            '所有文字、答案、表格和候选图仍需与原页核对；禁止解题、补答案或静默改字。',
        ],
    }


class Processor:
    def __init__(self, root, task, output, assets_root, weights, threads):
        self.root = root
        self.task = task
        self.output = output
        self.assets_root = assets_root
        self.weights = weights
        self.threads = threads
        self.ocr = None
        self.layout = None
        self.pages = []
        self.assets = []
        self.started = time.perf_counter()

    def engines(self):
        if self.ocr is None:
            module = load_ocr()
            self.ocr = module.OCREngine(self.weights, self.threads)
            self.layout = module.LayoutEngine(self.weights, self.threads)

    def process_pdf(self, source, source_index):
        import pymupdf as fitz
        from PIL import Image

        page_dir = self.output / 'pages'
        crop_dir = self.output / 'candidates'
        page_dir.mkdir(parents=True, exist_ok=True)
        crop_dir.mkdir(parents=True, exist_ok=True)
        with fitz.open(source) as document:
            for number, page in enumerate(document, 1):
                raw_text = page.get_text(sort=True)
                route = text_route(raw_text)
                pixmap = page.get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False, annots=False)
                image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
                image_path = page_dir / f's{source_index:02d}_p{number:03d}.png'
                image.save(image_path, compress_level=1)
                scale_x, scale_y = image.width / page.rect.width, image.height / page.rect.height
                if route == 'paddle_ocr':
                    self.engines()
                    text, lines = self.ocr.read(image)
                else:
                    text, lines = raw_text, pdf_lines(page, scale_x, scale_y)
                anchors = question_anchors(lines, image.width) if not page.rotation else []
                native = native_boxes(page, scale_x, scale_y) if route == 'text_layer' else []
                self.engines()
                detected = self.layout.detect(image)
                if native:
                    figures = [{'box': box, 'confidence': None, 'detector': 'pdf_native'} for box in native]
                    detector = 'pdf_native'
                else:
                    figures = [{'box': item['box'], 'confidence': item['confidence'], 'detector': 'doclayout_yolo'}
                               for item in detected if item['cls'] == 3]
                    detector = 'doclayout_yolo'
                tables = [{'source': self.task['source_files'][source_index - 1], 'page': number,
                           'bbox': [round(value, 3) for value in (
                               item['box'][0] / scale_x, item['box'][1] / scale_y,
                               item['box'][2] / scale_x, item['box'][3] / scale_y)],
                           'confidence': item['confidence'], 'detector': 'doclayout_yolo'}
                          for item in detected if item['cls'] == 5]
                figure_ids = []
                for index, figure in enumerate(figures, 1):
                    box = figure['box']
                    asset_id = f'a_s{source_index:02d}_p{number:03d}_f{index:02d}'
                    crop = image.crop(box)
                    master = crop_dir / f'{asset_id}.png'
                    crop.save(master)
                    with Image.open(master) as check:
                        check.verify()
                    record = {
                        'id': asset_id, 'path': f'assets/{asset_id}.png', 'kind': 'image',
                        'source': self.task['source_files'][source_index - 1], 'page': number,
                        'bbox': [round(value, 3) for value in (
                            box[0] / scale_x, box[1] / scale_y, box[2] / scale_x, box[3] / scale_y)],
                        'q_hint': assign_question(box, anchors), 'detector': figure['detector'],
                        'crop_sha256': digest(master), 'master_path': str(master),
                    }
                    if figure['confidence'] is not None:
                        record['confidence'] = figure['confidence']
                    self.assets.append(record)
                    figure_ids.append(asset_id)
                self.pages.append({
                    'source': self.task['source_files'][source_index - 1], 'source_sha256': self.task['source_hashes'][self.task['source_files'][source_index - 1]],
                    'page': number, 'page_count': len(document), 'route': route, 'text': text, 'lines': lines,
                    'image': image_path.relative_to(self.root).as_posix(), 'image_sha256': digest(image_path),
                    'page_size_pt': [page.rect.width, page.rect.height], 'page_size_px': list(image.size),
                    'rotation': page.rotation, 'figure_detector': detector, 'figure_candidates': figure_ids,
                    'table_candidates': tables,
                    'needs_review': (['核对 OCR 文字与公式'] if route == 'paddle_ocr' else [])
                                    + (['旋转页的自动题号提示已停用，需人工核对配图归属'] if page.rotation else []),
                })
                print(f'{self.task["entry_key"]} s{source_index} p{number}: {route}, '
                      f'{len(figure_ids)} figures, {len(tables)} tables', flush=True)

    def materialize(self):
        for record in self.assets:
            master = Path(record['master_path'])
            destination = self.assets_root / record['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                record['reused_existing'] = True
            else:
                shutil.copy2(master, destination)
                record['created_by_pipeline'] = True
            record['sha256'] = digest(destination)
            record.pop('master_path', None)
            record['master_path'] = str(master)

    def run(self):
        for source_name in self.task['source_files']:
            source = (self.root / source_name).resolve()
            source.relative_to(self.root)
            if not source.is_file() or digest(source) != self.task['source_hashes'][source_name]:
                raise RuntimeError(f'Source missing or hash mismatch: {source_name}')
        primary = self.task['source_files'][0]
        base = {
            'pipeline': PIPELINE_VERSION, 'entry_key': self.task['entry_key'],
            'source_hashes': self.task['source_hashes'], 'dpi': DPI,
            'ocr': 'PP-OCRv5 server det/rec lightweight ONNX', 'layout': 'DocLayout-YOLO DocStructBench ONNX',
            'threads': self.threads, 'pages': self.pages, 'assets': self.assets,
        }
        if Path(primary).suffix.lower() != '.pdf':
            base.update(mode='agent_only', stats={'reason': 'Primary source is not PDF; benchmarked local route does not apply'})
            return base
        self.assets_root.mkdir(parents=True, exist_ok=True)
        for index, source_name in enumerate(self.task['source_files'], 1):
            source = self.root / source_name
            if source.suffix.lower() == '.pdf':
                self.process_pdf(source, index)
        self.materialize()
        base.update(mode='pdf_local', stats={
            'pages': len(self.pages),
            'text_layer_pages': sum(page['route'] == 'text_layer' for page in self.pages),
            'ocr_pages': sum(page['route'] == 'paddle_ocr' for page in self.pages),
            'figure_candidates': len(self.assets),
            'table_candidates': sum(len(page['table_candidates']) for page in self.pages),
            'processing_s': round(time.perf_counter() - self.started, 3),
        })
        return base


def cache_valid(path, task):
    if not path.is_file():
        return None
    try:
        bundle = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None
    if bundle.get('pipeline') != PIPELINE_VERSION or bundle.get('source_hashes') != task['source_hashes']:
        return None
    for page in bundle.get('pages', []):
        image = ROOT / page['image']
        if not image.is_file() or digest(image) != page['image_sha256']:
            return None
    for asset in bundle.get('assets', []):
        master = Path(asset['master_path'])
        if not master.is_file() or digest(master) != asset['crop_sha256']:
            return None
    return bundle


def prepare(args):
    task = json.loads(args.task.read_text(encoding='utf-8'))
    args.output.mkdir(parents=True, exist_ok=True)
    source_path = args.output / 'source_bundle.json'
    agent_path = args.output / 'agent_bundle.json'
    bundle = cache_valid(source_path, task)
    if bundle is None:
        processor = Processor(ROOT, task, args.output, args.assets_root, args.weights, args.threads)
        bundle = processor.run()
        bundle['created_at'] = datetime.now(timezone.utc).isoformat()
    else:
        processor = Processor(ROOT, task, args.output, args.assets_root, args.weights, args.threads)
        processor.assets = bundle['assets']
        processor.materialize()
        bundle['cache_reused'] = True
    atomic_json(source_path, bundle)
    atomic_json(agent_path, agent_view(bundle))
    print(json.dumps({'entry_key': task['entry_key'], 'mode': bundle['mode'], 'stats': bundle['stats'],
                      'agent_bundle': str(agent_path)}, ensure_ascii=False), flush=True)
    return bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--assets-root', type=Path, required=True)
    parser.add_argument('--weights', type=Path, default=ROOT / 'data/models/question_ocr')
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    args.task = args.task.resolve()
    args.output = args.output.resolve()
    args.assets_root = args.assets_root.resolve()
    args.weights = args.weights.resolve()
    if not 1 <= args.threads <= 16:
        parser.error('--threads must be 1..16')
    prepare(args)


if __name__ == '__main__':
    main()
