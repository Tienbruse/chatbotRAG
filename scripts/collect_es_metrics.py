#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
CHATBOT_SRC = REPO_ROOT / "chatbot"
if str(CHATBOT_SRC) not in sys.path:
    sys.path.append(str(CHATBOT_SRC))

from elasticsearch import AsyncElasticsearch
from src.services.query_creator import QueryCreator
from src.settings import SETTINGS


def _load_queries(input_path: Path) -> List[Dict[str, Any]]:
    raw_text = input_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            data = [data]
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw_text.splitlines() if line.strip()]

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(data):
        normalized.append(
            {
                "id": item.get("id") or f"q{idx + 1}",
                "user_input": item.get("user_input", ""),
                "entities": item.get("entities") or {},
                "top_k": item.get("top_k"),
            }
        )
    return normalized


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"http://{url}")
    return parsed.netloc.lower() or parsed.path.split("/")[0].lower()


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)

    sorted_values = sorted(values)
    rank = (p / 100) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    fraction = rank - low
    if high == low:
        return float(sorted_values[low])
    return float(sorted_values[low] * (1 - fraction) + sorted_values[high] * fraction)


def _summarize_times(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {
            "runs": 0,
            "min_ms": None,
            "max_ms": None,
            "mean_ms": None,
            "median_ms": None,
            "p95_ms": None,
        }

    return {
        "runs": len(values),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(mean(values), 3),
        "median_ms": round(median(values), 3),
        "p95_ms": round(_percentile(values, 95), 3),
    }


async def _collect_for_query(
    query_cfg: Dict[str, Any],
    query_creator: QueryCreator,
    client: AsyncElasticsearch,
    version: str,
    default_top_k: int,
    runs: int,
    warmup: int,
) -> Dict[str, Any]:
    entities = query_cfg["entities"]
    top_k = query_cfg.get("top_k") or default_top_k
    query_payload = query_creator.create_query(
        entities=entities,
        index_name=SETTINGS.ELASTICSEARCH_INDEX,
        top_k=top_k,
    )

    result: Dict[str, Any] = {
        "query_id": query_cfg["id"],
        "user_input": query_cfg["user_input"],
        "version": version,
        "top_k": top_k,
        "entities": entities,
        "es_query": query_payload["query"] if query_payload else None,
    }

    if not query_payload:
        result["metrics"] = {
            "total_hits": 0,
            "returned_hits": 0,
            "max_score": None,
            "hits_with_url": 0,
            "hits_with_rich_contact": 0,
            "domain_distribution": {},
        }
        result["timing"] = {
            "wall_ms": _summarize_times([]),
            "took_ms": _summarize_times([]),
        }
        result["top_results"] = []
        return result

    async def _run_once() -> tuple[Dict[str, Any], float]:
        start = time.perf_counter()
        resp = await client.search(**query_payload)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return resp.body, elapsed_ms

    for _ in range(max(0, warmup)):
        await _run_once()

    wall_times: List[float] = []
    took_times: List[float] = []
    body: Dict[str, Any] | None = None
    for _ in range(max(1, runs)):
        body, wall_ms = await _run_once()
        wall_times.append(wall_ms)
        took_value = body.get("took") if body else None
        if isinstance(took_value, (int, float)):
            took_times.append(float(took_value))

    hits_section = (body or {}).get("hits", {})
    hits = hits_section.get("hits", [])

    def _has_fields(hit: Dict[str, Any], fields: List[str]) -> bool:
        source = hit.get("_source", {})
        return all(bool(source.get(field)) for field in fields)

    urls = [hit.get("_source", {}).get("url") for hit in hits]
    domain_counter = Counter(filter(None, (_extract_domain(url) for url in urls)))

    result["metrics"] = {
        "total_hits": hits_section.get("total", {}).get("value", 0),
        "returned_hits": len(hits),
        "max_score": hits_section.get("max_score"),
        "hits_with_url": sum(1 for url in urls if url),
        "hits_with_rich_contact": sum(
            1 for hit in hits if _has_fields(hit, ["phone", "email"])
        ),
        "domain_distribution": dict(domain_counter.most_common(10)),
    }
    result["timing"] = {
        "wall_ms": _summarize_times(wall_times),
        "took_ms": _summarize_times(took_times),
    }
    result["top_results"] = [
        {
            "id": hit.get("_id"),
            "score": hit.get("_score"),
            "name": hit.get("_source", {}).get("name"),
            "url": hit.get("_source", {}).get("url"),
            "phone": hit.get("_source", {}).get("phone"),
        }
        for hit in hits[: min(5, len(hits))]
    ]

    return result


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Elasticsearch metrics for query versions."
    )
    parser.add_argument(
        "--version",
        default=SETTINGS.ES_QUERY_VERSION,
        help="Query version to test (default: value from settings).",
    )
    parser.add_argument(
        "--queries-file",
        default=str(REPO_ROOT / "docs" / "metrics" / "sample_queries.json"),
        help="Path to JSON/JSONL file describing test entities.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output filepath. Defaults to docs/metrics/{version}_<timestamp>.jsonl",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Default top_k used when queries do not specify it.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of measured runs per query (default: 3).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Number of warmup runs per query before measuring (default: 1).",
    )
    args = parser.parse_args()

    version = args.version.lower()
    queries_path = Path(args.queries_file)
    queries = _load_queries(queries_path)
    if not queries:
        raise SystemExit(f"No queries found in {queries_path}")

    output_dir = REPO_ROOT / "docs" / "metrics"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"{version}_{timestamp}.jsonl"

    query_creator = QueryCreator(query_version=version)

    results: List[Dict[str, Any]] = []
    async with AsyncElasticsearch(
        [f"http://{SETTINGS.ELASTICSEARCH_HOST}:{SETTINGS.ELASTICSEARCH_PORT}"],
        basic_auth=(SETTINGS.ELASTICSEARCH_USER, SETTINGS.ELASTICSEARCH_PASSWORD),
        http_compress=True,
        verify_certs=False,
        request_timeout=120,
    ) as client:
        for query_cfg in queries:
            metrics = await _collect_for_query(
                query_cfg=query_cfg,
                query_creator=query_creator,
                client=client,
                version=version,
                default_top_k=args.top_k,
                runs=args.runs,
                warmup=args.warmup,
            )
            results.append(metrics)
            mean_ms = metrics.get("timing", {}).get("wall_ms", {}).get("mean_ms")
            print(
                f"[{version}] {metrics['query_id']}: "
                f"{metrics['metrics']['returned_hits']} hits, "
                f"{metrics['metrics']['hits_with_url']} with URL, "
                f"mean {mean_ms} ms"
            )

    wall_means = [
        item.get("timing", {}).get("wall_ms", {}).get("mean_ms")
        for item in results
        if item.get("timing", {}).get("wall_ms", {}).get("mean_ms") is not None
    ]
    took_means = [
        item.get("timing", {}).get("took_ms", {}).get("mean_ms")
        for item in results
        if item.get("timing", {}).get("took_ms", {}).get("mean_ms") is not None
    ]

    aggregated = {
        "version": version,
        "total_queries": len(results),
        "average_hits_with_url": round(
            sum(item["metrics"]["hits_with_url"] for item in results) / len(results), 2
        ),
        "average_max_score": round(
            sum(
                item["metrics"]["max_score"] or 0.0
                for item in results
                if item["metrics"]["max_score"] is not None
            )
            / max(
                1,
                sum(1 for item in results if item["metrics"]["max_score"] is not None),
            ),
            4,
        ),
        "average_wall_mean_ms": round(sum(wall_means) / len(wall_means), 3)
        if wall_means
        else None,
        "average_took_mean_ms": round(sum(took_means) / len(took_means), 3)
        if took_means
        else None,
    }

    with output_path.open("w", encoding="utf-8") as file:
        for row in results:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        f"Metrics saved to {output_path} — "
        f"queries: {aggregated['total_queries']}, "
        f"avg hits with URL: {aggregated['average_hits_with_url']}, "
        f"avg wall mean: {aggregated['average_wall_mean_ms']} ms, "
        f"avg ES took: {aggregated['average_took_mean_ms']} ms, "
        f"avg max score: {aggregated['average_max_score']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
