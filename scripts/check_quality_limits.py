#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
}


@dataclass(frozen=True)
class Violation:
    kind: str
    path: Path
    value: int
    limit: int

    def format(self, root: Path) -> str:
        rel = self.path.relative_to(root)
        label = "lines" if self.kind == "max-lines" else "direct .py files"
        return f"{self.kind}: {rel} has {self.value} {label}; limit is {self.limit}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Python file line counts and per-directory file fanout.")
    parser.add_argument("--root", default=".", help="scan root")
    parser.add_argument("--max-lines", type=int, default=350, help="maximum lines per source file")
    parser.add_argument("--max-dir-files", type=int, default=7, help="maximum direct files per directory")
    parser.add_argument("--ext", action="append", default=None, help="extension to include, e.g. py; repeatable")
    parser.add_argument("--exclude-dir", action="append", default=[], help="directory basename to skip; repeatable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    exts = normalize_exts(args.ext or ["py"])
    excluded = DEFAULT_EXCLUDED_DIRS | set(args.exclude_dir)
    violations = scan(root, exts, excluded, args.max_lines, args.max_dir_files)
    if violations:
        for violation in violations:
            print(violation.format(root))
        return 1
    print(f"quality limits ok: root={root} max_lines={args.max_lines} max_dir_files={args.max_dir_files} ext={','.join(sorted(exts))}")
    return 0


def normalize_exts(values: list[str]) -> set[str]:
    return {value[1:] if value.startswith(".") else value for value in values}


def scan(root: Path, exts: set[str], excluded_dirs: set[str], max_lines: int, max_dir_files: int) -> list[Violation]:
    violations: list[Violation] = []
    for directory, files in walk_source_dirs(root, exts, excluded_dirs):
        if len(files) > max_dir_files:
            violations.append(Violation("dir-fanout", directory, len(files), max_dir_files))
        for path in files:
            line_count = count_lines(path)
            if line_count > max_lines:
                violations.append(Violation("max-lines", path, line_count, max_lines))
    return sorted(violations, key=lambda item: (str(item.path), item.kind))


def walk_source_dirs(root: Path, exts: set[str], excluded_dirs: set[str]) -> list[tuple[Path, list[Path]]]:
    output: list[tuple[Path, list[Path]]] = []
    for directory_text, dirnames, filenames in os.walk(root):
        directory = Path(directory_text)
        dirnames[:] = [name for name in dirnames if name not in excluded_dirs]
        files = sorted(directory / name for name in filenames if suffix_matches(name, exts))
        if files:
            output.append((directory, files))
    return output


def suffix_matches(filename: str, exts: set[str]) -> bool:
    return any(filename.endswith(f".{ext}") for ext in exts)


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for _ in handle)


if __name__ == "__main__":
    raise SystemExit(main())
