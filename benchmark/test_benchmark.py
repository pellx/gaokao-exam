"""Self-tests for the benchmark metrics and result coverage."""
import importlib.util
import json
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, BASE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score = load_module('score')


class Scoring(unittest.TestCase):
    def test_literal_snippet_ignores_surrounding_page_text(self):
        self.assertEqual(score.best_span('原文。', '页眉原文。页脚'), (0, '原文。'))
        self.assertEqual(score.best_span('原文。', '页眉原问。页脚'), (1, '原问。'))
        self.assertEqual(score.best_span('原文。', ''), (3, ''))

    def test_text_keeps_punctuation_but_formula_normalizes_minus_and_subscript(self):
        self.assertNotEqual(score.norm('原文。'), score.norm('原文.'))
        self.assertEqual(score.norm('a₁ − 3', True), 'a1-3')

    def test_bbox_matches_only_once(self):
        a = {'bbox': [0, 0, 10, 10]}
        b = {'bbox': [1, 1, 9, 9]}
        self.assertAlmostEqual(score.iou(a['bbox'], b['bbox']), .64)
        self.assertEqual(len(list(score.matches([a], [a, b]))), 1)
        self.assertEqual(list(score.matches([a], [{'bbox': [1, 1, 2, 2]}])), [])

    def test_otsl_grid(self):
        self.assertEqual(score.otsl_cells('<fcel>A<fcel>B<nl><fcel>1<fcel>2'), [['A', 'B'], ['1', '2']])
        self.assertEqual(score.otsl_cells('<fcel>A<ucel><lcel><ecel><nl><xcel><xcel>'), [['A', '', '', ''], ['', '']])
        table = '<fcel>A<fcel>B<nl><fcel>1<fcel>2<nl>'
        self.assertEqual(score.otsl_grids(f'前文{table}\n后文'), [[['A', 'B'], ['1', '2']]])
        scored = score.score_whole_page_tables({'p': {'tables': [{'cells': [['A', 'B'], ['1', '2']]}]}},
                                               [{'id': 'p', 'text': f'前文{table}\n后文'}])
        self.assertEqual(scored['totals']['correct_cells'], 4)
        self.assertFalse(scored['location_scored'])

    def test_single_round_vlm_and_table_scoring(self):
        ref = {'p': {'text': ['题'], 'tables': [{'bbox': [0, 0, 1000, 1000], 'cells': [['题', 'B'], ['1', '2']]}]}}
        inventory = [{'id': 'p', 'page_chars': 0, 'page_size_px': [100, 100]}]
        call = {'cls': 5, 'box': [0, 0, 100, 100], 'text': '<fcel>题<fcel>B<nl><fcel>1<fcel>2',
                'prompt_tokens': 8, 'generated_tokens': 7, 'hit_limit': False,
                'processor_s': .1, 'generate_s': .4, 'decode_s': .01}
        page = {'id': 'p', 'seconds': 1.2, 'requests': [call], 'prep_s': .3,
                'detect_s': .2, 'image_decode_s': .02, 'recognition_wall_s': .68, 'text': call['text']}
        row = score.score_vlm(ref, inventory, {'rounds': [{'pages': [page]}]}, 'paddlevl')
        self.assertEqual(row['median_12_page_processing_s'], 1.2)
        self.assertEqual(row['groups']['text']['exact'], 1)
        self.assertEqual(row['table_recognition']['totals']['correct_cells'], 4)
        self.assertEqual(row['first_round_totals']['generated_tokens'], 7)

    def test_output_coverage(self):
        result = json.loads((BASE / 'results.json').read_text(encoding='utf-8'))
        variants = ['pymupdf', 'pdftotext', 'windows', 'windows_gray', 'hybrid', 'paddle', 'hybrid_paddle']
        self.assertLessEqual(set(variants), set(result['methods']))
        self.assertLessEqual(set(result['methods']), set(variants + ['xiaomi', 'paddlevl']))
        for name in ('xiaomi', 'paddlevl'):
            self.assertEqual((BASE / '.work' / f'{name}_measurements.json').is_file(), name in result['methods'])
        for name, run in result['methods'].items():
            self.assertEqual(len(run['pages']), 12)
            self.assertEqual(run['groups']['text']['snippets'], 36)
            self.assertTrue(all(x > 0 for x in run['round_processing_s']))
            if name in ('xiaomi', 'paddlevl'):
                self.assertEqual(len(run['round_processing_s']), 1)
                self.assertEqual(run['first_round_totals']['hit_limit'], sum(p['hit_limit'] for p in run['pages'].values()))
        if 'paddlevl' in result['methods']:
            self.assertEqual(result['methods']['paddlevl']['table_recognition']['totals']['predicted'], 5)
        if 'xiaomi' in result['methods']:
            tables = result['methods']['xiaomi']['whole_page_table_cells']
            self.assertEqual(tables['totals']['expected'], 4)
            self.assertEqual(tables['totals']['pages_with_one_candidate'], 4)
            self.assertFalse(tables['location_scored'])
        request_keys = {(x['case'], x['mode']) for x in result['llm']['requests']}
        llm_cases = json.loads((BASE / 'llm_cases.json').read_text(encoding='utf-8'))['cases']
        self.assertEqual(request_keys, {(case['id'], mode) for case in llm_cases for mode in ('verbatim', 'references')})
        self.assertEqual(len(result['llm']['requests']), len(request_keys))


if __name__ == '__main__':
    unittest.main()
