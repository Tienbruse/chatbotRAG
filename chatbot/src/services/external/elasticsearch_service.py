from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from elasticsearch import AsyncElasticsearch
from src.exceptions.base import ServerError
from src.services.external import BaseRetriever
from src.settings import SETTINGS


class ElasticsearchService(BaseRetriever):
    def __init__(self, index_name: Optional[str] = None):
        self.host = SETTINGS.ELASTICSEARCH_HOST
        self.port = SETTINGS.ELASTICSEARCH_PORT
        self.username = SETTINGS.ELASTICSEARCH_USER
        self.password = SETTINGS.ELASTICSEARCH_PASSWORD

        if not index_name:
            self.index_name = SETTINGS.ELASTICSEARCH_INDEX
        else:
            self.index_name = index_name

        super().__init__()

    def _create_client(self) -> AsyncElasticsearch:
        return AsyncElasticsearch(
            [f"http://{self.host}:{self.port}"],
            basic_auth=(self.username, self.password),
            http_compress=True,
            verify_certs=False,
            request_timeout=120,
        )

    async def retrieve_documents(
        self,
        queries: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Retrieve documents from Elasticsearch

        Args:
            queries (Dict[str, Any]): query conditions
            index_name: elastic search index_name use to search
        Raises:
            ServerError: Latitude and longitude are required for geo search

        Returns:
            dict: Concatenated restaurant information
        """
        if not queries:
            return {}

        client = self._create_client()

        try:
            search_resp = await client.search(**queries)
        except Exception as exc:  # noqa: BLE001
            raise ServerError(f"Failed to retrieve documents: {exc}") from exc
        finally:
            await client.close()

        search_response = search_resp.body["hits"]

        return search_response

    async def msearch_documents(
        self,
        queries: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run multiple searches via Elasticsearch `_msearch`."""
        if not queries:
            return {"responses": []}

        searches: list[dict[str, Any]] = []
        for payload in queries:
            index_name = payload.get("index") or self.index_name
            searches.append({"index": index_name})
            searches.append(
                {
                    "query": payload.get("query"),
                    "size": payload.get("size"),
                    "sort": payload.get("sort"),
                    "_source": payload.get("_source", True),
                    "track_scores": payload.get("track_scores", False),
                }
            )

        client = self._create_client()
        try:
            resp = await client.msearch(searches=searches)
            return resp.body
        except Exception as exc:  # noqa: BLE001
            raise ServerError(f"Failed to msearch documents: {exc}") from exc
        finally:
            await client.close()

    async def mget_documents(
        self,
        ids: Sequence[str],
        *,
        source: bool | None = None,
    ) -> Dict[str, Any]:
        """Fetch multiple documents by `_id` via Elasticsearch `_mget`."""
        id_list = [doc_id for doc_id in ids if doc_id]
        if not id_list:
            return {"docs": []}

        client = self._create_client()
        try:
            resp = await client.mget(
                index=self.index_name,
                ids=id_list,
                source=source,
            )
            return resp.body
        except Exception as exc:  # noqa: BLE001
            raise ServerError(f"Failed to mget documents: {exc}") from exc
        finally:
            await client.close()

    async def search_lucene(
        self,
        query_string: str,
        *,
        fields: Sequence[str] | None = None,
        top_k: int = 5,
        default_operator: str = "AND",
    ) -> Dict[str, Any]:
        """Search using Lucene query syntax via `query_string` query."""
        if not query_string:
            return {}

        query = {
            "query_string": {
                "query": query_string,
                "default_operator": default_operator,
                **({"fields": list(fields)} if fields else {}),
            }
        }

        client = self._create_client()
        try:
            resp = await client.search(
                index=self.index_name,
                query=query,
                size=top_k,
                _source=True,
                track_scores=True,
            )
            return resp.body.get("hits", {})
        except Exception as exc:  # noqa: BLE001
            raise ServerError(f"Failed to lucene search documents: {exc}") from exc
        finally:
            await client.close()
