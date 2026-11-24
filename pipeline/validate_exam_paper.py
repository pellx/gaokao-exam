"""Validate an exam-questions/v1 paper and its local references."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_assets(value):
    found = []
    if isinstance(value, list):
        for block in value:
            found.extend(content_assets(block))
    elif isinstance(value, dict):
        if "asset" in value:
            found.append(value["asset"])
        elif "table" in value:
            table = value["table"]
            found.extend(content_assets(table.get("header", [])))
            found.extend(content_assets(table.get("rows", [])))
        else:
            for nested in value.values():
                found.extend(content_assets(nested))
    return found


def validate_paper(paper, schema, validator=None):
    """Return a report; allow batch callers to reuse a compiled schema validator."""
    paper_path = Path(paper).resolve()
    schema_path = Path(schema).resolve()
    errors = []
    raw = paper_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("paper.json must not contain a UTF-8 BOM")
    data = json.loads(raw.decode("utf-8"))
    if validator is None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    for error in validator.iter_errors(data):
        location = "/".join(str(part) for part in error.absolute_path)
        errors.append(f"schema {location}: {error.message}")

    base = paper_path.parent

    def check_file(relative, expected_hash, label):
        path = Path(relative)
        if path.is_absolute() or "\\" in relative or PurePosixPath(relative).is_absolute():
            errors.append(f"{label}: path is not a POSIX relative path: {relative}")
            return
        resolved = (base / relative).resolve()
        if not resolved.is_file() or resolved.stat().st_size == 0:
            errors.append(f"{label}: missing or empty file: {relative}")
            return
        if expected_hash and sha256(resolved) != expected_hash:
            errors.append(f"{label}: SHA-256 mismatch: {relative}")

    source = data.get("source", {})
    check_file(source.get("file", ""), source.get("sha256"), "source")
    for index, answer in enumerate(source.get("answer_files", [])):
        check_file(answer.get("file", ""), answer.get("sha256"), f"answer_files[{index}]")

    assets = data.get("assets", {})
    for asset_id, asset in assets.items():
        check_file(asset.get("path", ""), asset.get("sha256"), f"asset {asset_id}")

    defined_materials = set(data.get("materials", {}))
    defined_options = set(data.get("option_sets", {}))
    used_assets = set(content_assets(data.get("materials", {})))
    leaf_count = 0

    def visit(nodes, siblings_label):
        nonlocal leaf_count
        ids = [node.get("id") for node in nodes]
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate sibling question IDs at {siblings_label}")
        for node in nodes:
            node_label = f"{siblings_label}/{node.get('id', '?')}"
            for material_id in node.get("materials", []):
                if material_id not in defined_materials:
                    errors.append(f"{node_label}: undefined material {material_id}")
            option_set = node.get("option_set")
            if option_set and option_set not in defined_options:
                errors.append(f"{node_label}: undefined option_set {option_set}")
            for field in ("stem", "analysis"):
                used_assets.update(content_assets(node.get(field)))
            for option in node.get("options", []):
                used_assets.update(content_assets(option.get("content")))
            answer = node.get("answer")
            if answer:
                if answer.get("source") == "inferred":
                    errors.append(f"{node_label}: inferred answer is forbidden")
                used_assets.update(content_assets(answer.get("content")))
                for rubric in answer.get("rubric", []):
                    used_assets.update(content_assets(rubric.get("content")))
            if node.get("needs_review") and not node.get("notes"):
                errors.append(f"{node_label}: needs_review requires notes")
            parts = node.get("parts")
            if parts:
                visit(parts, node_label)
            else:
                leaf_count += 1

    visit(data.get("questions", []), "questions")
    for asset_id in sorted(used_assets - set(assets)):
        errors.append(f"undefined asset reference: {asset_id}")

    report = {
        "paper": str(paper_path),
        "valid": not errors,
        "errors": errors,
        "top_level_questions": len(data.get("questions", [])),
        "leaf_questions": leaf_count,
        "materials": len(defined_materials),
        "assets": len(assets),
        "extraction_status": data.get("extraction", {}).get("status"),
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paper", type=Path)
    parser.add_argument("--schema", type=Path, required=True)
    args = parser.parse_args()
    report = validate_paper(args.paper, args.schema)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
