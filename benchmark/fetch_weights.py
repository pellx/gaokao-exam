"""Fetch only revision-pinned public model data; no installation or remote code."""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent / '.work' / 'weights'
PADDLE = 'monkt/paddleocr-onnx/resolve/7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65/'
QWEN = 'onnx-community/Qwen2.5-0.5B-Instruct/resolve/22942cb7d7ba4cc81bb4673549ca4d4614469b5e/'
FILES = {
    'paddle_det': (PADDLE + 'detection/v5/det.onnx', 88030804, '61824840edf6e74581898930b8091b1b2318f4b2705a2e8a40ad3de7ac480133'),
    'paddle_rec': (PADDLE + 'languages/chinese/rec.onnx', 84468836, '26fa4f47060f58e25962b9af6beaee05c8182b90e026c4ecc6db165d9dfdc38a'),
    'paddle_dict': (PADDLE + 'languages/chinese/dict.txt', 74012, '567898b49de2707853454682f05e0c504c0085b9'),
    'qwen_model': (QWEN + 'onnx/model_quantized.onnx', 512096557, '41834041ab1b29eff9fc592f1a29a1844133aea35832ea9fa91682be13016100'),
    'qwen_tokenizer': (QWEN + 'tokenizer.json', 7031673, 'd24314ef7f0afd1b678c2e24c767e19f24f86b0e'),
    'layout_model': ('wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/ee7c3d744e5c47c58e267044ac825f95abe69653/doclayout_yolo_docstructbench_imgsz1024.onnx', 75324598, 'fece9af02f618b603ff7921ccec6861d13e7e1f9830e091dfb7e8ad9311e5b21'),
}


def get(key):
    remote, size, expected = FILES[key]
    target = BASE / key / remote.rsplit('/', 1)[-1]
    target.parent.mkdir(parents=True, exist_ok=True)
    algo = hashlib.sha256 if len(expected) == 64 else hashlib.sha1

    def validate(candidate):
        if candidate.stat().st_size != size:
            return False
        with candidate.open('rb') as stream:
            digest = algo()
            if len(expected) == 40:
                digest.update(f'blob {size}\0'.encode())
            while data := stream.read(1024 * 1024):
                digest.update(data)
        return digest.hexdigest() == expected

    if target.is_file():
        if not validate(target):
            raise ValueError(f'Existing artifact failed checksum: {key}')
        return f'{key}: already verified'
    draft = target.with_suffix('.partial')
    received = 0
    with urlopen(Request('https://huggingface.co/' + remote, headers={'User-Agent': 'benchmark-client'}), timeout=30) as response, draft.open('wb') as out:
        while data := response.read(1024 * 1024):
            received += len(data)
            if received > size:
                raise ValueError(f'Unexpected size for {key}')
            out.write(data)
    if not validate(draft):
        raise ValueError(f'Downloaded artifact failed checksum: {key}')
    draft.replace(target)
    return f'{key}: verified {size} bytes'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('models', nargs='+', choices=['paddle', 'qwen', 'layout'])
    args = parser.parse_args()
    selected = [k for k in FILES if k.split('_', 1)[0] in args.models]
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {k: pool.submit(get, k) for k in selected}
        failed = False
        for k, f in futures.items():
            try:
                print(f.result(), flush=True)
            except Exception as error:
                failed = True
                print(k, type(error).__name__, error, flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
