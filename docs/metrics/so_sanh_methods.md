# So sánh benchmark phương pháp Elasticsearch

- Gốc: `docs/metrics/methods_before.json`
- Mới: `docs/metrics/methods_after.json`

| Phương pháp | Requests/run | Độ trễ TB (ms) | Δ TB | QPS TB | Δ QPS | ES took TB (ms) | Δ ES |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| get | 50 → 50 | 35.889 → 36.236 | +0.97% | 1395.511 → 1380.744 | -1.06% | — → — | — |
| mget | 5 → 5 | 14.329 → 15.053 | +5.05% | 3490.930 → 3334.732 | -4.47% | — → — | — |
| msearch_dsl | 1 → 1 | 7.074 → 7.651 | +8.16% | 598.124 → 534.643 | -10.61% | 4.100 → 3.975 | -3.05% |
| search_dsl | 4 → 4 | 18.594 → 22.028 | +18.47% | 220.400 → 182.496 | -17.20% | 2.850 → 3.425 | +20.18% |
