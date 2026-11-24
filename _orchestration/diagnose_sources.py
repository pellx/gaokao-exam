"""Inventory PDF text/image availability and DOCX contents without executing attachments."""
import collections
import concurrent.futures
import json
import zipfile
from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '_orchestration'
TASKS = json.loads((WORK / 'tasks_2026.json').read_text(encoding='utf-8'))


def diagnose(task):
    report = {'entry_key': task['entry_key'], 'sequence': task['sequence'], 'sources': []}
    for source in task['source_files']:
        path = ROOT / source
        item = {'file': source, 'bytes': path.stat().st_size}
        try:
            if path.suffix.lower() == '.pdf':
                with fitz.open(path) as doc:
                    item.update(pages=len(doc), encrypted=doc.is_encrypted,
                                text_chars=sum(len(p.get_text()) for p in doc),
                                embedded_images=sum(len(p.get_images()) for p in doc))
                    item['scanned_or_low_text'] = item['text_chars'] < 1000
            elif path.suffix.lower() == '.docx':
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    item.update(media_files=sum(n.startswith('word/media/') for n in names),
                                xml_bytes=archive.getinfo('word/document.xml').file_size)
            else:
                item['unsupported_extension'] = path.suffix.lower()
        except Exception as error:
            item['error'] = str(error)
        report['sources'].append(item)
    return report


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(diagnose, TASKS))
    output = WORK / 'source_diagnostics_2026.json'
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    sources = [source for report in reports for source in report['sources']]
    print(json.dumps({'tasks': len(reports), 'sources': len(sources),
                      'pdfs': sum('pages' in s for s in sources),
                      'pdf_pages': sum(s.get('pages', 0) for s in sources),
                      'scanned_or_low_text': sum(s.get('scanned_or_low_text', False) for s in sources),
                      'errors': [s for s in sources if 'error' in s or 'unsupported_extension' in s]},
                     ensure_ascii=False))
