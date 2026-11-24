"""Summarize successful visual reads without printing image base64 or source text."""
import argparse
import json
from pathlib import Path


def evidence(log):
    reads, tools, errors = [], {}, []
    for line in Path(log).read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get('part', {})
        if event.get('type') == 'error':
            errors.append(event.get('error'))
        if event.get('type') != 'tool_use':
            continue
        tool = part.get('tool', '')
        tools[tool] = tools.get(tool, 0) + 1
        state = part.get('state', {})
        if tool == 'read' and state.get('status') == 'completed':
            inputs = state.get('input', {})
            path = inputs.get('path', inputs.get('filePath', inputs.get('file_path', '')))
            if isinstance(path, str) and Path(path).suffix.lower() in ['.png', '.jpg', '.jpeg', '.webp', '.pdf']:
                reads.append(path)
    return {'log': str(log), 'tools': tools, 'visual_reads': sorted(set(reads)), 'errors': errors}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('logs', nargs='+')
    args = parser.parse_args()
    print(json.dumps([evidence(log) for log in args.logs], ensure_ascii=False, indent=2))
