from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSED = ROOT / "parsed"
FILES_CSV = ROOT / "data" / "original_downloads" / "files.csv"
MANIFEST = ROOT / "data" / "question_extraction" / "2026" / "parsed_reorganization_manifest.csv"
ACTIVE_REFERENCE_FILES = [
    ROOT / "_orchestration" / "tasks_2026.json",
    ROOT / "_orchestration" / "latest_summary.json",
    ROOT / "data" / "question_extraction" / "2026" / "status.json",
    ROOT / "data" / "question_extraction" / "2026" / "chinese" / "tasks.json",
    ROOT / "data" / "question_extraction" / "2026" / "pilot_tasks.json",
]
SOURCE_FILE_RE = re.compile(
    r'("source"\s*:\s*\{\s*"file"\s*:\s*")((?:\\.|[^"\\])*)(")',
    re.DOTALL,
)


def norm_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def norm_rel(value: str) -> str:
    return Path(value.replace("\\", "/")).as_posix().strip("./")


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def relative_output(row: dict) -> Path:
    source_name = Path(row["local_path"].replace("\\", "/")).name
    return Path("parsed") / row["subject"] / str(row["year"]) / row["kind"] / source_name


def collect_source_rows() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    rows, _ = read_csv(FILES_CSV)
    by_path: dict[str, dict] = {}
    by_entry: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("file_type") != "原始附件" or not row.get("local_path", "").replace("\\", "/").startswith("raw/"):
            continue
        absolute = (ROOT / row["local_path"].replace("\\", "/")).resolve()
        key = norm_path(absolute)
        if key in by_path:
            raise RuntimeError(f"Duplicate raw source path in files.csv: {row['local_path']}")
        by_path[key] = row
        by_entry.setdefault(row["entry_key"], []).append(row)
    for entry_rows in by_entry.values():
        entry_rows.sort(key=lambda item: int(item.get("sequence", "0") or 0))
    return by_path, by_entry


def read_source_ref(text: str, bundle_dir: Path) -> tuple[str, Path]:
    match = SOURCE_FILE_RE.search(text)
    if not match:
        raise RuntimeError(f"No top-level source.file field in {bundle_dir / 'paper.json'}")
    encoded = match.group(2)
    source_ref = json.loads('"' + encoded + '"')
    source_path = (bundle_dir / source_ref.replace("\\", "/")).resolve()
    return source_ref, source_path


def task_output(task: dict, old_outputs: dict[str, str], source_outputs: dict[str, str], entry_sources: dict[str, list[dict]]) -> str | None:
    old = task.get("output_dir")
    if isinstance(old, str) and norm_rel(old) in old_outputs:
        return old_outputs[norm_rel(old)]

    candidates = task.get("source_files")
    if isinstance(candidates, list):
        for candidate in candidates:
            key = norm_path((ROOT / str(candidate).replace("\\", "/")).resolve())
            if key in source_outputs:
                return source_outputs[key]

    entry_key = task.get("entry_key")
    if entry_key in entry_sources and entry_sources[entry_key]:
        return relative_output(entry_sources[entry_key][0]).as_posix()

    metadata = task.get("paper_metadata") if isinstance(task.get("paper_metadata"), dict) else task
    subject, year, kind = metadata.get("subject"), metadata.get("year"), metadata.get("kind")
    if isinstance(candidates, list) and candidates and subject and year and kind:
        return (Path("parsed") / str(subject) / str(year) / str(kind) / Path(str(candidates[0]).replace("\\", "/")).name).as_posix()
    return None


def rewrite_task_outputs(value, old_outputs: dict[str, str], source_outputs: dict[str, str], entry_sources: dict[str, list[dict]]) -> tuple[object, int]:
    changes = 0
    if isinstance(value, list):
        updated = []
        for item in value:
            result, count = rewrite_task_outputs(item, old_outputs, source_outputs, entry_sources)
            updated.append(result)
            changes += count
        return updated, changes
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "output_dir" and isinstance(item, str):
                candidate = task_output(value, old_outputs, source_outputs, entry_sources)
                if candidate is not None:
                    result[key] = candidate
                    changes += candidate != item
                    continue
            new_item, count = rewrite_task_outputs(item, old_outputs, source_outputs, entry_sources)
            result[key] = new_item
            changes += count
        return result, changes
    return value, 0


