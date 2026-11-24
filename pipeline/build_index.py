"""Build or verify the root Index.json for raw and processed exam files.

Read-only with respect to raw/, parsed/ and pipeline/state/. Run with Python -I.
"""
import argparse
import collections
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'Index.json'
CATALOG = ROOT / 'pipeline/manifests/papers_catalog.json'
SOURCE_PAGES = ROOT / 'pipeline/manifests/source_pages.json'


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_record(path, category):
    return {'path': relative(path), 'category': category,
            'bytes': path.stat().st_size, 'sha256': sha256(path)}


def leaf_count(nodes):
    return sum(leaf_count(node.get('parts', [])) if node.get('parts') else 1 for node in nodes)


def source_path(paper_path, reference):
    return (paper_path.parent / reference).resolve()


def load_catalog():
    if not CATALOG.is_file():
        return []
    return json.loads(CATALOG.read_text(encoding='utf-8-sig'))


def load_source_pages():
    if not SOURCE_PAGES.is_file():
        return []
    return json.loads(SOURCE_PAGES.read_text(encoding='utf-8-sig'))


def build():
    catalog = load_catalog()
    source_pages = load_source_pages()
    raw_files = sorted((path for path in (ROOT / 'raw').rglob('*') if path.is_file()),
                       key=lambda path: relative(path))
    parsed_files = sorted((path for path in (ROOT / 'parsed').rglob('*') if path.is_file()),
                          key=lambda path: relative(path))
    files = [file_record(path, 'raw') for path in raw_files]
    files.extend(file_record(path, 'paper_json' if path.name == 'paper.json' else 'processed_asset')
                 for path in parsed_files)

    queue_status = {}
    state_path = ROOT / 'pipeline/state/status.json'
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding='utf-8-sig'))
        queue_status = {task['entry_key']: task.get('status', 'unknown')
                        for task in state.get('full_batch', {}).get('tasks', [])}

    papers, errors = [], []
    referenced_sources = set()
    for paper_path in sorted((ROOT / 'parsed').rglob('paper.json'), key=lambda path: relative(path)):
        try:
            data = json.loads(paper_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as error:
            errors.append({'path': relative(paper_path), 'error': str(error)})
            papers.append({
                'id': None, 'title': None, 'subject': None, 'year': None, 'kind': None,
                'district': None, 'school': None, 'variant': None,
                'paper_json': relative(paper_path), 'paper_sha256': sha256(paper_path),
                'source_files': [], 'extraction_status': 'invalid_json',
                'queue_status': 'unknown', 'top_level_questions': None,
                'leaf_questions': None, 'assets': None,
            })
            continue
        metadata = data.get('paper', {})
        source = data.get('source', {})
        references = [source.get('file', '')]
        references.extend(answer.get('file', '') for answer in source.get('answer_files', []))
        sources = []
        for reference in filter(None, references):
            path = source_path(paper_path, reference)
            try:
                name = relative(path)
            except ValueError:
                errors.append({'path': relative(paper_path), 'error': f'Source outside project: {reference}'})
                continue
            sources.append(name)
            if path.is_file():
                referenced_sources.add(path)
            else:
                errors.append({'path': relative(paper_path), 'error': f'Missing source: {reference}'})
        for asset_id, asset in data.get('assets', {}).items():
            path = source_path(paper_path, asset.get('path', ''))
            if not path.is_file():
                errors.append({'path': relative(paper_path), 'error': f'Missing asset {asset_id}: {asset.get("path", "")}'})
        papers.append({
            'id': metadata.get('id'), 'title': metadata.get('title'),
            'subject': metadata.get('subject'), 'year': metadata.get('year'),
            'kind': metadata.get('kind'), 'district': metadata.get('district'),
            'school': metadata.get('school'), 'variant': metadata.get('variant'),
            'paper_json': relative(paper_path), 'paper_sha256': sha256(paper_path),
            'source_files': sources, 'extraction_status': data.get('extraction', {}).get('status', 'unknown'),
            'queue_status': queue_status.get(metadata.get('id'), 'not_in_2026_queue'),
            'top_level_questions': len(data.get('questions', [])),
            'leaf_questions': leaf_count(data.get('questions', [])),
            'assets': len(data.get('assets', {})),
        })

    unprocessed = [relative(path) for path in raw_files if path.resolve() not in referenced_sources]
    return {
        'format': 'exam-library-index/v1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'directories': {'raw': 'raw/', 'processed': 'parsed/', 'pipeline': 'pipeline/', 'archive': 'backup/'},
        'pipeline': {
            'version': 'local-hybrid-v1', 'readme': 'pipeline/README.md',
            'entrypoint': 'pipeline/native/run_2026_all.exe',
            'python_entrypoint': 'pipeline/run_2026_all.py',
            'schema': 'pipeline/docs/question-json-v1.schema.json',
            'catalog': 'pipeline/manifests/papers_catalog.json',
            'source_pages': 'pipeline/manifests/source_pages.json',
            'rebuild_index': 'python -I pipeline/build_index.py',
        },
        'summary': {
            'raw_files': len(raw_files), 'processed_papers': len(papers),
            'valid_processed_papers': sum(paper['extraction_status'] != 'invalid_json' for paper in papers),
            'processed_assets': sum(path.name != 'paper.json' for path in parsed_files),
            'indexed_files': len(files), 'unprocessed_raw_files': len(unprocessed),
            'extraction_status': dict(collections.Counter(paper['extraction_status'] for paper in papers)),
            'queue_status': dict(collections.Counter(queue_status.values())),
            'catalog_papers': len(catalog),
            'catalog_by_year': dict(sorted(collections.Counter(str(item.get('year')) for item in catalog).items())),
            'catalog_by_kind': dict(collections.Counter(item.get('kind') for item in catalog)),
            'catalog_count_status': dict(collections.Counter(item.get('count_status') for item in catalog)),
            'source_pages': len(source_pages),
            'index_errors': len(errors),
        },
        'catalog': catalog,
        'source_pages': source_pages,
        'papers': papers,
        'unprocessed_raw_files': unprocessed,
        'files': files,
        'errors': errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='verify Index.json without rewriting it')
    args = parser.parse_args()
    expected = build()
    if args.check:
        actual = json.loads(DESTINATION.read_text(encoding='utf-8'))
        expected['generated_at'] = actual.get('generated_at')
        if actual != expected:
            raise SystemExit('Index.json is stale or inconsistent; run python -I pipeline/build_index.py')
        print(json.dumps({'index': 'Index.json', 'current': True, **expected['summary']}, ensure_ascii=False))
        return
    temporary = DESTINATION.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(DESTINATION)
    print(json.dumps({'index': relative(DESTINATION), **expected['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
