"""Fetch checked safetensors and config data only; never load repository Python."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from urllib.request import Request, urlopen

WORK = Path(__file__).resolve().parent / '.work' / 'vlm'
SOURCES = {
    'xiaomi': ('xiaomi-research/Xiaomi-OCR-0', 'bbd8cf8d21f256b3644b44cb860f3a7cecb93c30', {
        'chat_template.jinja': (7755, '0ef09f214eaa6d9bca297988afc1454b5827b2c7'),
        'config.json': (2747, '87cadcf0711549f1e36a8fdf405647f787800f3f'),
        'model.safetensors': (1746937856, 'd4551e9f6127ac9e35cff7b4f7a72a90410e03e6c8676a36f0e2b7eaeb5d5348'),
        'processor_config.json': (1191, '33818c7f9e991ad735fd240209f4fa73e6c28c50'),
        'tokenizer.json': (19989325, '06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523'),
        'tokenizer_config.json': (1165, '1d134cd298be1e3be25db393d93a1cefe80e3214'),
    }),
    'paddlevl': ('PaddlePaddle/PaddleOCR-VL-1.6', '132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd', {
        'added_tokens.json': (25381, '6a2790e1462ecb007f9b92dfb00f594701462889'),
        'chat_template.jinja': (1474, 'd8b8c271d7a7245d4937098f53aa372cff3dba70'),
        'config.json': (2059, 'c54711466ae75457f8e57b909a49dae570bfa5c5'),
        'generation_config.json': (133, 'ba87d71b9881c1d52f0be4286fb63e381ce3febf'),
        'model.safetensors': (1917255968, '85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db'),
        'preprocessor_config.json': (641, 'd873cd63d04d9e6243999759fc30f7752dab3222'),
        'processor_config.json': (137, '033053ac4d8b5de2e47884ce85a6b4939cc58e87'),
        'special_tokens_map.json': (1151, 'dcd70aaa8c9987899d7593995546d9c0cfc6a1f3'),
        'tokenizer.json': (11189060, 'c8a215a59183d0d0781adc33bacd3ce6162716f7fd568fb30234a74d69803a7d'),
        'tokenizer.model': (1614363, '34ef7db83df785924fb83d7b887b6e822a031c56e15cff40aaf9b982988180df'),
        'tokenizer_config.json': (186947, 'cd9eb7da01a3209f4fb9b3561e04be022cd87764'),
    }),
}


def verify(p, bytes_expected, digest_expected):
    if p.stat().st_size != bytes_expected:
        raise ValueError(f'Incorrect size: {p.name}')
    with p.open('rb') as stream:
        h = hashlib.sha256() if len(digest_expected) == 64 else hashlib.sha1(f'blob {bytes_expected}\0'.encode())
        while data := stream.read(4 * 1024 * 1024):
            h.update(data)
    if h.hexdigest() != digest_expected:
        raise ValueError(f'Incorrect checksum: {p.name}')


def get(job):
    key, name = job
    repo, revision, checks = SOURCES[key]
    size, digest = checks[name]
    destination = WORK / key / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        verify(destination, size, digest)
        return f'{key}/{name}: cached and verified'
    partial = destination.with_suffix(destination.suffix + '.part')
    url = f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
    received = partial.stat().st_size if partial.is_file() else 0
    headers = {'User-Agent': 'benchmark-client'}
    resume = 0 < received < size
    if resume:
        headers['Range'] = f'bytes={received}-'
    with urlopen(Request(url, headers=headers), timeout=45) as client:
        if client.status == 206:
            bounds = client.headers.get('Content-Range', '')
            if not resume or bounds != f'bytes {received}-{size - 1}/{size}':
                raise ValueError(f'Unexpected download range for {name}: {bounds}')
            mode = 'ab'
        else:
            received = 0
            mode = 'wb'
        with partial.open(mode) as output:
            while data := client.read(4 * 1024 * 1024):
                received += len(data)
                if received > size:
                    raise ValueError(f'Unexpected size for {name}')
                output.write(data)
    verify(partial, size, digest)
    partial.replace(destination)
    return f'{key}/{name}: {size} bytes verified'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('models', nargs='+', choices=list(SOURCES))
    args = parser.parse_args()
    keys = [(key, name) for key in args.models for name in SOURCES[key][2]]
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending = [(key, pool.submit(get, key)) for key in keys]
        okay = True
        for key, f in pending:
            try:
                print(f.result(), flush=True)
            except Exception as e:
                okay = False
                print(key, type(e).__name__, str(e), flush=True)
    return int(not okay)


if __name__ == '__main__':
    raise SystemExit(main())
