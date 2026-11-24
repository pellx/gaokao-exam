"""Recover the PDF bbox of a cropped asset by normalized cross-correlation.

Only needed when a repair worker overwrote an image without submitting its
metadata. The recovered bbox is a candidate: the operator still has to look at
the crop and the page before merging.
"""
import argparse
import json
import sys
from pathlib import Path

import fitz
import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter
from scipy.signal import fftconvolve

ROOT = Path(__file__).resolve().parents[1]
ZOOM = 300 / 72


def match(page_gray, template_gray):
    t = template_gray - template_gray.mean()
    t_energy = np.sqrt((t * t).sum())
    area = t.size
    numerator = fftconvolve(page_gray, t[::-1, ::-1], mode='valid')
    ones = np.ones(t.shape)
    local_sum = fftconvolve(page_gray, ones, mode='valid')
    local_sq = fftconvolve(page_gray * page_gray, ones, mode='valid')
    local_mean = local_sum / area
    local_var = np.maximum(local_sq / area - local_mean * local_mean, 1e-9)
    denominator = np.sqrt(local_var * area) * t_energy
    score = numerator / np.maximum(denominator, 1e-9)
    y, x = np.unravel_index(np.argmax(score), score.shape)
    return float(score[y, x]), int(x), int(y)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('paper')
    parser.add_argument('pdf')
    parser.add_argument('asset_ids', nargs='+')
    args = parser.parse_args()
    paper_path = ROOT / args.paper
    data = json.loads(paper_path.read_text(encoding='utf-8'))
    doc = fitz.open(ROOT / args.pdf)
    for asset_id in args.asset_ids:
        asset = data['assets'][asset_id]
        image = Image.open(paper_path.parent / asset['path']).convert('L')
        template = np.asarray(image, dtype=np.float64)
        page = doc[asset['page'] - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csGRAY)
        page_gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).astype(np.float64)
        score, x, y = match(page_gray, template)
        bbox = [round(x / ZOOM, 2), round(y / ZOOM, 2),
                round((x + template.shape[1]) / ZOOM, 2), round((y + template.shape[0]) / ZOOM, 2)]
        print(json.dumps({'id': asset_id, 'score': round(score, 5), 'bbox': bbox,
                          'old_bbox': asset['bbox'], 'px': [int(template.shape[1]), int(template.shape[0])]},
                         ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
