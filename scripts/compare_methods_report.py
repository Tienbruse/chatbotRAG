#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _extract(row: Dict[str, Any]) -> Dict[str, Any]:
    methods = row.get("methods") or {}

    def pick(method_key: str) -> Dict[str, Any]:
        m = methods.get(method_key) or {}
        wall = m.get("wall_ms") or {}
        qps = m.get("qps") or {}
        took = m.get("es_took_ms") or {}
        return {
            "requests_run": m.get("requests_per_run"),
            "ops_run": m.get("ops_per_run"),
            "wall_mean": wall.get("mean_ms"),
            "qps_mean": qps.get("mean"),
            "took_mean": took.get("mean"),
        }

    return {key: pick(key) for key in methods}


def _pct_change(new: float | None, old: float | None) -> str:
    if new is None or old is None or old == 0:
        return "—"
    return f"{(new - old) / old * 100:+.2f}%"


def _build_table(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
) -> str:
    header = [
        "| Phương pháp | Requests/run | Độ trễ TB (ms) | Δ TB | QPS TB | Δ QPS | ES took TB (ms) | Δ ES |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    lines = []
    all_keys = sorted(set(baseline) | set(candidate))
    for method in all_keys:
        b = baseline.get(method) or {}
        c = candidate.get(method) or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    method,
                    f"{_fmt(b.get('requests_run'), digits=0)} → {_fmt(c.get('requests_run'), digits=0)}",
                    f"{_fmt(b.get('wall_mean'))} → {_fmt(c.get('wall_mean'))}",
                    _pct_change(c.get("wall_mean"), b.get("wall_mean")),
                    f"{_fmt(b.get('qps_mean'))} → {_fmt(c.get('qps_mean'))}",
                    _pct_change(c.get("qps_mean"), b.get("qps_mean")),
                    f"{_fmt(b.get('took_mean'))} → {_fmt(c.get('took_mean'))}",
                    _pct_change(c.get("took_mean"), b.get("took_mean")),
                ]
            )
            + " |"
        )
    return "\n".join(header + lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="So sánh hai file benchmark methods_* (search/msearch/get/mget/Lucene) với bảng tiếng Việt."
    )
    parser.add_argument("--baseline", required=True, help="File JSON benchmark gốc.")
    parser.add_argument("--candidate", required=True, help="File JSON benchmark mới.")
    parser.add_argument(
        "--output",
        default=None,
        help="File markdown đầu ra (mặc định in stdout).",
    )
    args = parser.parse_args()

    base_path = Path(args.baseline)
    cand_path = Path(args.candidate)
    base_row = _load(base_path)
    cand_row = _load(cand_path)

    baseline = _extract(base_row)
    candidate = _extract(cand_row)

    table = _build_table(baseline, candidate)

    header = [
        f"# So sánh benchmark phương pháp Elasticsearch",
        "",
        f"- Gốc: `{base_path}`",
        f"- Mới: `{cand_path}`",
        "",
        table,
    ]
    content = "\n".join(header)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"Đã lưu báo cáo: {out_path}")
    else:
        print(content)


if __name__ == "__main__":
    main()
