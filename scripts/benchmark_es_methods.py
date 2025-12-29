#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Sequence
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


def _chunked(values: Sequence[Any], chunk_size: int) -> Iterable[Sequence[Any]]:
    if chunk_size <= 0:
        yield values
        return
    for idx in range(0, len(values), chunk_size):
        yield values[idx : idx + chunk_size]


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
            "p99_ms": None,
        }

    return {
        "runs": len(values),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(mean(values), 3),
        "median_ms": round(median(values), 3),
        "p95_ms": round(_percentile(values, 95), 3),
        "p99_ms": round(_percentile(values, 99), 3),
    }


def _summarize_numbers(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(mean(values), 3),
        "median": round(median(values), 3),
        "p95": round(_percentile(values, 95), 3),
        "p99": round(_percentile(values, 99), 3),
    }


_LUCENE_SPECIAL_CHARS = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/])')


def _escape_lucene(text: str) -> str:
    if not text:
        return ""
    return _LUCENE_SPECIAL_CHARS.sub(r"\\\1", text)


def _phrase(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    escaped = _escape_lucene(cleaned)
    if re.search(r"\s", escaped):
        return f"\"{escaped}\""
    return escaped


def _build_lucene_query_payload(
    *,
    query_cfg: Dict[str, Any],
    index_name: str,
    default_top_k: int,
    track_scores: bool,
) -> Dict[str, Any]:
    entities: Dict[str, Any] = query_cfg.get("entities") or {}
    top_k = query_cfg.get("top_k") or default_top_k

    root_terms: List[str] = []
    company_name = (entities.get("company_name") or "").strip()
    business_field = (entities.get("business_field") or "").strip()
    address = (entities.get("address") or "").strip()

    if company_name:
        root_terms.append(_phrase(company_name))
    if business_field:
        root_terms.append(_phrase(business_field))
    if address:
        root_terms.append(_phrase(address))

    fallback_text = (query_cfg.get("user_input") or "").strip()
    query_text = " ".join(root_terms) or _escape_lucene(fallback_text) or "*"

    should_clauses: List[Dict[str, Any]] = [
        {
            "query_string": {
                "query": query_text,
                "fields": [
                    "name^4",
                    "introduction^2",
                    "address^2",
                    "tax_code^5",
                    "email^2",
                    "phone^2",
                    "url^1",
                ],
                "default_operator": "AND",
            }
        }
    ]

    product_names = entities.get("product_names")
    product_terms: List[str] = []
    if isinstance(product_names, str):
        product_terms = [name.strip() for name in product_names.split(",") if name.strip()]

    nested_text_parts = product_terms or ([business_field] if business_field else [])
    if nested_text_parts:
        nested_text = " ".join(_phrase(part) for part in nested_text_parts if part)
        if nested_text:
            should_clauses.append(
                {
                    "nested": {
                        "path": "products",
                        "score_mode": "avg",
                        "query": {
                            "query_string": {
                                "query": nested_text,
                                "fields": [
                                    "products.product_name^2",
                                    "products.product_description",
                                ],
                                "default_operator": "AND",
                            }
                        },
                    }
                }
            )

    filter_clauses: List[Dict[str, Any]] = []
    employees = entities.get("num_employees")
    operator = entities.get("num_employees_operator")
    if employees is not None and operator in {"gte", "lte"}:
        range_condition: Dict[str, Any] = {}
        if operator == "gte":
            range_condition["gte"] = employees
        else:
            range_condition["lte"] = employees
        filter_clauses.append({"range": {"employees": range_condition}})

    must_not_clauses: List[Dict[str, Any]] = []
    for domain in SETTINGS.ES_URL_BLACKLIST or []:
        domain_value = (domain or "").strip()
        if not domain_value:
            continue
        must_not_clauses.append(
            {"wildcard": {"url": {"value": f"*{domain_value}*"}}}
        )

    bool_query: Dict[str, Any] = {
        "should": should_clauses,
        "minimum_should_match": 1,
    }
    if filter_clauses:
        bool_query["filter"] = filter_clauses
    if must_not_clauses:
        bool_query["must_not"] = must_not_clauses

    return {
        "index": index_name,
        "query": {"bool": bool_query},
        "size": top_k,
        "_source": True,
        "track_scores": track_scores,
    }


def _extract_hit_metrics(hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    def _has_fields(hit: Dict[str, Any], fields: List[str]) -> bool:
        source = hit.get("_source", {})
        return all(bool(source.get(field)) for field in fields)

    urls = [hit.get("_source", {}).get("url") for hit in hits]
    domain_counter = Counter(filter(None, (_extract_domain(url) for url in urls)))

    return {
        "returned_hits": len(hits),
        "hits_with_url": sum(1 for url in urls if url),
        "hits_with_rich_contact": sum(1 for hit in hits if _has_fields(hit, ["phone", "email"])),
        "domain_distribution": dict(domain_counter.most_common(10)),
    }


async def _run_search_requests(
    client: AsyncElasticsearch,
    payloads: Sequence[Dict[str, Any]],
    *,
    batch_size: int,
) -> tuple[List[Dict[str, Any]], List[float]]:
    bodies: List[Dict[str, Any]] = []
    took_values: List[float] = []
    for batch in _chunked(payloads, batch_size):
        for payload in batch:
            resp = await client.search(**payload)
            body: Dict[str, Any] = resp.body
            bodies.append(body)
            took = body.get("took")
            if isinstance(took, (int, float)):
                took_values.append(float(took))
    return bodies, took_values


async def _run_msearch_requests(
    client: AsyncElasticsearch,
    payloads: Sequence[Dict[str, Any]],
    *,
    batch_size: int,
) -> tuple[List[Dict[str, Any]], List[float]]:
    bodies: List[Dict[str, Any]] = []
    took_values: List[float] = []

    for batch in _chunked(payloads, batch_size):
        searches: List[Dict[str, Any]] = []
        for payload in batch:
            searches.append({"index": payload["index"]})
            searches.append(
                {
                    "query": payload.get("query"),
                    "size": payload.get("size"),
                    "sort": payload.get("sort"),
                    "_source": payload.get("_source", True),
                    "track_scores": payload.get("track_scores", False),
                }
            )

        resp = await client.msearch(searches=searches)
        response_items = resp.body.get("responses", [])
        for item in response_items:
            bodies.append(item)
            took = item.get("took")
            if isinstance(took, (int, float)):
                took_values.append(float(took))

    return bodies, took_values


async def _benchmark_queries(
    *,
    client: AsyncElasticsearch,
    method_name: str,
    payloads: Sequence[Dict[str, Any]],
    runs: int,
    warmup: int,
    batch_size: int,
    use_msearch: bool,
) -> Dict[str, Any]:
    runner = _run_msearch_requests if use_msearch else _run_search_requests

    async def _run_once() -> tuple[List[Dict[str, Any]], List[float], float]:
        start = time.perf_counter()
        bodies, took_values = await runner(client, payloads, batch_size=batch_size)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return bodies, took_values, elapsed_ms

    for _ in range(max(0, warmup)):
        await _run_once()

    wall_times: List[float] = []
    took_times: List[float] = []
    sample_bodies: List[Dict[str, Any]] = []

    for idx in range(max(1, runs)):
        bodies, took_values, wall_ms = await _run_once()
        wall_times.append(wall_ms)
        took_times.extend(took_values)
        if idx == runs - 1:
            sample_bodies = bodies

    total_ops = len(payloads)
    qps_values = [
        (total_ops / (wall_ms / 1000)) if wall_ms > 0 else 0.0 for wall_ms in wall_times
    ]

    requests_per_run = (
        len(list(_chunked(payloads, batch_size))) if use_msearch else len(payloads)
    )

    return {
        "method": method_name,
        "ops_per_run": total_ops,
        "requests_per_run": requests_per_run,
        "wall_ms": _summarize_times(wall_times),
        "qps": _summarize_numbers(qps_values),
        "es_took_ms": _summarize_numbers(took_times),
    }


async def _sample_doc_ids(
    client: AsyncElasticsearch,
    *,
    index_name: str,
    count: int,
) -> List[str]:
    if count <= 0:
        return []
    resp = await client.search(
        index=index_name,
        query={"match_all": {}},
        size=count,
        sort=["_doc"],
        _source=False,
    )
    hits = (resp.body.get("hits") or {}).get("hits", [])
    return [hit.get("_id") for hit in hits if hit.get("_id")]


async def _benchmark_get(
    *,
    client: AsyncElasticsearch,
    index_name: str,
    ids: Sequence[str],
    runs: int,
    warmup: int,
    batch_size: int,
    use_mget: bool,
) -> Dict[str, Any]:
    ids_list = [doc_id for doc_id in ids if doc_id]
    if not ids_list:
        return {
            "method": "mget" if use_mget else "get",
            "ops_per_run": 0,
            "requests_per_run": 0,
            "wall_ms": _summarize_times([]),
            "qps": _summarize_numbers([]),
            "found_docs": 0,
        }

    async def _run_once() -> tuple[int, float]:
        start = time.perf_counter()
        found_count = 0

        for batch in _chunked(ids_list, batch_size):
            if use_mget:
                resp = await client.mget(index=index_name, ids=list(batch))
                docs = resp.body.get("docs", [])
                found_count += sum(1 for doc in docs if doc.get("found"))
            else:
                for doc_id in batch:
                    resp = await client.get(index=index_name, id=doc_id)
                    if resp.body.get("found"):
                        found_count += 1

        elapsed_ms = (time.perf_counter() - start) * 1000
        return found_count, elapsed_ms

    for _ in range(max(0, warmup)):
        await _run_once()

    wall_times: List[float] = []
    found_docs = 0
    for idx in range(max(1, runs)):
        found, wall_ms = await _run_once()
        wall_times.append(wall_ms)
        if idx == runs - 1:
            found_docs = found

    total_ops = len(ids_list)
    qps_values = [
        (total_ops / (wall_ms / 1000)) if wall_ms > 0 else 0.0 for wall_ms in wall_times
    ]

    requests_per_run = len(list(_chunked(ids_list, batch_size))) if use_mget else total_ops

    return {
        "method": "mget" if use_mget else "get",
        "ops_per_run": total_ops,
        "requests_per_run": requests_per_run,
        "wall_ms": _summarize_times(wall_times),
        "qps": _summarize_numbers(qps_values),
        "found_docs": found_docs,
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    meta = report.get("meta") or {}
    methods: Dict[str, Any] = report.get("methods") or {}

    header_lines = [
        "# Elasticsearch benchmark (search / msearch / get / mget)",
        "",
        f"- Timestamp (UTC): `{meta.get('timestamp_utc')}`",
        f"- Index: `{meta.get('index')}`",
        f"- Query version (DSL): `{meta.get('dsl_version')}`",
        f"- Runs: `{meta.get('runs')}`, warmup: `{meta.get('warmup')}`",
        f"- search batch size: `{meta.get('search_batch_size')}`",
        f"- msearch batch size: `{meta.get('msearch_batch_size')}`",
        f"- mget batch size: `{meta.get('mget_batch_size')}`",
        f"- mget docs: `{meta.get('mget_docs')}`",
        "",
        "## Summary",
        "",
        "| method | ops/run | requests/run | wall mean (ms) | QPS mean | ES took mean (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    def _fmt(value: Any, digits: int = 3) -> str:
        if value is None:
            return "—"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    for method_key in [
        "search_dsl",
        "msearch_dsl",
        "get",
        "mget",
    ]:
        row = methods.get(method_key)
        if not row:
            continue
        wall = row.get("wall_ms") or {}
        qps = row.get("qps") or {}
        took = row.get("es_took_ms") or {}
        header_lines.append(
            "| "
            + " | ".join(
                [
                    method_key,
                    _fmt(row.get("ops_per_run"), digits=0),
                    _fmt(row.get("requests_per_run"), digits=0),
                    _fmt(wall.get("mean_ms")),
                    _fmt(qps.get("mean")),
                    _fmt(took.get("mean")),
                ]
            )
            + " |"
        )

    return "\n".join(header_lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Elasticsearch methods: search vs msearch, get vs mget, and Lucene query_string."
    )
    parser.add_argument(
        "--dsl-version",
        default=SETTINGS.ES_QUERY_VERSION,
        help="QueryCreator version for DSL benchmark (default: from settings).",
    )
    parser.add_argument(
        "--index",
        default=SETTINGS.ELASTICSEARCH_INDEX,
        help="Elasticsearch index name (default: from settings).",
    )
    parser.add_argument(
        "--queries-file",
        default=str(REPO_ROOT / "docs" / "metrics" / "sample_queries.json"),
        help="Path to JSON/JSONL describing test queries/entities.",
    )
    parser.add_argument("--runs", type=int, default=10, help="Measured runs per method.")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup runs per method.")
    parser.add_argument(
        "--search-batch-size",
        type=int,
        default=0,
        help="Batch size cho search/msearch (0 = gộp toàn bộ).",
    )
    parser.add_argument(
        "--msearch-batch-size",
        type=int,
        default=0,
        help="Batch size riêng cho msearch (0 = gộp toàn bộ).",
    )
    parser.add_argument(
        "--mget-batch-size",
        type=int,
        default=10,
        help="Batch size cho mget/get (0 = gộp toàn bộ).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Default top_k used when queries do not specify it.",
    )
    parser.add_argument(
        "--mget-docs",
        type=int,
        default=50,
        help="Number of documents to sample for get/mget benchmark.",
    )
    parser.add_argument(
        "--ids-file",
        default=None,
        help="File chứa danh sách _id (JSON list). Nếu tồn tại sẽ dùng lại, nếu chưa sẽ sample và lưu.",
    )
    parser.add_argument(
        "--prewarm",
        action="store_true",
        default=True,
        help="Chạy pre-warm cache cho search/msearch/mget trước khi đo.",
    )
    parser.add_argument(
        "--no-prewarm",
        action="store_false",
        dest="prewarm",
        help="Bỏ qua pre-warm cache.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: docs/metrics/methods_<timestamp>.json).",
    )
    parser.add_argument(
        "--markdown",
        default=None,
        help="Optional Markdown report path (default: docs/metrics/methods_<timestamp>.md).",
    )
    args = parser.parse_args()

    queries_path = Path(args.queries_file)
    query_cfgs = _load_queries(queries_path)
    if not query_cfgs:
        raise SystemExit(f"No queries found in {queries_path}")

    dsl_version = str(args.dsl_version).lower()
    query_creator = QueryCreator(query_version=dsl_version)

    dsl_payloads: List[Dict[str, Any]] = []
    skipped_dsl: List[str] = []

    for cfg in query_cfgs:
        payload = query_creator.create_query(
            entities=cfg.get("entities") or {},
            index_name=args.index,
            top_k=cfg.get("top_k") or args.top_k,
        )
        if payload:
            dsl_payloads.append(payload)
        else:
            skipped_dsl.append(cfg.get("id") or "?")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = REPO_ROOT / "docs" / "metrics"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output) if args.output else output_dir / f"methods_{timestamp}.json"
    markdown_path = (
        Path(args.markdown) if args.markdown else output_dir / f"methods_{timestamp}.md"
    )

    report: Dict[str, Any] = {
        "meta": {
            "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "index": args.index,
            "dsl_version": dsl_version,
            "runs": args.runs,
            "warmup": args.warmup,
            "search_batch_size": args.search_batch_size,
            "msearch_batch_size": args.msearch_batch_size,
            "mget_batch_size": args.mget_batch_size,
            "mget_docs": args.mget_docs,
            "skipped_dsl_queries": skipped_dsl,
        },
        "methods": {},
    }

    async with AsyncElasticsearch(
        [f"http://{SETTINGS.ELASTICSEARCH_HOST}:{SETTINGS.ELASTICSEARCH_PORT}"],
        basic_auth=(SETTINGS.ELASTICSEARCH_USER, SETTINGS.ELASTICSEARCH_PASSWORD),
        http_compress=True,
        verify_certs=False,
        request_timeout=120,
    ) as client:
        # Pre-warm cache nếu được bật
        if args.prewarm and dsl_payloads:
            await _run_msearch_requests(
                client, dsl_payloads, batch_size=args.msearch_batch_size or args.search_batch_size
            )

        if dsl_payloads:
            report["methods"]["search_dsl"] = await _benchmark_queries(
                client=client,
                method_name="search_dsl",
                payloads=dsl_payloads,
                runs=args.runs,
                warmup=args.warmup,
                batch_size=args.search_batch_size,
                use_msearch=False,
            )
            report["methods"]["msearch_dsl"] = await _benchmark_queries(
                client=client,
                method_name="msearch_dsl",
                payloads=dsl_payloads,
                runs=args.runs,
                warmup=args.warmup,
                batch_size=args.msearch_batch_size or args.search_batch_size,
                use_msearch=True,
            )

        ids: List[str] = []
        ids_path: Path | None = None
        if args.ids_file:
            ids_path = Path(args.ids_file)
            if ids_path.exists():
                try:
                    loaded_ids = json.loads(ids_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_ids, list):
                        ids = [str(item) for item in loaded_ids if item]
                except json.JSONDecodeError:
                    ids = []

        if not ids:
            ids = await _sample_doc_ids(
                client,
                index_name=args.index,
                count=args.mget_docs,
            )
            if ids_path:
                ids_path.parent.mkdir(parents=True, exist_ok=True)
                ids_path.write_text(
                    json.dumps(ids, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        # Pre-warm get/mget nếu cần
        if args.prewarm and ids:
            await _benchmark_get(
                client=client,
                index_name=args.index,
                ids=ids,
                runs=1,
                warmup=0,
                batch_size=args.mget_batch_size or args.search_batch_size,
                use_mget=True,
            )

        report["methods"]["get"] = await _benchmark_get(
            client=client,
            index_name=args.index,
            ids=ids,
            runs=args.runs,
            warmup=args.warmup,
            batch_size=args.mget_batch_size,
            use_mget=False,
        )
        report["methods"]["mget"] = await _benchmark_get(
            client=client,
            index_name=args.index,
            ids=ids,
            runs=args.runs,
            warmup=args.warmup,
            batch_size=args.mget_batch_size or args.search_batch_size,
            use_mget=True,
        )

    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")

    print(f"JSON saved: {output_path}")
    print(f"Markdown saved: {markdown_path}")


if __name__ == "__main__":
    asyncio.run(main())
