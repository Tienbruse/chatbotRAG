from __future__ import annotations

from typing import Any, List, Optional

from pydantic_settings import BaseSettings


class ExternalConfig(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8910
    ROOT_PATH: str = "/michelin-recommendation/api"
    API_V1_STR: str = "/v1"
    CORS_ORIGINS: Any = ["*"]
    CORS_ORIGINS_REGEX: Optional[str] = None
    CORS_HEADERS: Any = ["*"]

    # Michelin configuration
    LLM_TYPE: str = "OpenAPI"
    RETRIEVER_TYPE: str = "Elasticsearch"

    MODEL_SOURCE: str = "openai"

    # OpenAI configuration
    OPENAI_API_KEY: str = ""
    MODEL_NAME: str = "gpt-4.1-nano"
    MODEL_TEMPERATURE: float = 0.9

    # DeepSeek configuration
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL_NAME: str = "deepseek-chat"
    DEEPSEEK_MODEL_TEMPERATURE: float = 0.9

    # Elasticsearch configuration
    ELASTICSEARCH_HOST: str = "localhost"
    ELASTICSEARCH_PORT: int = 9200
    ELASTICSEARCH_USER: str = "admin"
    ELASTICSEARCH_PASSWORD: str = "admin"
    ELASTICSEARCH_INDEX: str = "company-data-20240329"
    ES_QUERY_VERSION: str = "v1"
    ES_URL_BLACKLIST: List[str] = ["doanhnghiepmoi.vn"]
    ES_URL_PREFERRED_DOMAINS: List[str] = [
        "gov.vn",
        "org.vn",
        "fairs.vn",
    ]
    ES_URL_PENALTY_DOMAINS: List[str] = []
    ES_URL_PENALTY_WEIGHT: float = -2.0
    ES_TRACK_SCORES: bool = False


SETTINGS = ExternalConfig()  # pyright: ignore
APP_CONFIGS: dict[str, Any] = {
    "title": "Company Recommendation",
    "root_path": SETTINGS.ROOT_PATH,
}
