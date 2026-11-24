"""Unit tests for deterministic local preparation and asset cleanup."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local = load('local_extraction_under_test', ROOT / '_orchestration/local_extraction.py')
controller = load('local_controller_under_test', ROOT / '_orchestration/run_2026_all.py')


class LocalExtractionTests(unittest.TestCase):
    def test_text_layer_route(self):
        self.assertEqual(local.text_route('汉字' * 50), 'text_layer')
        self.assertEqual(local.text_route(''), 'paddle_ocr')
        self.assertEqual(local.text_route('A' * 80 + chr(0xE000) * 3), 'paddle_ocr')

    def test_question_hint_uses_left_margin_and_previous_anchor(self):
        lines = [{'text': '1. 第一题', 'bbox': [10, 100, 100, 120]},
                 {'text': '答案：2. B', 'bbox': [500, 200, 700, 220]},
                 {'text': '2. 第二题', 'bbox': [10, 300, 100, 320]}]
        anchors = local.question_anchors(lines, 1000)
        self.assertEqual(local.assign_question([20, 250, 200, 280], anchors), 1)
        self.assertEqual(local.assign_question([20, 340, 200, 400], anchors), 2)

    def test_agent_bundle_omits_line_boxes_and_internal_master_paths(self):
        bundle = {'pipeline': local.PIPELINE_VERSION, 'mode': 'pdf_local',
                  'pages': [{'text': '题', 'lines': [{'text': '题', 'bbox': [0, 0, 1, 1]}]}],
                  'assets': [{'id': 'a_s01_p001_f01', 'sha256': 'abc', 'master_path': '/internal',
                              'crop_sha256': 'abc', 'created_by_pipeline': True}]}
        view = local.agent_view(bundle)
        self.assertNotIn('lines', view['pages'][0])
        self.assertNotIn('master_path', view['assets'][0])
        self.assertNotIn('created_by_pipeline', view['assets'][0])

    def test_prune_only_removes_unchanged_unreferenced_pipeline_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {'output_dir': 'parsed/test'}
            assets = root / task['output_dir'] / 'assets'
            assets.mkdir(parents=True)
            records = []
            for name, created in [('referenced', True), ('removed', True), ('modified', True), ('preexisting', False)]:
                path = assets / f'{name}.png'
                path.write_bytes(name.encode())
                record = {'path': f'assets/{name}.png', 'sha256': controller.digest(path)}
                if created:
                    record['created_by_pipeline'] = True
                records.append(record)
            paper = {'assets': {'a_keep': {'path': 'assets/referenced.png'}}}
            (assets.parent / 'paper.json').write_text(json.dumps(paper), encoding='utf-8')
            (assets / 'modified.png').write_bytes(b'agent edit')
            run = root / 'run/local'
            controller.atomic_json(run / 'source_bundle.json', {'mode': 'pdf_local', 'assets': records})
            with patch.object(controller, 'ROOT', root):
                report = controller.prune_local_assets(task, run.parent)
            self.assertEqual(report['removed_unreferenced'], ['assets/removed.png'])
            self.assertEqual(report['preserved_modified'], ['assets/modified.png'])
            self.assertFalse((assets / 'removed.png').exists())
            self.assertTrue((assets / 'referenced.png').exists())
            self.assertTrue((assets / 'modified.png').exists())
            self.assertTrue((assets / 'preexisting.png').exists())

    def test_prompt_uses_local_bundle(self):
        task = {'entry_key': 'k', 'sequence': 7, 'paper_metadata': {'subject': '数学'}, 'output_dir': 'parsed/test'}
        with patch.object(controller, 'RUN_ID', 'test-run'):
            prompt = controller.extraction_prompt(task, 0, None)
        self.assertIn('runs/test-run/007/local/agent_bundle.json', prompt)
        self.assertIn('禁止重新运行 OCR', prompt)
        self.assertIn('mode=agent_only', prompt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