def legacy_output_parts(row: dict) -> tuple[str, ...]:
    parts = [str(row["year"]), row["kind"], row["district"]]
    school = row.get("region_or_school") or row["district"]
    if school != row["district"]:
        parts.append(school)
    parts.append(row["subject"])
    if row.get("variant"):
        parts.append(row["variant"])
    return tuple(parts)


def plan() -> tuple[list[dict], dict[str, str], dict[str, str], dict[str, list[dict]]]:
    source_rows, entry_sources = collect_source_rows()
    papers = sorted(PARSED.rglob("paper.json"))
    moves = []
    old_outputs: dict[str, str] = {}
    source_outputs: dict[str, str] = {}
    target_owners: dict[str, Path] = {}
    paper_bundle_dirs: set[str] = set()

    for paper_path in papers:
        bundle_dir = paper_path.parent
        paper_bundle_dirs.add(norm_path(bundle_dir))
        text = paper_path.read_text(encoding="utf-8")
        source_ref, source_path = read_source_ref(text, bundle_dir)
        key = norm_path(source_path)
        row = source_rows.get(key)
        if row is None:
            raise RuntimeError(f"source.file does not match an original attachment in files.csv: {paper_path} -> {source_ref}")
        if not source_path.is_file():
            raise RuntimeError(f"Missing original source: {source_path}")

        target_rel = relative_output(row)
        target_dir = ROOT / target_rel
        target_key = norm_path(target_dir)
        owner = target_owners.get(target_key)
        if owner is not None and owner != bundle_dir:
            raise RuntimeError(f"Two processed bundles map to the same target: {owner} and {bundle_dir} -> {target_rel}")
        target_owners[target_key] = bundle_dir
        if target_dir.exists() and norm_path(target_dir) != norm_path(bundle_dir):
            raise RuntimeError(f"Destination already exists: {target_rel}")

        old_rel = bundle_dir.relative_to(ROOT).as_posix()
        new_rel = target_rel.as_posix()
        old_outputs[norm_rel(old_rel)] = new_rel
        source_outputs[key] = new_rel
        new_source_ref = os.path.relpath(source_path, target_dir).replace(os.sep, "/")
        moves.append({
            "action": "paper",
            "paper": paper_path,
            "old_dir": bundle_dir,
            "new_dir": target_dir,
            "old_rel": old_rel,
            "new_rel": new_rel,
            "source": source_path,
            "old_source_ref": source_ref,
            "new_source_ref": new_source_ref,
            "text": text,
        })

    asset_target_owners: dict[str, Path] = {}
    for assets_dir in sorted(PARSED.rglob("assets")):
        if norm_path(assets_dir.parent) in paper_bundle_dirs:
            continue
        if not assets_dir.is_dir() or not any(path.is_file() for path in assets_dir.rglob("*")):
            continue
        current_output = assets_dir.parent.relative_to(ROOT).as_posix()
        organized_rows = [row for row in source_rows.values() if relative_output(row).as_posix() == current_output]
        if organized_rows:
            if len(organized_rows) != 1:
                raise RuntimeError(f"Ambiguous organized assets folder: {assets_dir}")
            row = organized_rows[0]
            source_path = (ROOT / row["local_path"].replace("\\", "/")).resolve()
            source_key = norm_path(source_path)
            target_rel = relative_output(row).as_posix()
            previous_source = source_outputs.get(source_key)
            if previous_source is not None and previous_source != target_rel:
                raise RuntimeError(f"Conflicting source output mapping for {source_path}")
            source_outputs[source_key] = target_rel
            old_outputs[norm_rel(current_output)] = target_rel
            asset_rel = assets_dir.relative_to(ROOT).as_posix()
            moves.append({
                "action": "assets",
                "paper": None,
                "old_dir": assets_dir,
                "new_dir": assets_dir,
                "old_rel": asset_rel,
                "new_rel": asset_rel,
                "source": source_path,
                "old_source_ref": "",
                "new_source_ref": "",
                "text": "",
            })
            continue
        legacy_parts = assets_dir.relative_to(PARSED).parts[:-1]
        matching_rows = [row for row in source_rows.values() if legacy_output_parts(row) == legacy_parts]
        matching_targets = {norm_path(ROOT / relative_output(row)): row for row in matching_rows}
        if len(matching_targets) != 1:
            raise RuntimeError(f"Cannot uniquely map legacy assets folder: {assets_dir}")
        target_key, row = next(iter(matching_targets.items()))
        source_path = (ROOT / row["local_path"].replace("\\", "/")).resolve()
        if not source_path.is_file():
            raise RuntimeError(f"Missing original source for legacy assets: {source_path}")
        target_dir = ROOT / relative_output(row)
        target_assets = target_dir / "assets"
        if target_assets.exists() and norm_path(target_assets) != norm_path(assets_dir):
            raise RuntimeError(f"Legacy and organized assets both exist: {assets_dir} and {target_assets}")
        owner = asset_target_owners.get(target_key)
        if owner is not None and owner != assets_dir:
            raise RuntimeError(f"Two legacy assets folders map to the same result: {owner} and {assets_dir}")
        asset_target_owners[target_key] = assets_dir

        old_rel = assets_dir.relative_to(ROOT).as_posix()
        new_rel = target_assets.relative_to(ROOT).as_posix()
        source_key = norm_path(source_path)
        legacy_output = assets_dir.parent.relative_to(ROOT).as_posix()
        previous_output = old_outputs.get(norm_rel(legacy_output))
        if previous_output is not None and previous_output != target_dir.relative_to(ROOT).as_posix():
            raise RuntimeError(f"Conflicting legacy output mapping for {legacy_output}")
        old_outputs[norm_rel(legacy_output)] = target_dir.relative_to(ROOT).as_posix()
        previous_source = source_outputs.get(source_key)
        target_output = target_dir.relative_to(ROOT).as_posix()
        if previous_source is not None and previous_source != target_output:
            raise RuntimeError(f"Conflicting source output mapping for {source_path}")
        source_outputs[source_key] = target_output
        moves.append({
            "action": "assets",
            "paper": None,
            "old_dir": assets_dir,
            "new_dir": target_assets,
            "old_rel": old_rel,
            "new_rel": new_rel,
            "source": source_path,
            "old_source_ref": "",
            "new_source_ref": "",
            "text": "",
        })

    return moves, old_outputs, source_outputs, entry_sources


