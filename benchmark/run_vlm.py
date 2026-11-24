"""Measure the two local VLM recognition arms on the fixed page images."""
import argparse
import json
import os
import platform
from pathlib import Path
from time import perf_counter

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'

from PIL import Image
import torch
import transformers
from transformers import (AutoModelForImageTextToText, AutoProcessor,
                          PaddleOCRVLForConditionalGeneration, PaddleOCRVLProcessor)

WORK = Path(__file__).resolve().parent / '.work'
XIAOMI_DOCUMENT = (
    'Extract all information from the main body of the document image and represent it '
    'in markdown format, ignoring headers and footers. Tables should be expressed in OTSL '
    'format, formulas in the document should be represented using LATEX format, and the '
    'parsing should be organized according to the reading order.'
)


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, data):
    partial = path.with_suffix('.tmp')
    partial.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    partial.replace(path)


def load_model(name, dtype):
    path = WORK / 'vlm' / name
    opts = {'local_files_only': True}
    if name == 'xiaomi':
        model_class, processor_class = AutoModelForImageTextToText, AutoProcessor
        opts['trust_remote_code'] = False
    else:
        model_class, processor_class = PaddleOCRVLForConditionalGeneration, PaddleOCRVLProcessor
    processor = processor_class.from_pretrained(path, **opts)
    model, loading = model_class.from_pretrained(path, dtype=dtype, use_safetensors=True,
                                                 output_loading_info=True, **opts)
    for key in ('missing_keys', 'mismatched_keys', 'unexpected_keys', 'error_msgs'):
        if loading.get(key):
            raise RuntimeError(f'{name} checkpoint {key}: {loading[key]}')
    return model.eval(), processor


@torch.inference_mode()
def recognize(model, processor, image, prompt, cap, image_size):
    messages = [{'role': 'user', 'content': [{'type': 'image', 'image': image}, {'type': 'text', 'text': prompt}]}]
    image_kwargs = {'images_kwargs': {'size': image_size}} if image_size else {}
    started = perf_counter()
    inputs = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
                                           return_dict=True, return_tensors='pt', **image_kwargs)
    prompt_tokens = inputs['input_ids'].shape[-1]
    prepared = perf_counter()
    output = model.generate(**inputs, max_new_tokens=cap, do_sample=False, use_cache=True)
    generated = perf_counter()
    tokens = output[0, prompt_tokens:]
    text = processor.decode(tokens, skip_special_tokens=True)
    eos = model.generation_config.eos_token_id
    eos_ids = eos if isinstance(eos, (list, tuple)) else [eos]
    truncated = len(tokens) == cap and int(tokens[-1]) not in eos_ids
    return {'text': text, 'prompt_tokens': prompt_tokens, 'generated_tokens': len(tokens),
            'hit_limit': truncated, 'processor_s': prepared - started,
            'generate_s': generated - prepared, 'decode_s': perf_counter() - generated}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model', choices=['xiaomi', 'paddlevl'])
    parser.add_argument('--pages', nargs='+')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--dtype', choices=['bfloat16', 'float32'], default='bfloat16')
    parser.add_argument('--max-new-tokens', type=int)
    parser.add_argument('--max-image-pixels', type=int, help='override the processor’s per-image pixel cap')
    args = parser.parse_args()
    cap = args.max_new_tokens if args.max_new_tokens is not None else (4096 if args.model == 'xiaomi' else 512)
    if args.threads < 1 or cap < 1 or (args.max_image_pixels is not None and args.max_image_pixels < 112896):
        parser.error('threads and max-new-tokens must be positive; max-image-pixels must be at least 112896')
    inventory = load_json(WORK / 'inventory.json')
    ids = {p['id'] for p in inventory}
    if args.pages and set(args.pages) - ids:
        parser.error(f'unknown pages: {sorted(set(args.pages) - ids)}')
    layout = load_json(WORK / 'vlm_regions.json') if args.model == 'paddlevl' else None
    layouts = {p['id']: p for p in layout['pages']} if layout else {}
    image_size = {'shortest_edge': 65536 if args.model == 'xiaomi' else 112896,
                  'longest_edge': args.max_image_pixels} if args.max_image_pixels else None
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    started = perf_counter()
    model, processor = load_model(args.model, getattr(torch, args.dtype))
    elapsed_load = perf_counter() - started
    print(f'{args.model}: loaded in {elapsed_load:.3f}s', flush=True)
    batch_started = perf_counter()
    rows = []
    data = {'engine': type(model).__name__,
            'source': {'xiaomi': 'xiaomi-research/Xiaomi-OCR-0@bbd8cf8d21f256b3644b44cb860f3a7cecb93c30',
                       'paddlevl': 'PaddlePaddle/PaddleOCR-VL-1.6@132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd'}[args.model],
            'trust_remote_code': False, 'offline_inference': True,
            'torch': torch.__version__, 'transformers': transformers.__version__,
            'python': platform.python_version(), 'threads': args.threads, 'dtype': args.dtype,
            'device': 'cpu', 'cap_per_call': cap, 'max_image_pixels': args.max_image_pixels,
            'model_and_processor_load_s': elapsed_load,
            'layout': {k: v for k, v in layout.items() if k != 'pages'} if layout else None,
            'rounds': [{'pages': rows}]}
    completed_path = WORK / f'{args.model}_measurements.json'
    progress_path = completed_path.with_suffix('.in_progress')
    for page in inventory:
        key = page['id']
        if args.pages and key not in args.pages:
            continue
        started = perf_counter()
        with Image.open(WORK / 'pages' / f'{key}.png') as src:
            image = src.convert('RGB')
        decoded = perf_counter()
        plan = layouts.get(key)
        if plan:
            requests = [{**region, **recognize(model, processor, image.crop(region['box']), region['task'], cap, image_size)}
                        for region in plan['regions']]
        else:
            requests = [dict(task='document', **recognize(model, processor, image, XIAOMI_DOCUMENT, cap, image_size))]
        wall = perf_counter() - started
        prep = page['render_s'] + page['encode_s']
        detect = plan['detect_s'] if plan else 0
        row = {'id': key, 'text': '\n'.join(r['text'] for r in requests), 'seconds': prep + detect + wall,
               'prep_s': prep, 'detect_s': detect, 'image_decode_s': decoded - started,
               'recognition_wall_s': wall, 'requests': requests}
        rows.append(row)
        print(f'{key}: {row["seconds"]:.2f}s, {len(requests)} calls, '
              f'{sum(r["generated_tokens"] for r in requests)} generated tokens, '
              f'{sum(r["hit_limit"] for r in requests)} limits', flush=True)
        if args.pages:
            print(row['text'][:400].encode('unicode_escape').decode('ascii'), flush=True)
        data['rounds'][0]['wall_s'] = perf_counter() - batch_started
        if not args.pages:
            save_json(progress_path, data)
    if not args.pages:
        progress_path.replace(completed_path)


if __name__ == '__main__':
    main()
