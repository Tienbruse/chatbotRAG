#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import List


def parse_md_table(lines: List[str]) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Bỏ dòng separator (---)
        if set(line.replace("|", "").replace(":", "").replace("-", "").strip()) == set():
            continue
        parts = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(parts)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Chuyển bảng Markdown thành CSV (dễ import vào Google Sheets).")
    parser.add_argument("--input", required=True, help="Đường dẫn file .md chứa bảng.")
    parser.add_argument("--output", required=True, help="Đường dẫn file .csv sẽ xuất.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = input_path.read_text(encoding="utf-8").splitlines()
    rows = parse_md_table(lines)
    if not rows:
        raise SystemExit(f"Không tìm thấy bảng trong {input_path}")

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)

    print(f"Đã xuất CSV: {output_path}")


if __name__ == "__main__":
    main()
