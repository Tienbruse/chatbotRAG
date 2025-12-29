# Elasticsearch benchmark (search / msearch / get / mget)

- Timestamp (UTC): `2025-12-26T09:47:05Z`
- Index: `company-data-20240329`
- Query version (DSL): `v1`
- Runs: `10`, warmup: `5`
- search batch size: `0`
- msearch batch size: `0`
- mget batch size: `10`
- mget docs: `50`

## Summary

| method | ops/run | requests/run | wall mean (ms) | QPS mean | ES took mean (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| search_dsl | 4 | 4 | 22.028 | 182.496 | 3.425 |
| msearch_dsl | 4 | 1 | 7.651 | 534.643 | 3.975 |
| get | 50 | 50 | 36.236 | 1380.744 | — |
| mget | 50 | 5 | 15.053 | 3334.732 | — |
