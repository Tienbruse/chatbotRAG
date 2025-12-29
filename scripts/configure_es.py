#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
CHATBOT_SRC = REPO_ROOT / "chatbot"
import sys

if str(CHATBOT_SRC) not in sys.path:
    sys.path.append(str(CHATBOT_SRC))

from elasticsearch import AsyncElasticsearch, BadRequestError
from src.settings import SETTINGS


async def apply_settings(args: argparse.Namespace) -> Dict[str, Any]:
    index_settings: Dict[str, Any] = {
        "refresh_interval": args.refresh_interval,
        "translog.flush_threshold_size": args.flush_threshold,
       
        "requests.cache.enable": args.request_cache,
    }

    index_body = {"index": index_settings}

    cluster_body: Dict[str, Any] = {}
    if args.index_buffer:
        cluster_body["indices.memory.index_buffer_size"] = args.index_buffer
    if args.query_cache_size:
        cluster_body["indices.queries.cache.size"] = args.query_cache_size

    async with AsyncElasticsearch(
        [f"http://{SETTINGS.ELASTICSEARCH_HOST}:{SETTINGS.ELASTICSEARCH_PORT}"],
        basic_auth=(SETTINGS.ELASTICSEARCH_USER, SETTINGS.ELASTICSEARCH_PASSWORD),
        http_compress=True,
        verify_certs=False,
        request_timeout=120,
    ) as client:
        responses: Dict[str, Any] = {}
        # Apply index-level settings
        responses["index_settings"] = await client.indices.put_settings(
            index=args.index,
            settings=index_body,
        )
        # Apply cluster-level settings
        if cluster_body:
            try:
                responses["cluster_settings"] = await client.cluster.put_settings(
                    transient=cluster_body
                )
            except BadRequestError as exc:
                if "index_buffer_size" in cluster_body:
                    trimmed = {
                        k: v
                        for k, v in cluster_body.items()
                        if "index_buffer_size" not in k
                    }
                    if trimmed:
                        responses["cluster_settings"] = await client.cluster.put_settings(
                            transient=trimmed
                        )
                    else:
                        responses["cluster_settings"] = {"warning": str(exc)}
                else:
                    responses["cluster_settings"] = {"warning": str(exc)}
        else:
            responses["cluster_settings"] = {"skipped": True}
        # Fetch current settings to confirm
        responses["current_index_settings"] = await client.indices.get_settings(
            index=args.index
        )
        responses["current_cluster_settings"] = await client.cluster.get_settings()
        return responses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure Elasticsearch performance-related settings (refresh, flush, cache)."
    )
    parser.add_argument(
        "--index",
        default=SETTINGS.ELASTICSEARCH_INDEX,
        help="Index name to update (default from settings).",
    )
    parser.add_argument(
        "--refresh-interval",
        default="30s",
        help="Index refresh interval (e.g., 1s, 30s, -1).",
    )
    parser.add_argument(
        "--flush-threshold",
        default="1gb",
        help="translog.flush_threshold_size (e.g., 512mb, 1gb).",
    )
    parser.add_argument(
        "--index-buffer",
        default=None,
        help="indices.memory.index_buffer_size for the cluster (percent or size). Optional because not dynamic on all versions.",
    )
    parser.add_argument(
        "--query-cache-size",
        default="20%",
        help="indices.queries.cache.size for the cluster (percent or size).",
    )
    parser.add_argument(
        "--request-cache",
        action="store_true",
        help="Enable request cache at index level.",
    )
    parser.add_argument(
        "--no-request-cache",
        dest="request_cache",
        action="store_false",
        help="Disable request cache at index level.",
    )
    parser.set_defaults(request_cache=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    responses = await apply_settings(args)
    print("Applied settings:")
    print(responses["index_settings"])
    print(responses["cluster_settings"])
    print("Current index settings:")
    print(responses["current_index_settings"])
    print("Current cluster settings:")
    print(responses["current_cluster_settings"])


if __name__ == "__main__":
    asyncio.run(main())
