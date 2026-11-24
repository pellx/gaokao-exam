"""Pinned, offline PP-OCRv5 and DocLayout-YOLO CPU inference.

The OCR adapter is the same lightweight DB/CTC implementation measured in
benchmark/. It is not PaddleOCR-VL or the complete PaddleOCR SDK.
"""
import hashlib
import math
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image
from scipy import ndimage

ARTIFACTS = {
    'paddle_det/det.onnx': (88030804, '61824840edf6e74581898930b8091b1b2318f4b2705a2e8a40ad3de7ac480133'),
    'paddle_rec/rec.onnx': (84468836, '26fa4f47060f58e25962b9af6beaee05c8182b90e026c4ecc6db165d9dfdc38a'),
    'paddle_dict/dict.txt': (74012, '567898b49de2707853454682f05e0c504c0085b9'),
    'layout_model/doclayout_yolo_docstructbench_imgsz1024.onnx': (
        75324598, 'fece9af02f618b603ff7921ccec6861d13e7e1f9830e091dfb7e8ad9311e5b21'),
}


def verify_artifact(root, name):
    path = root / name
    size, expected = ARTIFACTS[name]
    if not path.is_file() or path.stat().st_size != size:
        raise RuntimeError(f'Missing or incorrect OCR model: {path}')
    digest = hashlib.sha256() if len(expected) == 64 else hashlib.sha1(f'blob {size}\0'.encode())
    with path.open('rb') as stream:
        while data := stream.read(4 * 1024 * 1024):
            digest.update(data)
    if digest.hexdigest() != expected:
        raise RuntimeError(f'OCR model checksum mismatch: {path}')
    return path


def session(path, options):
    return ort.InferenceSession(path.read_bytes(), sess_options=options, providers=['CPUExecutionProvider'])


class OCREngine:
    def __init__(self, weights, threads=4):
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        self.det = session(verify_artifact(weights, 'paddle_det/det.onnx'), options)
        self.rec = session(verify_artifact(weights, 'paddle_rec/rec.onnx'), options)
        dictionary = verify_artifact(weights, 'paddle_dict/dict.txt')
        self.labels = [''] + dictionary.read_text(encoding='utf-8').splitlines() + [' ']

    def detect(self, image):
        width, height = image.size
        scale = min(1., 1280 / max(width, height))
        w, h = [max(32, int(round(x * scale / 32)) * 32) for x in image.size]
        data = np.asarray(image.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)[:, :, ::-1] / 255
        data = (data - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
        prediction = self.det.run(None, {self.det.get_inputs()[0].name: np.ascontiguousarray(data.transpose(2, 0, 1)[None])})[0][0, 0]
        bands, count = ndimage.label(prediction > .30, np.ones((3, 3)))
        height_m, width_m = prediction.shape
        rectangles = []
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
            rectangles.append((max(0, int((xs.start - pad) * width / width_m)),
                               max(0, int((ys.start - pad) * height / height_m)),
                               min(width, math.ceil((xs.stop + pad) * width / width_m)),
                               min(height, math.ceil((ys.stop + pad) * height / height_m))))
        return rectangles

    def recognize(self, image, boxes):
        if not boxes:
            return []
        target_h = self.rec.get_inputs()[0].shape[2]
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
            scores = self.rec.run(None, {self.rec.get_inputs()[0].name: array})[0]
            if scores.shape[-1] != len(self.labels):
                raise ValueError('Character dictionary does not match CTC output')
            for j, tokens in enumerate(scores.argmax(axis=-1)):
                ids = [int(t) for i, t in enumerate(tokens) if t != 0 and (i == 0 or t != tokens[i - 1])]
                results[batch[j]] = ''.join(self.labels[i] for i in ids)
        return results

    def read(self, image):
        boxes = self.detect(image)
        texts = self.recognize(image, boxes)
        lines = [{'text': texts[i], 'bbox': list(boxes[i])} for i in line_sort(boxes) if texts[i].strip()]
        return '\n'.join(line['text'] for line in lines), lines


def line_sort(boxes):
    def center(i):
        return (boxes[i][1] + boxes[i][3]) / 2
    groups = []
    for i in sorted(range(len(boxes)), key=center):
        top, bottom = boxes[i][1], boxes[i][3]
        if groups and abs(center(i) - center(groups[-1][0])) <= min(
                bottom - top, boxes[groups[-1][0]][3] - boxes[groups[-1][0]][1]) * .42:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [i for group in groups for i in sorted(group, key=lambda j: boxes[j][0])]


class LayoutEngine:
    def __init__(self, weights, threads=4):
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        path = verify_artifact(weights, 'layout_model/doclayout_yolo_docstructbench_imgsz1024.onnx')
        self.model = session(path, options)

    def detect(self, image, threshold=.25, classes=(3, 5)):
        width, height = image.size
        factor = 1024 / max(width, height)
        w, h = round(width * factor), round(height * factor)
        left, top = (1024 - w) // 2, (1024 - h) // 2
        square = Image.new('RGB', (1024, 1024), (114, 114, 114))
        square.paste(image.resize((w, h), Image.Resampling.BILINEAR), (left, top))
        values = np.asarray(square, dtype=np.float32).transpose(2, 0, 1)[None] / 255
        prediction = self.model.run(None, {self.model.get_inputs()[0].name: np.ascontiguousarray(values)})[0][0]
        if prediction.shape != (300, 6):
            raise ValueError(f'Unexpected layout shape: {prediction.shape}')
        found = []
        for x0, y0, x1, y1, confidence, cls in prediction:
            if confidence < threshold or int(cls) not in classes:
                continue
            box = (max(0, math.floor((x0 - left) * width / w)), max(0, math.floor((y0 - top) * height / h)),
                   min(width, math.ceil((x1 - left) * width / w)), min(height, math.ceil((y1 - top) * height / h)))
            if box[0] < box[2] and box[1] < box[3]:
                found.append({'cls': int(cls), 'box': box, 'confidence': round(float(confidence), 5)})
        return found
