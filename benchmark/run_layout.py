"""Offline DocLayout-YOLO figure/table location and lossless page-pixel crops."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from time import perf_counter

import numpy as np
import onnxruntime as ort
from PIL import Image

BASE = Path(__file__).resolve().parent
WORK = BASE / '.work'
MODEL = WORK / 'weights/layout_model/doclayout_yolo_docstructbench_imgsz1024.onnx'
QUESTION = re.compile(r'^\s*(\d{1,2})\s*[.．、](?!\s*\d)')


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def detect(session, image, threshold, classes=(3, 5)):
    width, height = image.size
    size = 1024
    factor = size / max(width, height)
    w, h = round(width * factor), round(height * factor)
    l, t = (size - w) // 2, (size - h) // 2
    square = Image.new('RGB', (size, size), (114, 114, 114))
    square.paste(image.resize((w, h), Image.Resampling.BILINEAR), (l, t))
    values = np.asarray(square, dtype=np.float32).transpose(2, 0, 1)[None] / 255
    prediction = session.run(None, {session.get_inputs()[0].name: np.ascontiguousarray(values)})[0][0]
    if prediction.shape != (300, 6):
        raise ValueError(f'Unexpected detection shape: {prediction.shape}')
    for x0, y0, x1, y1, conf, cls in prediction:
        if conf < threshold:
            continue
        if int(cls) not in classes:
            continue
        box = (max(0, math.floor((x0-l) * width / w)), max(0, math.floor((y0-t) * height / h)),
               min(width, math.ceil((x1-l) * width / w)), min(height, math.ceil((y1-t) * height / h)))
        if box[0] >= box[2] or box[1] >= box[3]:
            continue
        yield int(cls), box, round(float(conf), 5)


def assign(box, lines, width):
    anchors = []
    for line in lines:
        m = QUESTION.match(line['text'])
        x0, y0, _, _ = line['bbox']
        # Left margin and top-level numeric question labels, never answer content.
        if m and x0 <= .29 * width and 1 <= int(m[1]) <= 30:
            anchors.append((y0, int(m[1])))
    return next((q for y, q in sorted(anchors, reverse=True) if y <= box[1] + 20), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--threshold', type=float, default=.25)
    args = parser.parse_args()
    pages = load(WORK / 'inventory.json')
    paddle = load(WORK / 'paddle_measurements.json')['rounds'][0]['pages']
    lines = {p['id']: p['lines'] for p in paddle}
    opts = ort.SessionOptions()
    opts.intra_op_num_threads, opts.inter_op_num_threads = args.threads, 1
    start = perf_counter()
    session = ort.InferenceSession(MODEL.read_bytes(), sess_options=opts, providers=['CPUExecutionProvider'])
    result = {'engine': 'DocLayout-YOLO DocStructBench ONNX CPU', 'threshold': args.threshold,
              'model_load_s': perf_counter() - start, 'threads': args.threads, 'pages': []}
    crops = WORK / 'layout_crops'
    crops.mkdir(exist_ok=True)
    for page in pages:
        key = page['id']
        began = perf_counter()
        with Image.open(WORK / 'pages' / (key + '.png')) as rendered:
            image = rendered.convert('RGB')
        sec_decode = perf_counter() - began
        began = perf_counter()
        candidates = list(detect(session, image, args.threshold))
        sec_infer = perf_counter() - began
        began = perf_counter()
        tables, figs = [], []
        for cls, box, conf in candidates:
            extents = (image.width, image.height) * 2
            bbox = [round(v * 1000 / size, 3) for v, size in zip(box, extents)]
            pdf_dimensions = page['page_size_pt'] * 2
            record = {'bbox': bbox, 'page_bbox_pt': [round(v * unit / 1000, 3) for v, unit in zip(bbox, pdf_dimensions)],
                      'page': page['page'], 'confidence': conf}
            if cls == 5:
                tables.append({**record, 'cells': []})  # only locate; no table cell model
            else:
                path = crops / f'{key}-{len(figs)+1:02d}.png'
                image.crop(box).save(path)
                with Image.open(path) as preview:
                    preview.verify()
                with path.open('rb') as f:
                    record.update(q=assign(box, lines[key], image.width), path=f'layout_crops/{path.name}',
                                  sha256=hashlib.file_digest(f, 'sha256').hexdigest())
                figs.append(record)
        sec_crop = perf_counter() - began
        prep = page['render_s'] + page['encode_s']
        result['pages'].append({'id': key, 'figures': figs, 'tables': tables, 'figure_s': sec_crop,
                                'table_s': 0, 'prep_s': prep, 'decode_s': sec_decode, 'det_s': sec_infer,
                                'processing_s': prep + sec_decode + sec_infer + sec_crop})
        print(key, len(figs), 'figures;', len(tables), 'tables;', round(sec_infer, 3), 'detection seconds', flush=True)
    (WORK / 'layout_measurements.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
