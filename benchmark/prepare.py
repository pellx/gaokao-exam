"""Render local source pages and inventory PDF layers (no network)."""
import hashlib
import json
from pathlib import Path
from time import perf_counter

import pymupdf as fitz
from PIL import Image

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
WORK = BASE / '.work'
DPI = 210


def read_json(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def main():
    for folder in ['pages', 'preview']:
        (WORK / folder).mkdir(parents=True, exist_ok=True)
    checked = set()
    inventory = []
    for sample in read_json(BASE / 'samples.json'):
        paper_path = ROOT / sample['paper']
        paper = read_json(paper_path)
        source = (paper_path.parent / paper['source']['file']).resolve()
        source.relative_to(ROOT)
        if source not in checked:
            with source.open('rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == paper['source']['sha256'], source
            checked.add(source)
        with fitz.open(source) as doc:
            page = doc[sample['page'] - 1]
            item = dict(sample, source=source.relative_to(ROOT).as_posix(), source_sha256=paper['source']['sha256'],
                        page_count=doc.page_count, page_chars=len(page.get_text().strip()),
                        embedded_images=len(page.get_image_info()), vector_paths=len(page.get_drawings()),
                        page_size_pt=list(page.rect)[2:], rotation=page.rotation, page_cropbox=list(page.cropbox), dpi=DPI)
            img_path = WORK / 'pages' / (sample['id'] + '.png')
            start = perf_counter()
            pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False, annots=False)
            raw = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            item['render_s'] = perf_counter() - start
            start = perf_counter()
            raw.save(img_path, compress_level=1)
            item['encode_s'] = perf_counter() - start
            item['page_size_px'] = list(raw.size)
            raw.thumbnail((860, 1190), Image.Resampling.LANCZOS)
            raw.save(WORK / 'preview' / (sample['id'] + '.jpg'), quality=90)
        inventory.append(item)
    (WORK / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for item in inventory:
        print(item['id'], 'characters', item['page_chars'], 'images', item['embedded_images'], 'paths', item['vector_paths'], 'rotation', item['rotation'], 'render', round(item['render_s'], 4))


if __name__ == '__main__':
    main()
