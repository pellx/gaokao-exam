"""Score labeled evidence against recorded outputs; no inference or network."""
import argparse
import json
import math
import re
import statistics as stats
import unicodedata
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def norm(text, compat=False):
    s = unicodedata.normalize('NFKC' if compat else 'NFC', text or '')
    if compat:
        s = s.replace('−', '-').replace('–', '-').replace('∆', 'Δ')
    return ''.join(c for c in s if not c.isspace())


def best_span(ref, text):
    """Levenshtein distance from ref to the best contiguous span in text."""
    i = text.find(ref)
    if i >= 0:
        return 0, text[i:i + len(ref)]
    previous = [0] * (len(text) + 1)  # skip an arbitrary prefix at zero cost
    starts = list(range(len(text) + 1))
    for i, a in enumerate(ref, 1):
        row = [i]
        marks = [0]
        for j, b in enumerate(text, 1):
            sub = previous[j - 1] + (a != b)
            delete, insert = previous[j] + 1, row[j - 1] + 1
            if sub <= delete and sub <= insert:
                row.append(sub)
                marks.append(starts[j - 1])
            elif delete <= insert:
                row.append(delete)
                marks.append(starts[j])
            else:
                row.append(insert)
                marks.append(marks[j - 1])
        previous, starts = row, marks
    j = min(range(len(previous)), key=previous.__getitem__)
    return previous[j], text[starts[j]:j]


def iou(a, b):
    def area(c):
        return max(0, c[2] - c[0]) * max(0, c[3] - c[1])
    overlap = area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
    union = area(a) + area(b) - overlap
    return overlap / union if union else 0


def matches(gold, pred, threshold=.5):
    pairs = sorted(((iou(g['bbox'], p['bbox']), gi, pi) for gi, g in enumerate(gold) for pi, p in enumerate(pred)), reverse=True)
    g_used, p_used = set(), set()
    for overlap, gi, pi in pairs:
        if overlap < threshold:
            break
        if gi not in g_used and pi not in p_used:
            g_used.add(gi)
            p_used.add(pi)
            yield gi, pi, overlap


def add_scores(agg, tag, refs):
    group = agg[tag]
    group['snippets'] += len(refs)
    group['exact'] += sum(r['edits'] == 0 for r in refs)
    group['chars'] += sum(r['chars'] for r in refs)
    group['edits'] += sum(r['edits'] for r in refs)


def percentile90(values):
    return sorted(values)[math.ceil(.9 * len(values)) - 1]


def score_text(ref, measurements):
    first = measurements['rounds'][0]
    if not first:
        raise ValueError('No extraction results')
    page_info = {p['id']: p for p in measurements['pages']}
    groups = {k: [] for k in ('scan', 'text_layer')}
    for page_id, p in page_info.items():
        groups['text_layer' if p['page_chars'] else 'scan'].append(page_id)
    methods = {}
    for kind, run in first.items():
        if kind == 'native_layout':
            continue
        if {p['id'] for p in run['pages']} != set(ref) or len(run['pages']) != len(ref):
            raise ValueError(f'{kind}: incomplete or duplicate output pages')
        summary = defaultdict(lambda: {'snippets': 0, 'exact': 0, 'chars': 0, 'edits': 0})
        repeats = [r[kind] for r in measurements['rounds']]
        row_times = {key: [] for key in ref}
        for r in repeats:
            for page in r['pages']:
                row_times[page['id']].append(page['seconds'])
        wall = [r['wall_s'] for r in repeats if 'wall_s' in r]
        full = [sum(p['seconds'] for p in r['pages']) for r in repeats]
        details = {}
        for page in run['pages']:
            page_id = page['id']
            truth = ref[page_id]
            selected = {}
            for tag in ('text', 'formulas', 'answers'):
                items = []
                for target in truth.get(tag, []):
                    compat = tag in ('formulas', 'answers')
                    target_norm = norm(target, compat)
                    distance, aligned = best_span(target_norm, norm(page['text'], compat))
                    items.append({'ref': target, 'match': aligned, 'chars': len(target_norm), 'edits': distance})
                selected[tag] = items
                add_scores(summary, tag, items)
                if tag == 'text':
                    add_scores(summary, 'scan' if page_id in groups['scan'] else 'text_layer', items)
            selected['median_s'] = stats.median(row_times[page_id])
            if 'route' in page:
                selected['route'] = page['route']
            details[page_id] = selected
        flat_times = [t for times in row_times.values() for t in times]
        methods[kind] = {'median_12_page_processing_s': stats.median(full), 'page_median_s': stats.median(flat_times),
                         'page_p90_s': percentile90(flat_times), 'round_processing_s': full,
                         'median_measured_batch_wall_s': stats.median(wall) if wall else None,
                         'round_batch_wall_s': wall, 'modeled_from_measured_stages': run.get('modeled_from_measured_stages', False),
                         'groups': dict(summary), 'pages': details}
    return methods, groups


