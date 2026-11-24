"""Install revision-pinned OCR model data, with checksum verification.

No repository Python or remote inference code is downloaded or executed.
"""
import argparse
import hashlib
import importlib.util
import shutil
from pathlib import Path
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location('production_local_ocr', Path(__file__).with_name('local_ocr.py'))
local_ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_ocr)
ARTIFACTS, verify_artifact = local_ocr.ARTIFACTS, local_ocr.verify_artifact

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'pipeline/models/question_ocr'
URLS = {
    'paddle_det/det.onnx': 'monkt/paddleocr-onnx/resolve/7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65/detection/v5/det.onnx',
    'paddle_rec/rec.onnx': 'monkt/paddleocr-onnx/resolve/7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65/languages/chinese/rec.onnx',
    'paddle_dict/dict.txt': 'monkt/paddleocr-onnx/resolve/7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65/languages/chinese/dict.txt',
    'layout_model/doclayout_yolo_docstructbench_imgsz1024.onnx': 'wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/ee7c3d744e5c47c58e267044ac825f95abe69653/doclayout_yolo_docstructbench_imgsz1024.onnx',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--copy-from', type=Path, help='reuse an already verified local model directory')
    args = parser.parse_args()
    for name, (size, _) in ARTIFACTS.items():
        destination = TARGET / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            verify_artifact(TARGET, name)
            print(f'{name}: already verified', flush=True)
            continue
        partial = destination.with_suffix(destination.suffix + '.partial')
        if args.copy_from:
            source = verify_artifact(args.copy_from, name)
            shutil.copyfile(source, partial)
        else:
            request = Request('https://huggingface.co/' + URLS[name], headers={'User-Agent': 'exam-ocr-bootstrap'})
            with urlopen(request, timeout=45) as response, partial.open('wb') as output:
                received = 0
                while data := response.read(4 * 1024 * 1024):
                    received += len(data)
                    if received > size:
                        raise ValueError(f'Unexpected size for {name}')
                    output.write(data)
        size, expected = ARTIFACTS[name]
        digest = hashlib.sha256() if len(expected) == 64 else hashlib.sha1(f'blob {size}\0'.encode())
        with partial.open('rb') as stream:
            while data := stream.read(4 * 1024 * 1024):
                digest.update(data)
        if partial.stat().st_size != size or digest.hexdigest() != expected:
            raise ValueError(f'Checksum mismatch: {name}')
        partial.replace(destination)
        print(f'{name}: verified {size} bytes', flush=True)


if __name__ == '__main__':
    main()
