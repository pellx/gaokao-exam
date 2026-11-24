from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES_CSV = ROOT / "data" / "original_downloads" / "files.csv"
MANIFEST = ROOT / "data" / "original_downloads" / "reorganization_manifest.csv"


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "reorganize_exam_files", Path(__file__).with_name("reorganize_exam_files.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load scripts/reorganize_exam_files.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_manifest(rows: list[dict], fieldnames: list[str]) -> None:
    temporary = MANIFEST.with_name(MANIFEST.name + ".repair.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(MANIFEST)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize redundant copy suffixes in standardized exam filenames")
    parser.add_argument("--apply", action="store_true", help="perform the renames (default: dry run)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    migration = load_migration()
    rows, fields = migration.load_rows()
    path_map, _ = migration.plan_moves(rows)
    changed = {old: new for old, new in path_map.items() if old != new}
    if not changed:
        print("No filename repairs needed.")
        return 0
    if len(set(path_map.values())) != len(path_map):
        raise RuntimeError("The corrected target paths are not unique")

    current_paths = set(path_map)
    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        manifest_reader = csv.DictReader(handle)
        manifest_fields = list(manifest_reader.fieldnames or [])
        manifest_rows = list(manifest_reader)
    if len(manifest_rows) != len(rows) or {row["new_path"] for row in manifest_rows} != current_paths:
        raise RuntimeError("The reorganization manifest does not match files.csv")

    for old, new in path_map.items():
        source = ROOT / Path(old)
        target = ROOT / Path(new)
        if not source.is_file():
            raise RuntimeError(f"Missing current file: {old}")
        if new != old and target.exists():
            raise RuntimeError(f"Corrected destination already exists: {new}")

    manifest_by_current = {row["new_path"]: row for row in manifest_rows}
    corrected_manifest = []
    for current, final in path_map.items():
        row = dict(manifest_by_current[current])
        row["new_path"] = final
        corrected_manifest.append(row)

    print(f"Files needing a shorter duplicate suffix: {len(changed)}")
    for old, new in list(changed.items())[:8]:
        print(f"  {old} -> {new}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to rename and update references.")
        return 0

    original_csv = FILES_CSV.read_bytes()
    original_manifest = MANIFEST.read_bytes()
    moved: list[tuple[Path, Path]] = []
    try:
        for old, new in changed.items():
            source = ROOT / Path(old)
            target = ROOT / Path(new)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append((source, target))
        migration.update_files_csv(rows, fields, path_map)
        changed_json, skipped_json = migration.update_machine_references(changed)
        write_manifest(corrected_manifest, manifest_fields)
    except Exception:
        try:
            migration.update_machine_references({new: old for old, new in changed.items()})
        finally:
            FILES_CSV.write_bytes(original_csv)
            MANIFEST.write_bytes(original_manifest)
            for source, target in reversed(moved):
                if target.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(source)
        raise

    print(
        f"Renamed {len(moved)} files; updated {changed_json} JSON/JSONL references; "
        f"skipped {skipped_json} non-UTF-8 metadata files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