def score_layout(ref, pages):
    by_page = {}
    for p in pages:
        g = ref[p['id']]
        fg = g.get('figures', [])
        tg = g.get('tables', [])
        f = {'expected': len(fg), 'predicted': len(p['figures']), 'matched': 0, 'correct_q': 0, 'matches': []}
        for gi, pi, overlap in matches(fg, p['figures']):
            pred = p['figures'][pi]
            f['matched'] += 1
            f['correct_q'] += int(fg[gi]['q'] == pred['q'])
            f['matches'].append({'ref_q': fg[gi]['q'], 'pred_q': pred['q'], 'iou': overlap, 'path': pred.get('path')})
        t = {'expected': len(tg), 'predicted': len(p['tables']), 'matched': 0, 'correct_shape': 0,
             'cells': sum(len(r) for q in tg for r in q['cells']), 'correct_cells': 0, 'matches': []}
        for gi, pi, overlap in matches(tg, p['tables']):
            expected, found = tg[gi]['cells'], p['tables'][pi]['cells']
            shape, aligned = compare_cells(expected, found)
            t['matched'] += 1
            t['correct_shape'] += int(shape)
            t['correct_cells'] += aligned
            t['matches'].append({'iou': overlap, 'same_grid': shape, 'correct_cells': aligned, 'predicted_cells': found})
        by_page[p['id']] = {'figures': f, 'tables': t, 'figure_s': p['figure_s'], 'table_s': p['table_s']}
    totals = {}
    for k in ('figures', 'tables'):
        if not by_page:
            continue
        keys = next(iter(by_page.values()))[k].keys() - {'matches'}
        totals[k] = {key: sum(p[k][key] for p in by_page.values()) for key in sorted(keys)}
    return {'totals': totals, 'pages': by_page, 'figure_total_s': sum(p['figure_s'] for p in by_page.values()),
            'table_total_s': sum(p['table_s'] for p in by_page.values())}


def otsl_cells(text):
    """Decode a simple OTSL grid, leaving continuation slots empty."""
    grid, row = [], []
    parts = re.split(r'<(fcel|ecel|lcel|ucel|xcel|nl)>', text.strip())
    for tag, value in zip(parts[1::2], parts[2::2]):
        if tag == 'nl':
            if row:
                grid.append(row)
                row = []
        else:
            row.append(value.strip() if tag == 'fcel' else '')
    if row:
        grid.append(row)
    return grid


def otsl_grids(text):
    """Extract OTSL runs from a whole page without assigning geometric boxes."""
    return [cells for match in re.finditer(r'(?:<fcel>.*?<nl>)+', text or '', re.DOTALL)
            if (cells := otsl_cells(match.group()))]


def compare_cells(expected, found):
    shape = [len(row) for row in expected] == [len(row) for row in found]
    correct = sum(norm(cell, True) == norm(found[i][j], True)
                  for i, row in enumerate(expected) for j, cell in enumerate(row)
                  if i < len(found) and j < len(found[i])) if shape else 0
    return shape, correct


