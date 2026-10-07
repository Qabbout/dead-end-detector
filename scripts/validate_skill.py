#!/usr/bin/env python3
"""Validate this repository's flat name/description Agent Skills profile.

This is a focused package check, not a general YAML or Agent Skills validator.
Author: Abdulrahman Qabbout.
"""
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL = ROOT / "skills" / "dead-end-detector"


def validate(folder):
    folder = Path(folder).resolve()
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("missing skill entry point") from error
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        raise ValueError("missing YAML frontmatter delimiters")
    fields = {}
    for line in match[1].splitlines():
        field = re.fullmatch(r"(name|description): (.+)", line)
        if not field or field[1] in fields:
            raise ValueError("expected unique flat name and description fields")
        fields[field[1]] = field[2]
    if fields.keys() != {"name", "description"}:
        raise ValueError("missing required frontmatter")
    name = fields["name"]
    if (len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or name != folder.name):
        raise ValueError("invalid or mismatched skill name")
    description = fields["description"]
    if (len(description) > 1024 or not description.startswith("Use when ")
            or description != description.strip()
            or ": " in description or " #" in description):
        raise ValueError("invalid trigger description in flat YAML profile")
    words = len(text.split())
    if words > 500 or len(text.splitlines()) > 500:
        raise ValueError("skill entry point exceeds repository size budget")
    for document in folder.rglob("*.md"):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            resolved = (document.parent / target.split("#", 1)[0]).resolve()
            if not resolved.is_relative_to(folder) or not resolved.is_file():
                raise ValueError("missing reference or dependency outside skill package")
    return {"name": name, "words": words, "bytes": len(text.encode("utf-8"))}


def main():
    if len(sys.argv) > 2:
        print("Usage: python3 validate_skill.py [SKILL_FOLDER]", file=sys.stderr)
        return 2
    try:
        result = validate(sys.argv[1] if len(sys.argv) == 2 else DEFAULT_SKILL)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Package validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Portable package checks passed: {result['name']}; "
          f"{result['words']} words, {result['bytes']} UTF-8 bytes in SKILL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
