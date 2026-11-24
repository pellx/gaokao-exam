"""Deterministic baseline for the already-segmented LLM fixtures only."""
import json
import re
from pathlib import Path
from statistics import median
from time import perf_counter

BASE = Path(__file__).resolve().parent


def extract(q, blocks):
    number = re.compile(rf'^\s*{q}[.．]\s*')
    next_question = re.compile(r'^\s*\d+[.．]')
    answer_record = re.compile(r'^(?:[（(]\d+分[）)]\s*|答案\s*)[A-D]{1,4}$')
    option = re.compile(r'^\s*(?:[A-D][.．]|[（(][A-D][）)])')
    result = {'q': q, 's': '', 'o': [], 'a': ''}
    collecting = False
    for key, text in blocks.items():
        hit = number.match(text)
        if hit and answer_record.match(text[hit.end():]):
            result['a'] = key
            collecting = False
        elif hit and not result['s']:
            result['s'] = key
            collecting = True
        elif next_question.match(text):
            collecting = False
        elif collecting and option.match(text):
            result['o'].append(key)
    return result


def main():
    cases = json.loads((BASE / 'llm_cases.json').read_text(encoding='utf-8'))['cases']
    result = []
    for case in cases:
        durations = []
        for _ in range(101):
            start = perf_counter()
            got = extract(case['q'], case['blocks'])
            durations.append(perf_counter() - start)
        result.append({'case': case['id'], 'median_s': median(durations), 'predicted': got,
                       'fields_exact': {k: got[k] == v for k, v in case['gold'].items()}})
    (BASE / '.work/rules_measurements.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('rules', sum(all(row['fields_exact'].values()) for row in result), '/', len(result), 'median microseconds', round(median(row['median_s'] for row in result) * 1e6, 1))


if __name__ == '__main__':
    main()