def score_whole_page_tables(ref, pages):
    details = {}
    for page in pages:
        expected = ref[page['id']].get('tables', [])
        if not expected:
            continue
        grids = otsl_grids(page['text'])
        # This sample has one reference table per relevant page. Multiple OTSL
        # candidates are not guessed away: structure is counted as failed.
        shape, correct = (compare_cells(expected[0]['cells'], grids[0])
                          if len(expected) == len(grids) == 1 else (False, 0))
        details[page['id']] = {'candidates': len(grids), 'same_grid': shape,
                               'correct_cells': correct, 'cells': sum(map(len, expected[0]['cells'])),
                               'predicted_cells': grids}
    return {'totals': {'expected': len(details), 'pages_with_one_candidate': sum(x['candidates'] == 1 for x in details.values()),
                       'correct_shape': sum(x['same_grid'] for x in details.values()),
                       'cells': sum(x['cells'] for x in details.values()),
                       'correct_cells': sum(x['correct_cells'] for x in details.values())},
            'pages': details, 'location_scored': False}


def score_vlm(ref, inventory, data, name):
    methods, _ = score_text(ref, {'pages': inventory, 'rounds': [{name: run} for run in data['rounds']]})
    record = methods[name]
    rows = data['rounds'][0]['pages']
    fields = ('prompt_tokens', 'generated_tokens', 'hit_limit', 'processor_s', 'generate_s', 'decode_s')
    inputs = {page['id']: page for page in inventory}
    layouts = []
    for page in rows:
        page_id = page['id']
        calls = page['requests']
        detail = record['pages'][page_id]
        detail.update({key: sum(call[key] for call in calls) for key in fields})
        detail.update({key: page[key] for key in ('prep_s', 'detect_s', 'image_decode_s', 'recognition_wall_s')})
        detail['calls'] = len(calls)
        if name == 'paddlevl':
            source = inputs[page_id]
            dimensions = source['page_size_px'] * 2
            tables = [{'bbox': [v * 1000 / size for v, size in zip(call['box'], dimensions)],
                       'cells': otsl_cells(call['text'])} for call in calls if call['cls'] == 5]
            table_time = sum(call['processor_s'] + call['generate_s'] + call['decode_s']
                             for call in calls if call['cls'] == 5)
            layouts.append({'id': page_id, 'figures': [], 'tables': tables, 'figure_s': 0, 'table_s': table_time})
    record['first_round_totals'] = {key: sum(page[key] for page in record['pages'].values())
                                    for key in (*fields, 'calls', 'detect_s')}
    if layouts:
        tables = score_layout(ref, layouts)
        record['table_recognition'] = {'totals': tables['totals']['tables'], 'recognition_s': tables['table_total_s'],
                                       'pages': {key: value['tables'] for key, value in tables['pages'].items()}}
    elif name == 'xiaomi':
        record['whole_page_table_cells'] = score_whole_page_tables(ref, rows)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--measurements', type=Path, default=BASE / '.work/measurements.json')
    parser.add_argument('--output', type=Path, default=BASE / 'results.json')
    args = parser.parse_args()
    ref = load(BASE / 'reference.json')['pages']
    measurements = load(args.measurements)
    env = {k: v for k, v in measurements.items() if k not in ('pages', 'rounds')}
    paddle_path = args.measurements.with_name('paddle_measurements.json')
    if paddle_path.is_file():
        paddle_data = load(paddle_path)
        if len(paddle_data['rounds']) != len(measurements['rounds']):
            raise ValueError('PaddleOCR and baseline round counts differ')
        env['paddle'] = {k: v for k, v in paddle_data.items() if k != 'rounds'}
        for r, p in zip(measurements['rounds'], paddle_data['rounds']):
            r['paddle'] = p
            # Route on the same input predicate as the WinRT hybrid, using
            # actual timings from each stage and no reference-based routing.
            base = {page['id']: page for page in r['pymupdf']['pages']}
            wins = {page['id']: page for page in r['hybrid']['pages']} if 'hybrid' in r else {}
            if wins:
                pages = []
                for raw in p['pages']:
                    key = raw['id']
                    if wins[key]['route'] == 'pymupdf':
                        row = dict(base[key], route='pymupdf')
                    else:
                        row = dict(raw, route='paddle', seconds=raw['seconds'] + base[key]['seconds'])
                    pages.append(row)
                r['hybrid_paddle'] = {'pages': pages, 'modeled_from_measured_stages': True}
    methods, page_groups = score_text(ref, measurements)
    for name in ('xiaomi', 'paddlevl'):
        path = args.measurements.with_name(f'{name}_measurements.json')
        if path.is_file():
            data = load(path)
            env[name] = {key: value for key, value in data.items() if key != 'rounds'}
            env[name].setdefault('source', {
                'xiaomi': 'xiaomi-research/Xiaomi-OCR-0@bbd8cf8d21f256b3644b44cb860f3a7cecb93c30',
                'paddlevl': 'PaddlePaddle/PaddleOCR-VL-1.6@132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd'}[name])
            env[name].setdefault('trust_remote_code', False)
            env[name].setdefault('offline_inference', True)
            methods[name] = score_vlm(ref, measurements['pages'], data, name)
    first = measurements['rounds'][0]
    layout = score_layout(ref, first.get('native_layout', {}).get('pages', []))
    result = {'environment': env, 'page_groups': page_groups, 'methods': methods, 'native_layout': layout}
    for key in ('llm', 'llm_example'):
        path = args.measurements.with_name(f'{key}_measurements.json')
        if path.is_file():
            data = load(path)
            grouped = {}
            for mode in ('verbatim', 'references'):
                rows = [r for r in data['requests'] if r['mode'] == mode]
                grouped[mode] = {'calls': len(rows), 'valid': sum(r['valid'] for r in rows),
                    'exact_cases': sum(all(r['fields_exact'].values()) for r in rows),
                    'exact_fields': sum(sum(r['fields_exact'].values()) for r in rows),
                    'field_count': sum(len(r['fields_exact']) for r in rows),
                    'prompt_tokens': sum(r['prompt_tokens'] for r in rows),
                    'generated_tokens': sum(r['generated_tokens'] for r in rows),
                    'truncated': sum(r['stop_reason'] == 'length' for r in rows),
                    'unbacked_answers_in_json': sum(r['unbacked_answer'] for r in rows),
                    'total_s': sum(r['seconds'] for r in rows), 'median_s': stats.median(r['seconds'] for r in rows)}
            result[key] = {**data, 'summary': grouped}
    layout_path = args.measurements.with_name('layout_measurements.json')
    if layout_path.is_file():
        data = load(layout_path)
        scored = score_layout(ref, data['pages'])
        scored['environment'] = {k: v for k, v in data.items() if k != 'pages'}
        scored['total_page_processing_s'] = sum(r['processing_s'] for r in data['pages'])
        scored['total_detect_s'] = sum(r['det_s'] for r in data['pages'])
        info = {p['id']: p for p in measurements['pages']}
        native = {p['id']: p for p in first['native_layout']['pages']}
        fused = []
        for page in data['pages']:
            key = page['id']
            row = dict(page)
            if info[key]['page_chars'] and native[key]['figures']:
                row['figures'] = native[key]['figures']
            fused.append(row)
        scored['native_preferred'] = score_layout(ref, fused)['totals']['figures']
        result['detected_layout'] = scored
    rules_path = args.measurements.with_name('rules_measurements.json')
    if rules_path.is_file():
        rows = load(rules_path)
        result['rules'] = {'cases': len(rows), 'exact_cases': sum(all(row['fields_exact'].values()) for row in rows),
                           'median_s': stats.median(row['median_s'] for row in rows), 'model_tokens': 0, 'rows': rows}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('pages', {k: len(v) for k, v in page_groups.items()})
    for k, m in methods.items():
        print(k, {a: (v['exact'], v['snippets'], v['edits'], v['chars']) for a, v in m['groups'].items()},
              'seconds', *(round(m[a], 4) if m[a] is not None else None for a in ('median_12_page_processing_s', 'page_median_s', 'page_p90_s', 'median_measured_batch_wall_s')))
    for name in ('xiaomi', 'paddlevl'):
        if name in methods:
            print(name, 'vlm totals', methods[name]['first_round_totals'])
            if 'table_recognition' in methods[name]:
                print(name, 'table recognition', methods[name]['table_recognition']['totals'])
            if 'whole_page_table_cells' in methods[name]:
                print(name, 'whole-page table cells', methods[name]['whole_page_table_cells']['totals'])
    print('layout', layout['totals'], 'seconds', round(layout['figure_total_s'], 4), round(layout['table_total_s'], 4))


if __name__ == '__main__':
    main()
