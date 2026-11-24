"""Run PP-OCRv5 server det/rec on local CPU using existing ONNX Runtime.

This lightweight axis-aligned DB/CTC adapter is not PaddleOCR-VL, RapidOCR, or
an exact reproduction of PaddleOCR's full SDK postprocessing.
"""
import argparse
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import onnxruntime as ort
from PIL import Image
from scipy import ndimage

BASE = Path(__file__).resolve().parent
WORK = BASE / '.work'
WEIGHTS = WORK / 'weights'


def session(path, options):
    # Model bytes, not a filename: no loading relative external data files.
    return ort.InferenceSession(path.read_bytes(), sess_options=options, providers=['CPUExecutionProvider'])


def detect(det, image):
    width, height = image.size
    scale = min(1., 1280 / max(width, height))
    w, h = [max(32, int(round(x * scale / 32)) * 32) for x in image.size]
    data = np.asarray(image.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)[:, :, ::-1] / 255
    data = (data - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
    prediction = det.run(None, {det.get_inputs()[0].name: np.ascontiguousarray(data.transpose(2, 0, 1)[None])})[0][0, 0]
    bands, count = ndimage.label(prediction > .30, np.ones((3, 3)))
    height_m, width_m = prediction.shape
    rects = []
    for label, spans in enumerate(ndimage.find_objects(bands, max_label=count), start=1):
        if not spans:
            continue
        ys, xs = spans
        bw, bh = xs.stop - xs.start, ys.stop - ys.start
        if min(bw, bh) < 4:
            continue
        region = bands[spans] == label
        if region.sum() < 16 or np.mean(prediction[spans][region]) < .5:
            continue
        pad = min(16, max(2, int(1.35 * bw * bh / (2 * (bw + bh)))))
        bbox = (max(0, int((xs.start - pad) * width / width_m)), max(0, int((ys.start - pad) * height / height_m)),
                min(width, math.ceil((xs.stop + pad) * width / width_m)), min(height, math.ceil((ys.stop + pad) * height / height_m)))
        rects.append(bbox)
    return rects


def recognize(rec, image, boxes, labels):
    if not boxes:
        return []
    target_h = rec.get_inputs()[0].shape[2]
    widths = [max(8, min(1600, math.ceil(target_h * (r - l) / (b - t)))) for l, t, r, b in boxes]
    results = [''] * len(boxes)
    by_width = sorted(range(len(boxes)), key=widths.__getitem__)
    for start in range(0, len(by_width), 5):
        batch = by_width[start:start + 5]
        max_w = max(32, math.ceil(max(widths[i] for i in batch) / 8) * 8)
        array = np.zeros((len(batch), 3, target_h, max_w), dtype=np.float32)
        for j, i in enumerate(batch):
            crop = image.crop(boxes[i]).resize((widths[i], target_h), Image.Resampling.BILINEAR)
            values = np.asarray(crop, dtype=np.float32)[:, :, ::-1] / 127.5 - 1
            array[j, :, :, :widths[i]] = values.transpose(2, 0, 1)
        scores = rec.run(None, {rec.get_inputs()[0].name: array})[0]
        if scores.shape[-1] != len(labels):
            raise ValueError(f'Character dictionary does not match CTC output: {scores.shape[-1]} vs {len(labels)}')
        for j, tokens in enumerate(scores.argmax(axis=-1)):
            ids = [int(t) for i, t in enumerate(tokens) if t != 0 and (i == 0 or t != tokens[i-1])]
            results[batch[j]] = ''.join(labels[i] for i in ids)
    return results


def line_sort(boxes):
    """Top-to-bottom; join boxes with close vertical centers left-to-right."""
    def y(i):
        return (boxes[i][1] + boxes[i][3]) / 2
    groups = []
    for i in sorted(range(len(boxes)), key=y):
        top, bottom = boxes[i][1], boxes[i][3]
        if groups and abs(y(i) - y(groups[-1][0])) <= min(bottom-top, boxes[groups[-1][0]][3]-boxes[groups[-1][0]][1]) * .42:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [i for group in groups for i in sorted(group, key=lambda j: boxes[j][0])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    inventory = json.loads((WORK / 'inventory.json').read_text(encoding='utf-8'))
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    start = perf_counter()
    det = session(WEIGHTS / 'paddle_det/det.onnx', options)
    rec = session(WEIGHTS / 'paddle_rec/rec.onnx', options)
    labels = [''] + (WEIGHTS / 'paddle_dict/dict.txt').read_text(encoding='utf-8').splitlines() + [' ']
    result = {'engine': 'PP-OCRv5 server det/rec, lightweight DB/CTC', 'ort_version': ort.__version__,
              'threads': args.threads, 'model_load_s': perf_counter() - start, 'rounds': []}
    for n in range(1, args.rounds + 1):
        wall = perf_counter()
        rows = []
        for item in inventory:
            key = item['id']
            start = perf_counter()
            with Image.open(WORK / 'pages' / (key + '.png')) as png:
                img = png.convert('RGB')
            decode = perf_counter() - start
            start = perf_counter()
            boxes = detect(det, img)
            after_det = perf_counter()
            texts = recognize(rec, img, boxes, labels)
            seq = line_sort(boxes)
            lines = [{'text': texts[i], 'bbox': boxes[i]} for i in seq if texts[i].strip()]
            infer = perf_counter() - start
            rows.append({'id': key, 'decode_s': decode, 'infer_s': infer, 'det_s': after_det - start,
                         'seconds': decode + infer + item['render_s'] + item['encode_s'],
                         'text': '\n'.join(r['text'] for r in lines), 'lines': lines})
            print('round', n, key, 'regions', len(boxes), 'infer_s', round(infer, 3), flush=True)
        result['rounds'].append({'pages': rows, 'wall_s': perf_counter() - wall})
    (WORK / 'paddle_measurements.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
