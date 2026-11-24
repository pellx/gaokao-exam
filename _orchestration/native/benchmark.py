"""Reproducible, read-only log-parser benchmark against the Python controller."""
import argparse
import importlib.util
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('logs', nargs='*', type=Path)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--output', type=Path, default=NATIVE / 'benchmark_results.json')
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    spec = importlib.util.spec_from_file_location('compat', ROOT / '_orchestration/run_2026_all.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    paths = args.logs or [ROOT / '_orchestration/chemistry_review_resume.jsonl',
                          ROOT / '_orchestration/chinese_127_note_review.jsonl']
    results = []
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'result.json'
        for path in paths:
            path = path.resolve()
            py_times, c_times = [], []
            for _ in range(args.repeat):
                start = time.perf_counter()
                expected = m.results(path)
                py_times.append(time.perf_counter() - start)
                start = time.perf_counter()
                subprocess.run([str(NATIVE / 'run_2026_all.exe'), '--parse-log', str(path),
                                '--result', str(output)], check=True, capture_output=True)
                c_times.append(time.perf_counter() - start)
                actual = json.loads(output.read_text(encoding='utf-8'))
                if expected != actual:
                    raise AssertionError(f'Parser output mismatch: {path}')
            p, c = statistics.median(py_times), statistics.median(c_times)
            row = {'log': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
                   'python_median_s': p, 'c_median_s': c, 'speedup': p / c,
                   'python_runs_s': py_times, 'c_runs_s': c_times, 'identical_results': True}
            results.append(row)
            print(f'{row["log"]}: Python {p:.4f}s, C {c:.4f}s, {p/c:.2f}x', flush=True)
    report = {'repeat': args.repeat, 'scope': 'Local JSONL result parsing only; C timings include process startup and output write. Not end-to-end model throughput.',
              'results': results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
