"""Measure DocLayout-YOLO regions for the PaddleOCR-VL arm."""
import json
from time import perf_counter


def main():
    import onnxruntime as ort
    from PIL import Image
    from run_layout import MODEL, WORK, detect, load

    threads = 4
    threshold = .25
    # DocStructBench classes. Omit figures and regions labeled abandon.
    tasks = {0: 'OCR:', 1: 'OCR:', 4: 'OCR:', 5: 'Table Recognition:',
             6: 'OCR:', 7: 'OCR:', 8: 'Formula Recognition:', 9: 'OCR:'}
    opts = ort.SessionOptions()
    opts.intra_op_num_threads, opts.inter_op_num_threads = threads, 1
    started = perf_counter()
    session = ort.InferenceSession(MODEL.read_bytes(), sess_options=opts, providers=['CPUExecutionProvider'])
    result = {'engine': 'DocLayout-YOLO DocStructBench ONNX CPU', 'threads': threads,
              'threshold': threshold, 'model_load_s': perf_counter() - started, 'pages': []}
    for page in load(WORK / 'inventory.json'):
        key = page['id']
        with Image.open(WORK / 'pages' / f'{key}.png') as src:
            image = src.convert('RGB')
        decoded = perf_counter()
        regions = [dict(task=tasks[cls], cls=cls, box=box, confidence=conf)
                   for cls, box, conf in detect(session, image, threshold, tasks)]
        regions.sort(key=lambda region: (region['box'][1], region['box'][0]))
        if not regions:
            regions = [dict(task='OCR:', cls=None, box=[0, 0, image.width, image.height], confidence=None)]
        seconds = perf_counter() - decoded
        result['pages'].append({'id': key, 'regions': regions, 'detect_s': seconds})
        print(key, len(regions), f'{seconds:.3f}s', flush=True)
    (WORK / 'vlm_regions.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    # Isolated execution keeps both the project and model data off the default
    # import path. Explicitly add only this benchmark's source directory.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
