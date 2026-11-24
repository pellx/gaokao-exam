"""Local Qwen2.5-0.5B INT8 text extraction: full text vs block references."""
import argparse
import json
import re
from pathlib import Path
from time import perf_counter

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

BASE = Path(__file__).resolve().parent
WORK = BASE / '.work'
WEIGHTS = WORK / 'weights'
STOP = {151643, 151645}
SYSTEM = '忠实提取试卷，不解题、不猜答案，不编造。只输出一个 JSON 对象，不要解释。'
INPUT = '只提取第{q}题。以下是有编号的原文块。遇到别的题号就停止收集选项；答案必须是本题的单独答案记录。'
RULES = {
    'verbatim': '键固定为 q,s,o,a。q是题号整数；s是题干块的完整原文（含题号），o是选项原文数组（每项含标签），a是答案记录完整原文。没有选项用[]，没有答案用空字符串。不要改写或删掉原文。',
    'references': '键固定为 q,s,o,a。q是题号整数；s是题干块的ID，o是选项块ID数组，a是本题答案块ID。没有选项用[]，没有答案用空字符串。只填ID，不要复述原文。',
}


class LocalQwen:
    def __init__(self, threads):
        start = perf_counter()
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        self.tokenizer = Tokenizer.from_file(str(WEIGHTS / 'qwen_tokenizer/tokenizer.json'))
        self.session = ort.InferenceSession((WEIGHTS / 'qwen_model/model_quantized.onnx').read_bytes(),
                                            sess_options=options, providers=['CPUExecutionProvider'])
        self.cache_names = [x.name for x in self.session.get_inputs() if x.name.startswith('past_key_values.')]
        self.load_s = perf_counter() - start

    def answer(self, prompt, max_tokens):
        start = perf_counter()
        chat = '<|im_start|>system\n' + SYSTEM + '<|im_end|>\n<|im_start|>user\n' + prompt + '<|im_end|>\n<|im_start|>assistant\n'
        tokens = self.tokenizer.encode(chat, add_special_tokens=False).ids
        length = len(tokens)
        ids = np.array([tokens], dtype=np.int64)
        positions = np.arange(length, dtype=np.int64)[None]
        cache = {name: np.empty((1, 2, 0, 64), dtype=np.float32) for name in self.cache_names}
        output = []
        end = 'length'
        first_token_time = None
        for _ in range(max_tokens):
            feeds = {'input_ids': ids, 'position_ids': positions,
                     'attention_mask': np.ones((1, length), dtype=np.int64), **cache}
            logits, *state = self.session.run(None, feeds)
            token = int(np.argmax(logits[0, -1]))
            if first_token_time is None:
                first_token_time = perf_counter() - start
            output.append(token)
            if token in STOP:
                end = 'stop'
                break
            ids, positions = np.array([[token]], dtype=np.int64), np.array([[length]], dtype=np.int64)
            cache = dict(zip(self.cache_names, state))
            length += 1
        raw = self.tokenizer.decode(output, skip_special_tokens=True)
        return {'raw': raw, 'prompt_tokens': len(tokens), 'generated_tokens': len(output), 'seconds': perf_counter() - start,
                'first_token_s': first_token_time, 'stop_reason': end}


def deref(obj, blocks):
    result = dict(obj)
    result['s'] = blocks[obj['s']]
    result['o'] = [blocks[key] for key in obj['o']]
    result['a'] = blocks[obj['a']] if obj['a'] else ''
    return result


def evaluate(raw, mode, case):
    text = raw.strip()
    fence = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.S)
    candidate = fence[1] if fence else text
    truth = deref(case['gold'], case['blocks'])
    obj = None
    try:
        obj = json.loads(candidate)
        if (not isinstance(obj, dict) or set(obj) != {'q', 's', 'o', 'a'} or type(obj['q']) is not int
                or not isinstance(obj['s'], str) or not isinstance(obj['a'], str) or not isinstance(obj['o'], list)
                or not all(isinstance(x, str) for x in obj['o'])):
            raise ValueError('shape mismatch')
        pred = deref(obj, case['blocks']) if mode == 'references' else obj
    except (TypeError, ValueError, KeyError) as exc:
        return {'valid': False, 'error': str(exc), 'fields_exact': dict.fromkeys(truth, False),
                'unbacked_answer': bool(isinstance(obj, dict) and not truth['a'] and obj.get('a'))}
    return {'valid': True, 'fields_exact': {key: pred[key] == truth[key] for key in truth},
            'unbacked_answer': bool(not truth['a'] and pred['a']), 'pred': pred}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--max-tokens', type=int, default=224)
    parser.add_argument('--example', action='store_true', help='Exploratory prompt with an unrelated synthetic example')
    args = parser.parse_args()
    model = LocalQwen(args.threads)
    smoke = model.answer('请只回答数字，不要解释。2+2等于几？', 16)
    cases = json.loads((BASE / 'llm_cases.json').read_text(encoding='utf-8'))['cases']
    result = {'engine': 'Qwen2.5-0.5B-Instruct INT8 ONNX CPU', 'threads': args.threads, 'ort_version': ort.__version__,
              'model_load_s': model.load_s, 'max_tokens': args.max_tokens, 'example': args.example,
              'smoke': smoke, 'requests': []}
    outname = 'llm_example_measurements.json' if args.example else 'llm_measurements.json'
    for index, case in enumerate(cases):
        order = list(RULES)
        if index % 2:
            order.reverse()
        blocks = '\n'.join(f'{key}: {value}' for key, value in case['blocks'].items())
        for mode in order:
            example = ''
            if args.example:
                demo = {'q': 7, 's': 'x1', 'o': ['x2', 'x3'], 'a': 'x4'} if mode == 'references' else {
                    'q': 7, 's': '7. 示例题', 'o': ['A. 甲', 'B. 乙'], 'a': '7. 答案 B'}
                example = ('\n示例（仅说明格式）：第7题；x1: 7. 示例题；x2: A. 甲；x3: B. 乙；x4: 7. 答案 B。\n'
                           + '示例输出：' + json.dumps(demo, ensure_ascii=False, separators=(',', ':'))
                           + '\n实际输入如下：')
            prompt = f"{INPUT.format(q=case['q'])}\n{RULES[mode]}{example}\n\n{blocks}"
            if args.example:
                prompt += f"\n\n现在只返回第{case['q']}题的 JSON 对象（以 {{ 开头）。"
            out = model.answer(prompt, args.max_tokens)
            assessment = evaluate(out['raw'], mode, case)
            result['requests'].append({'case': case['id'], 'mode': mode, **out, **assessment})
            print(case['id'], mode, 'time', round(out['seconds'], 3), 'tokens', out['prompt_tokens'], out['generated_tokens'],
                  'valid', assessment['valid'], 'exact', sum(assessment['fields_exact'].values()), flush=True)
        (WORK / outname).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