def update_active_references(old_outputs: dict[str, str], source_outputs: dict[str, str], entry_sources: dict[str, list[dict]], apply: bool) -> tuple[dict[Path, bytes], int]:
    backups: dict[Path, bytes] = {}
    changed = 0
    for path in ACTIVE_REFERENCE_FILES:
        if not path.is_file():
            continue
        original = path.read_bytes()
        value = json.loads(original.decode("utf-8"))
        updated, count = rewrite_task_outputs(value, old_outputs, source_outputs, entry_sources)
        if count:
            changed += count
            if apply:
                backups[path] = original
                temporary = path.with_suffix(path.suffix + ".reorg.tmp")
                temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(path)
    return backups, changed


def write_manifest(moves: list[dict]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = MANIFEST.with_suffix(MANIFEST.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["item_type", "old_directory", "new_directory", "source_file", "paper_json"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for move in moves:
            writer.writerow({
                "item_type": move["action"],
                "old_directory": move["old_rel"],
                "new_directory": move["new_rel"],
                "source_file": move["source"].relative_to(ROOT).as_posix(),
                "paper_json": (Path(move["new_rel"]) / "paper.json").as_posix()
                if move["action"] == "paper" else "",
            })
    temporary.replace(MANIFEST)


def prune_empty_parents(paths: list[Path]) -> int:
    removed = 0
    for path in sorted(set(paths), key=lambda item: len(item.parts), reverse=True):
        current = path
        while current != PARSED and current.is_relative_to(PARSED):
            try:
                current.rmdir()
                removed += 1
            except OSError:
                break
            current = current.parent
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Reorganize processed exam bundles under the standardized paper layout")
    parser.add_argument("--apply", action="store_true", help="move bundles and update references (default: dry run)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    moves, old_outputs, source_outputs, entry_sources = plan()
    active_changes = 0
    for path in ACTIVE_REFERENCE_FILES:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            _, count = rewrite_task_outputs(value, old_outputs, source_outputs, entry_sources)
            active_changes += count

    changed_moves = [move for move in moves if norm_path(move["old_dir"]) != norm_path(move["new_dir"])]
    # Some legacy output bundles contain a nested variant bundle; move the deepest first.
    changed_moves.sort(key=lambda move: len(move["old_dir"].parts), reverse=True)
    paper_count = sum(move["action"] == "paper" for move in moves)
    asset_only_count = sum(move["action"] == "assets" for move in moves)
    print(f"Processed output items: {len(moves)} ({paper_count} paper bundles, {asset_only_count} asset-only folders); directories to move: {len(changed_moves)}")
    print(f"Active task/reference output_dir fields to update: {active_changes}")
    for move in changed_moves[:12]:
        print(f"  {move['old_rel']} -> {move['new_rel']}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to move bundles and update references.")
        return 0

    moved: list[tuple[Path, Path]] = []
    paper_backups: dict[Path, bytes] = {}
    reference_backups: dict[Path, bytes] = {}
    original_manifest = MANIFEST.read_bytes() if MANIFEST.exists() else None
    try:
        for move in changed_moves:
            move["new_dir"].parent.mkdir(parents=True, exist_ok=True)
            move["old_dir"].rename(move["new_dir"])
            moved.append((move["old_dir"], move["new_dir"]))

        for move in moves:
            if move["action"] != "paper":
                continue
            paper = move["new_dir"] / "paper.json"
            old_text = move["text"]
            escaped = json.dumps(move["new_source_ref"], ensure_ascii=False)[1:-1]
            new_text, count = SOURCE_FILE_RE.subn(lambda match: match.group(1) + escaped + match.group(3), old_text, count=1)
            if count != 1:
                raise RuntimeError(f"Could not update source.file in {paper}")
            if new_text != old_text:
                paper_backups[paper] = old_text.encode("utf-8")
                paper.write_text(new_text, encoding="utf-8")

        reference_backups, _ = update_active_references(old_outputs, source_outputs, entry_sources, apply=True)
        write_manifest(moves)
        removed = prune_empty_parents([move["old_dir"].parent for move in changed_moves])
    except Exception:
        for path, content in {**reference_backups, **paper_backups}.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for old_dir, new_dir in reversed(moved):
            if new_dir.exists() and not old_dir.exists():
                old_dir.parent.mkdir(parents=True, exist_ok=True)
                new_dir.rename(old_dir)
        if original_manifest is not None:
            MANIFEST.write_bytes(original_manifest)
        elif MANIFEST.exists():
            MANIFEST.unlink()
        raise

    moved_papers = sum(move["action"] == "paper" for move in changed_moves)
    moved_assets = sum(move["action"] == "assets" for move in changed_moves)
    print(f"Moved {moved_papers} paper bundles and {moved_assets} asset-only folders; updated {len(paper_backups)} source.file references.")
    print(f"Updated {len(reference_backups)} active JSON files; pruned {removed} empty legacy directories.")
    print(f"Manifest: {MANIFEST.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
