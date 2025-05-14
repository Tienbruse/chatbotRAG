import re
from typing import Any, Dict, List, Optional

from src.utils.logger import logger


class QueryCreator:
    def create_query(
        self,
        entities: Dict[str, Any],
        index_name: str,
        top_k: int,
    ) -> Optional[Dict[str, Any]]:
        """Create query in Elasticsearch

        Args:
            entities (Dict[str, Any]): Entities from user's message

        Returns:
            List[Dict[str, Any]]: List of query condition
        """
        must_query = []
        should_query = []
        must_not_query = []
        filter_query = []

        if entities["company_name"]:
            must_query.append(
                self._create_match_pharse_prefix_query(
                    entity_name="name",
                    value=entities["company_name"],
                )
            )

        if entities["business_field"]:
            should_query.append(
                self._create_match_single_query(
                    entity_name="introduction",
                    value=entities["business_field"],
                )
            )
            product_name_condition = self._create_match_pharse_prefix_query(
                entity_name="products.product_name",
                value=entities["business_field"],
            )
            should_query.append(
                {
                    "nested": {
                        "path": "products",
                        "query": {
                            "bool": {
                                "filter": product_name_condition,
                            }
                        },
                    }
                }
            )

        if entities["address"]:
            must_query.append(
                self._create_accents_query( # <--- THAY ĐỔI Ở ĐÂY
                    entity_name="address", # Tên trường gốc
                    value=entities["address"],
                )
            )

        if entities["num_employees"] and entities["num_employees_operator"]:
            if entities["num_employees_operator"] == "gte":
                lower_bound = entities["num_employees"]
                upper_bound = None
            elif entities["num_employees_operator"] == "lte":
                lower_bound = None
                upper_bound = entities["num_employees"]

            must_query.append(
                self._create_range_query(
                    entity_name="employees",
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                )
            )

        if entities.get("product_names"):
            product_names = entities["product_names"].split(",")
            product_names = [v.strip() for v in product_names]

            product_name_conditions = []

            for product_name in product_names:
                product_name_conditions.append(
                    self._create_match_pharse_prefix_query(
                        entity_name="products.product_name",
                        value=product_name,
                    )
                )

            if len(product_name_conditions):
                filter_query.append(
                    {
                        "nested": {
                            "path": "products",
                            "query": {
                                "bool": {
                                    "filter": product_name_conditions,
                                }
                            },
                        }
                    }
                )

        if (
            not len(must_query)
            and not len(should_query)
            and not len(must_not_query)
            and not len(filter_query)
        ):
            return None

        query = {
            "bool": {
                "filter": filter_query,
                "must": [*must_query],
                "should": [*should_query],
                "must_not": [*must_not_query],
            },
        }

        function_score_query = {
            "function_score": {
                "query": query,
            }
        }
        sort = []

        logger.info(
            f"Retrieve_documents full query with index: \n"
            f"""
            {{
                "query": {str(function_score_query).replace("'", '"')},
                "size": {top_k},
                "sort": {sort},
                "_source": true
            }}
            """
        )

        return {
            "index": index_name,
            "query": function_score_query,
            "size": top_k,
            "sort": sort,
            "source": True,
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
        self, entity_name: List[str], entity_weight: List[str], value: Any
    ) -> Dict[str, Any]:
        return {
            "multi_match": {
                "query": value,
                "fields": [
                    f"{name}^{weight}"
                    for name, weight in zip(entity_name, entity_weight)
                ],
            }
        }

    def replace_strings(self, response: str) -> str:
        response = re.sub(
            r"[Tt][hH][oOôÔơƠỏỎổỔởỞ][ ][DdĐđ][iIíìỊị][aA][ ][Mm][oO][mM][oO]",
            "Thổ Địa Momo",
            response,
        )  # noqa: E501
        return response
