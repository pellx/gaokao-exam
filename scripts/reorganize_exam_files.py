from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES_CSV = ROOT / "data" / "original_downloads" / "files.csv"
MANIFEST = ROOT / "data" / "original_downloads" / "reorganization_manifest.csv"


def load_layout():
    spec = importlib.util.spec_from_file_location("paper_layout", Path(__file__).with_name("paper_layout.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load scripts/paper_layout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


layout = load_layout()


def load_rows() -> tuple[list[dict], list[str]]:
    with FILES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    return rows, fields


def normalized_relpath(value: str) -> str:
    return Path(value.replace("\\", "/")).as_posix()


def plan_moves(rows: list[dict]) -> tuple[dict[str, str], dict[str, int]]:
    old_paths = [normalized_relpath(row["local_path"]) for row in rows]
    duplicates = [path for path, count in Counter(old_paths).items() if count > 1]
    if duplicates:
        raise RuntimeError(f"Duplicate source paths in files.csv: {duplicates[:5]}")

    attachments = [row for row in rows if row["file_type"] == "原始附件"]
    pages = [row for row in rows if row["file_type"] == "来源网页"]
    groups: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    for row in attachments:
        extension = Path(row["local_path"]).suffix.lower().lstrip(".")
        groups[(row["subject"], row["year"], row["kind"], row["district"], extension)].append(row)

    targets: dict[str, str] = {}
    for group in groups.values():
        collided = len(group) > 1
        candidates: dict[str, list[dict]] = defaultdict(list)
        for row in group:
            entry = {
                "subject": row["subject"],
                "year": row["year"],
                "kind": row["kind"],
                "district": row["district"],
                "region_or_school": row["region_or_school"],
                "variant": row["variant"],
            }
            extension = Path(row["local_path"]).suffix.lower().lstrip(".")
            candidate = layout.paper_relative_path(entry, extension, disambiguate=collided).as_posix()
            candidates[candidate].append(row)
        for candidate, same_name_rows in candidates.items():
            if len(same_name_rows) == 1:
                targets[normalized_relpath(same_name_rows[0]["local_path"])] = candidate
                continue
            for copy_index, row in enumerate(sorted(same_name_rows, key=lambda item: item["local_path"]), 1):
                entry = {
                    "subject": row["subject"],
                    "year": row["year"],
                    "kind": row["kind"],
                    "district": row["district"],
                    "region_or_school": row["region_or_school"],
                    "variant": row["variant"],
                }
                extension = Path(row["local_path"]).suffix.lower().lstrip(".")
                target = layout.paper_relative_path(
                    entry, extension, disambiguate=True, copy_index=copy_index
                ).as_posix()
                targets[normalized_relpath(row["local_path"])] = target

    page_indices: Counter[str] = Counter()
    for row in pages:
        page_indices[row["entry_key"]] += 1
        entry = {
            "subject": row["subject"],
            "year": row["year"],
            "kind": row["kind"],
            "district": row["district"],
            "sequence": row["sequence"],
        }
        target = layout.source_page_relative_path(entry, page_indices[row["entry_key"]]).as_posix()
        targets[normalized_relpath(row["local_path"])] = target

    if len(set(targets.values())) != len(targets):
        reverse: dict[str, list[str]] = defaultdict(list)
        for old, new in targets.items():
            reverse[new].append(old)
        collisions = {new: old for new, old in reverse.items() if len(old) > 1}
        raise RuntimeError(f"Target path collision after disambiguation: {list(collisions.items())[:5]}")
    return targets, {
        "attachments": len(attachments),
        "source_pages": len(pages),
        "collision_groups": sum(len(group) > 1 for group in groups.values()),
        "colliding_attachments": sum(len(group) for group in groups.values() if len(group) > 1),
    }


def create_reference_rewriter(path_map: dict[str, str]):
    replacements: dict[str, str] = {}
    for old, new in path_map.items():
        old_windows = old.replace("/", "\\")
        new_windows = new.replace("/", "\\")
        replacements[old] = new
        replacements[old_windows] = new_windows
        replacements[json.dumps(old_windows, ensure_ascii=False)[1:-1]] = json.dumps(
            new_windows, ensure_ascii=False
        )[1:-1]
    pattern = re.compile("|".join(re.escape(value) for value in sorted(replacements, key=len, reverse=True)))

    def rewrite(text: str) -> str:
        return pattern.sub(lambda match: replacements[match.group(0)], text)

    return rewrite


def update_machine_references(path_map: dict[str, str]) -> tuple[int, int]:
    rewrite = create_reference_rewriter(path_map)
    changed_files = 0
    skipped_non_utf8 = 0
    for directory in (ROOT / "data", ROOT / "parsed", ROOT / "_orchestration", ROOT / "benchmark"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
                continue
            if ROOT / "data" / "cache" in path.parents:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                skipped_non_utf8 += 1
                continue
            updated = rewrite(text)
            if updated != text:
                temporary = path.with_name(path.name + ".reorg.tmp")
                temporary.write_text(updated, encoding="utf-8", newline="")
                temporary.replace(path)
                changed_files += 1
    return changed_files, skipped_non_utf8


def write_manifest(path_map: dict[str, str], rows: list[dict]) -> None:
    by_path = {normalized_relpath(row["local_path"]): row for row in rows}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file_type", "sha256", "old_path", "new_path"])
        writer.writeheader()
        for old, new in path_map.items():
            row = by_path[old]
            writer.writerow({
                "file_type": row["file_type"],
                "sha256": row.get("sha256", ""),
                "old_path": old,
                "new_path": new,
            })


def update_files_csv(rows: list[dict], fields: list[str], path_map: dict[str, str]) -> None:
    temporary = FILES_CSV.with_name(FILES_CSV.name + ".reorg.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row["local_path"] = path_map[normalized_relpath(row["local_path"])].replace("/", "\\")
            writer.writerow(row)
    temporary.replace(FILES_CSV)


def main() -> int:
    parser = argparse.ArgumentParser(description="Move raw exam files into the standardized subject/year/kind layout")
    parser.add_argument("--apply", action="store_true", help="perform the planned moves (default: dry run)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rows, fields = load_rows()
    path_map, counts = plan_moves(rows)
    non_ascii_names = [new for new in path_map.values() if not Path(new).name.isascii()]
    invalid_names = [
        new for new in path_map.values()
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in Path(new).name)
        or "." not in Path(new).name
    ]
    if non_ascii_names or invalid_names:
        raise RuntimeError(f"Non-English or invalid filenames: {(non_ascii_names + invalid_names)[:10]}")
    if MANIFEST.exists():
        with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
            prior_manifest = {row["old_path"]: row["new_path"] for row in csv.DictReader(handle)}
        if prior_manifest != path_map:
            raise RuntimeError(f"A different migration manifest already exists: {MANIFEST}")
        print("Found a matching manifest from the rolled-back attempt; it will be reused.")

    missing = []
    existing_targets = []
    total_bytes = 0
    for old, new in path_map.items():
        source = ROOT / Path(old)
        target = ROOT / Path(new)
        if not source.is_file():
            missing.append(old)
            continue
        row = next(row for row in rows if normalized_relpath(row["local_path"]) == old)
        expected_bytes = int(row.get("bytes") or 0)
        if expected_bytes and source.stat().st_size != expected_bytes:
            raise RuntimeError(f"Source size differs from files.csv: {old}")
        total_bytes += source.stat().st_size
        if target.exists():
            existing_targets.append(new)
    if missing:
        raise RuntimeError(f"Missing source files: {missing[:10]}")
    if existing_targets:
        raise RuntimeError(f"Destination files already exist: {existing_targets[:10]}")

    print(f"Files to reorganize: {len(path_map)} ({counts['attachments']} exam attachments, {counts['source_pages']} saved source pages)")
    print(f"Filename collisions resolved: {counts['collision_groups']} groups, {counts['colliding_attachments']} attachments")
    print(f"Total bytes moved: {total_bytes:,}")
    print("Sample destinations:")
    for old, new in list(path_map.items())[:12]:
        print(f"  {old} -> {new}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to move files and update references.")
        return 0

    original_files_csv = FILES_CSV.read_bytes()
    moved: list[tuple[Path, Path]] = []
    try:
        write_manifest(path_map, rows)
        for old, new in path_map.items():
            source = ROOT / Path(old)
            target = ROOT / Path(new)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append((source, target))
        for old, new in path_map.items():
            row = next(row for row in rows if normalized_relpath(row["local_path"]) == old)
            new_file = ROOT / Path(new)
            expected_bytes = int(row.get("bytes") or 0)
            if not new_file.is_file() or (expected_bytes and new_file.stat().st_size != expected_bytes):
                raise RuntimeError(f"Moved file failed post-move verification: {new}")
        update_files_csv(rows, fields, path_map)
        changed_json, skipped_json = update_machine_references(path_map)
    except Exception:
        rollback_errors = []
        try:
            update_machine_references({new: old for old, new in path_map.items()})
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        try:
            FILES_CSV.write_bytes(original_files_csv)
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        for source, target in reversed(moved):
            try:
                if target.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(source)
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if not rollback_errors and MANIFEST.exists():
            MANIFEST.unlink()
        if rollback_errors:
            print("Rollback needs attention:", "; ".join(rollback_errors), file=sys.stderr)
        raise

    print(f"Moved {len(moved)} files; updated files.csv and {changed_json} JSON/JSONL metadata files; skipped {skipped_json} non-UTF-8 JSON/JSONL files without decoding.")
    print(f"Rollback mapping saved to {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
