from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from src.settings import SETTINGS
from src.utils.logger import logger


class QueryCreator:
    def __init__(self, query_version: Optional[str] = None):
        self.query_version = (query_version or SETTINGS.ES_QUERY_VERSION).lower()
        self.url_blacklist = [domain.lower() for domain in SETTINGS.ES_URL_BLACKLIST or []]
        self.preferred_domains = SETTINGS.ES_URL_PREFERRED_DOMAINS or []
        self.penalty_domains = [domain.lower() for domain in SETTINGS.ES_URL_PENALTY_DOMAINS or []]
        self.penalty_weight = SETTINGS.ES_URL_PENALTY_WEIGHT
        self.track_scores = SETTINGS.ES_TRACK_SCORES or self.query_version == "v2"
        self._use_enhanced_should = self.query_version == "v2"

    def create_query(
        self,
        entities: Dict[str, Any],
        index_name: str,
        top_k: int,
    ) -> Optional[Dict[str, Any]]:
        """Create query in Elasticsearch."""

        must_query: List[Dict[str, Any]] = []
        should_query: List[Dict[str, Any]] = []
        must_not_query: List[Dict[str, Any]] = self._build_url_blacklist_filters()
        filter_query: List[Dict[str, Any]] = []

        self._append_company_name_query(entities, must_query)
        self._append_business_field_query(entities, should_query)
        self._append_address_query(entities, must_query)
        self._append_num_employees_query(entities, must_query)
        self._append_product_names_query(entities, should_query, filter_query)

        if (
            not must_query
            and not should_query
            and not must_not_query
            and not filter_query
        ):
            return None

        bool_query: Dict[str, Any] = {
            "filter": filter_query,
            "must": must_query,
            "should": should_query,
            "must_not": must_not_query,
        }

        if self._use_enhanced_should and should_query:
            bool_query["minimum_should_match"] = 1

        query = {"bool": bool_query}

        info_boost_functions = self._build_information_score_functions()
        if self.query_version == "v2":
            info_boost_functions.extend(self._build_domain_boost_functions())
            info_boost_functions.extend(self._build_contact_density_functions())
            info_boost_functions.extend(self._build_domain_penalty_functions())

        function_score_query = {
            "function_score": {
                "query": query,
                "functions": info_boost_functions,
                "boost_mode": "sum",
                "score_mode": "sum",
            }
        }
        sort: List[Any] = []

        logger.info(
            f"Retrieve_documents full query with index: \n"
            f"""
            {{
                "query": {str(function_score_query).replace("'", '"')},
                "size": {top_k},
                "sort": {sort},
                "_source": true,
                "track_scores": {self.track_scores}
            }}
            """
        )

        return {
            "index": index_name,
            "query": function_score_query,
            "size": top_k,
            "sort": sort,
            "_source": True,
            "track_scores": self.track_scores,
        }

    def _create_match_single_query(
        self,
        entity_name: str,
        value: Any,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        return {
            "match": {
                entity_name: {
                    "query": value,
                    "boost": weight,
                }
            }
        }

    def _create_match_pharse_prefix_query(
        self,
        entity_name: str,
        value: Any,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        return {
            "match_phrase_prefix": {
                entity_name: {
                    "query": value,
                    "boost": weight,
                },
            }
        }

    def _create_range_query(
        self,
        entity_name: str,
        lower_bound: Optional[Any] = None,
        upper_bound: Optional[Any] = None,
    ) -> Dict[str, Any]:
        condition = {}
        if lower_bound is not None:
            condition["gte"] = lower_bound
        if upper_bound is not None:
            condition["lte"] = upper_bound
        return {"range": {entity_name: condition}}

    def _create_accents_query(
        self,
        entity_name: str,
        value: Any,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        return {
            "multi_match": {
                "query": value,
                "fields": [
                    f"{entity_name}.without_accent_normalized_analyzer",
                    f"{entity_name}.with_accent_normalized_analyzer",
                ],
                "type": "phrase_prefix",
                "boost": weight,
            }
        }

    def _create_multi_match_query(
        self,
        fields: Sequence[str],
        field_weights: Sequence[float],
        value: Any,
        *,
        match_type: str | None = None,
        operator: str | None = None,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        if len(fields) != len(field_weights):
            raise ValueError("fields and field_weights must have the same length")

        return {
            "multi_match": {
                "query": value,
                "fields": [
                    f"{name}^{field_weight}"
                    for name, field_weight in zip(fields, field_weights)
                ],
                **({"type": match_type} if match_type else {}),
                **({"operator": operator} if operator else {}),
                **({"boost": weight} if weight != 1.0 else {}),
            }
        }

    def _create_product_multi_match_query(
        self,
        value: Any,
        *,
        weight: float = 1.0,
    ) -> Dict[str, Any]:
        return self._create_multi_match_query(
            fields=["products.product_name", "products.product_description"],
            field_weights=[2.0, 1.0],
            value=value,
            match_type="phrase_prefix",
            weight=weight,
        )

    def _append_company_name_query(
        self,
        entities: Dict[str, Any],
        must_query: List[Dict[str, Any]],
    ) -> None:
        company_name = (entities.get("company_name") or "").strip()
        if not company_name:
            return

        must_query.append(
            self._create_match_pharse_prefix_query(
                entity_name="name",
                value=company_name,
            )
        )

    def _append_business_field_query(
        self,
        entities: Dict[str, Any],
        should_query: List[Dict[str, Any]],
    ) -> None:
        business_field = (entities.get("business_field") or "").strip()
        if not business_field:
            return

        intro_weight = 1.3 if self._use_enhanced_should else 1.0
        should_query.append(
            self._create_match_single_query(
                entity_name="introduction",
                value=business_field,
                weight=intro_weight,
            )
        )
        product_condition = [
            self._create_product_multi_match_query(
                value=business_field,
                weight=1.2 if self._use_enhanced_should else 1.0,
            ),
        ]
        nested_bool_key = "should" if self._use_enhanced_should else "filter"
        should_query.append(
            self._create_nested_product_clause(
                query_conditions=product_condition,
                bool_key=nested_bool_key,
                minimum_should_match=1 if nested_bool_key == "should" else 0,
            )
        )

    def _append_address_query(
        self,
        entities: Dict[str, Any],
        must_query: List[Dict[str, Any]],
    ) -> None:
        address = entities.get("address")
        if not address:
            return

        must_query.append(
            self._create_accents_query(
                entity_name="address",
                value=address,
            )
        )

    def _append_num_employees_query(
        self,
        entities: Dict[str, Any],
        must_query: List[Dict[str, Any]],
    ) -> None:
        employees = entities.get("num_employees")
        operator = entities.get("num_employees_operator")
        if not (employees and operator):
            return

        lower_bound = employees if operator == "gte" else None
        upper_bound = employees if operator == "lte" else None

        must_query.append(
            self._create_range_query(
                entity_name="employees",
                lower_bound=lower_bound,
                upper_bound=upper_bound,
            )
        )

    def _append_product_names_query(
        self,
        entities: Dict[str, Any],
        should_query: List[Dict[str, Any]],
        filter_query: List[Dict[str, Any]],
    ) -> None:
        raw_products = entities.get("product_names")
        if not raw_products:
            return

        product_names = [name.strip() for name in raw_products.split(",") if name.strip()]
        if not product_names:
            return

        product_name_conditions = [
            self._create_product_multi_match_query(
                value=product_name,
                weight=1.3 if self._use_enhanced_should else 1.0,
            )
            for product_name in product_names
        ]

        nested_bool_key = "should" if self._use_enhanced_should else "filter"
        product_clause = self._create_nested_product_clause(
            query_conditions=product_name_conditions,
            bool_key=nested_bool_key,
            minimum_should_match=1 if nested_bool_key == "should" else 0,
        )

        if nested_bool_key == "should":
            should_query.append(product_clause)
        else:
            filter_query.append(product_clause)

    def _build_information_score_functions(self) -> List[Dict[str, Any]]:
        """Boost companies that contain richer metadata."""
        functions: List[Dict[str, Any]] = [
            self._create_exists_function("url", weight=6.0),
            self._create_exists_function("introduction", weight=4.0),
            self._create_exists_function("products.product_name", weight=3.0),
            self._create_exists_function("phone", weight=1.5),
            self._create_exists_function("email", weight=1.5),
            self._create_exists_function("tax_code", weight=1.0),
            self._create_exists_function("address", weight=0.5),
        ]

        return functions

    def _build_domain_boost_functions(self) -> List[Dict[str, Any]]:
        functions: List[Dict[str, Any]] = []
        base_weight = 3.0

        for idx, domain in enumerate(self.preferred_domains):
            domain_value = domain.strip()
            if not domain_value:
                continue

            weight = max(1.0, base_weight - (0.4 * idx))
            functions.append(
                {
                    "filter": {
                        "wildcard": {
                            "url": {
                                "value": f"*{domain_value}*",
                            }
                        }
                    },
                    "weight": weight,
                }
            )

        return functions

    def _build_domain_penalty_functions(self) -> List[Dict[str, Any]]:
        if not self.penalty_domains or not self.penalty_weight:
            return []

        functions: List[Dict[str, Any]] = []
        for domain in self.penalty_domains:
            domain_value = domain.strip()
            if not domain_value:
                continue

            functions.append(
                {
                    "filter": {
                        "wildcard": {
                            "url": {
                                "value": f"*{domain_value}*",
                            }
                        }
                    },
                    "weight": float(self.penalty_weight),
                }
            )

        return functions

    def _build_contact_density_functions(self) -> List[Dict[str, Any]]:
        density_rules = [
            (["phone", "email"], 2.5),
            (["phone", "email", "url"], 3.0),
        ]

        return [
            self._create_multi_exists_function(fields=fields, weight=weight)
            for fields, weight in density_rules
        ]

    def _create_exists_function(self, field: str, weight: float) -> Dict[str, Any]:
        if field.startswith("products."):
            return self._create_nested_exists_function(
                path="products", field=field, weight=weight
            )

        return {"filter": {"exists": {"field": field}}, "weight": weight}

    def _create_multi_exists_function(
        self,
        fields: List[str],
        weight: float,
    ) -> Dict[str, Any]:
        return {
            "filter": {
                "bool": {
                    "must": [
                        {"exists": {"field": field}}
                        for field in fields
                        if field and not field.startswith("products.")
                    ]
                }
            },
            "weight": weight,
        }

    def _create_nested_exists_function(
        self,
        path: str,
        field: str,
        weight: float,
    ) -> Dict[str, Any]:
        return {
            "filter": {
                "nested": {
                    "path": path,
                    "query": {
                        "exists": {
                            "field": field,
                        }
                    },
                }
            },
            "weight": weight,
        }

    def _create_nested_product_clause(
        self,
        query_conditions: List[Dict[str, Any]],
        bool_key: str,
        minimum_should_match: int = 0,
    ) -> Dict[str, Any]:
        bool_body: Dict[str, Any] = {bool_key: query_conditions}
        if bool_key == "should" and minimum_should_match:
            bool_body["minimum_should_match"] = minimum_should_match

        return {
            "nested": {
                "path": "products",
                "score_mode": "avg",
                "query": {
                    "bool": bool_body,
                },
            }
        }

    def _build_url_blacklist_filters(self) -> List[Dict[str, Any]]:
        filters: List[Dict[str, Any]] = []
        for domain in self.url_blacklist:
            domain_value = domain.strip()
            if not domain_value:
                continue
            filters.append(
                {
                    "wildcard": {
                        "url": {
                            "value": f"*{domain_value}*",
                        }
                    }
                }
            )
        return filters

    def replace_strings(self, response: str) -> str:
        response = re.sub(
            r"[Tt][hH][oOôÔơƠỏỎổỔởỞ][ ][DdĐđ][iIíìỊị][aA][ ][Mm][oO][mM][oO]",
            "Thổ Địa Momo",
            response,
        )
        return response
