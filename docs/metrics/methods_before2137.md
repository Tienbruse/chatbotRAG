# Elasticsearch benchmark (search / msearch / get / mget)

- Timestamp (UTC): `2025-12-26T09:46:36Z`
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
| search_dsl | 4 | 4 | 18.594 | 220.400 | 2.850 |
| msearch_dsl | 4 | 1 | 7.074 | 598.124 | 4.100 |
| get | 50 | 50 | 35.889 | 1395.511 | — |
| mget | 50 | 5 | 14.329 | 3490.930 | — |
