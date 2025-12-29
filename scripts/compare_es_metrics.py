#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict


def _load_jsonl(path: Path) -> Dict[str, Any]:
    rows: Dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        query_id = row.get("query_id")
        if not query_id:
            raise ValueError(f"Missing query_id in {path}")
        rows[str(query_id)] = row
    return rows


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100


def _get_metric(row: Dict[str, Any], key: str) -> Any:
    return (row.get("metrics") or {}).get(key)


def _get_timing_mean(row: Dict[str, Any], key: str) -> float | None:
    value = (row.get("timing") or {}).get(key, {}).get("mean_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _avg(values: list[float | None]) -> float | None:
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    return round(mean(cleaned), 3)


def _fmt_num(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _build_report(
    baseline_path: Path,
    candidate_path: Path,
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
) -> str:
    common_ids = sorted(set(baseline).intersection(candidate))
    missing_baseline = sorted(set(candidate).difference(baseline))
    missing_candidate = sorted(set(baseline).difference(candidate))

    rows = []
    for query_id in common_ids:
        b = baseline[query_id]
        c = candidate[query_id]

        b_wall = _get_timing_mean(b, "wall_ms")
        c_wall = _get_timing_mean(c, "wall_ms")
        b_took = _get_timing_mean(b, "took_ms")
        c_took = _get_timing_mean(c, "took_ms")

        rows.append(
            {
                "query_id": query_id,
                "b_wall": b_wall,
                "c_wall": c_wall,
                "wall_pct": _pct_change(c_wall, b_wall),
                "b_took": b_took,
                "c_took": c_took,
                "took_pct": _pct_change(c_took, b_took),
                "b_hits_url": _get_metric(b, "hits_with_url"),
                "c_hits_url": _get_metric(c, "hits_with_url"),
                "b_hits_contact": _get_metric(b, "hits_with_rich_contact"),
                "c_hits_contact": _get_metric(c, "hits_with_rich_contact"),
                "b_max_score": _get_metric(b, "max_score"),
                "c_max_score": _get_metric(c, "max_score"),
            }
        )

    summary = {
        "avg_wall_baseline_ms": _avg([row["b_wall"] for row in rows]),
        "avg_wall_candidate_ms": _avg([row["c_wall"] for row in rows]),
        "avg_took_baseline_ms": _avg([row["b_took"] for row in rows]),
        "avg_took_candidate_ms": _avg([row["c_took"] for row in rows]),
        "avg_hits_with_url_baseline": _avg(
            [
                float(row["b_hits_url"])
                for row in rows
                if isinstance(row["b_hits_url"], (int, float))
            ]
        ),
        "avg_hits_with_url_candidate": _avg(
            [
                float(row["c_hits_url"])
                for row in rows
                if isinstance(row["c_hits_url"], (int, float))
            ]
        ),
        "avg_hits_with_rich_contact_baseline": _avg(
            [
                float(row["b_hits_contact"])
                for row in rows
                if isinstance(row["b_hits_contact"], (int, float))
            ]
        ),
        "avg_hits_with_rich_contact_candidate": _avg(
            [
                float(row["c_hits_contact"])
                for row in rows
                if isinstance(row["c_hits_contact"], (int, float))
            ]
        ),
    }

    report_lines = [
        "# Elasticsearch metrics comparison",
        "",
        f"- Baseline: `{baseline_path}`",
        f"- Candidate: `{candidate_path}`",
        "",
        "## Summary (average across common queries)",
        "",
        f"- wall mean (ms): {_fmt_num(summary['avg_wall_baseline_ms'])} → {_fmt_num(summary['avg_wall_candidate_ms'])} ({_fmt_pct(_pct_change(summary['avg_wall_candidate_ms'], summary['avg_wall_baseline_ms']))})",
        f"- ES took mean (ms): {_fmt_num(summary['avg_took_baseline_ms'])} → {_fmt_num(summary['avg_took_candidate_ms'])} ({_fmt_pct(_pct_change(summary['avg_took_candidate_ms'], summary['avg_took_baseline_ms']))})",
        f"- hits with URL: {_fmt_num(summary['avg_hits_with_url_baseline'], digits=2)} → {_fmt_num(summary['avg_hits_with_url_candidate'], digits=2)}",
        f"- hits with rich contact: {_fmt_num(summary['avg_hits_with_rich_contact_baseline'], digits=2)} → {_fmt_num(summary['avg_hits_with_rich_contact_candidate'], digits=2)}",
        "",
        "## Per-query",
        "",
        "| query_id | wall mean (ms) | Δ wall | ES took mean (ms) | Δ took | hits_with_url | hits_with_rich_contact | max_score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in rows:
        report_lines.append(
            "| "
            + " | ".join(
                [
                    row["query_id"],
                    f"{_fmt_num(row['b_wall'])} → {_fmt_num(row['c_wall'])}",
                    _fmt_pct(row["wall_pct"]),
                    f"{_fmt_num(row['b_took'])} → {_fmt_num(row['c_took'])}",
                    _fmt_pct(row["took_pct"]),
                    f"{_fmt_num(row['b_hits_url'], digits=0)} → {_fmt_num(row['c_hits_url'], digits=0)}",
                    f"{_fmt_num(row['b_hits_contact'], digits=0)} → {_fmt_num(row['c_hits_contact'], digits=0)}",
                    f"{_fmt_num(row['b_max_score'])} → {_fmt_num(row['c_max_score'])}",
                ]
            )
            + " |"
        )

    if missing_baseline:
        report_lines.extend(
            [
                "",
                "## Warnings",
                "",
                f"- Present only in candidate: {', '.join(missing_baseline)}",
            ]
        )
    if missing_candidate:
        report_lines.extend(
            [
                "",
                "## Warnings",
                "",
                f"- Present only in baseline: {', '.join(missing_candidate)}",
            ]
        )

    return "\n".join(report_lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two ES metrics JSONL files.")
    parser.add_argument("--baseline", required=True, help="Path to baseline JSONL file.")
    parser.add_argument("--candidate", required=True, help="Path to candidate JSONL file.")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown file. If omitted, prints to stdout.",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    baseline = _load_jsonl(baseline_path)
    candidate = _load_jsonl(candidate_path)

    report = _build_report(baseline_path, candidate_path, baseline, candidate)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report saved to {output_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()

