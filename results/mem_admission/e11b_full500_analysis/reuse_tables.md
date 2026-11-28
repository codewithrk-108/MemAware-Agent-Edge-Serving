## Run comparison

|                          | E11b slot reuse   | E7b control (erase)   |
|:-------------------------|:------------------|:----------------------|
| completed                | 500/500           | 500/500               |
| prompt tokens            | 4569909           | 4569839               |
| computed                 | 2500809           | 3042953               |
| reused                   | 2069100           | 1526886               |
| reused %                 | 45.3              | 33.4                  |
| p50 s                    | 1603.8            | 1522.6                |
| p95 s                    | 5958.1            | 6136.0                |
| p99 s                    | 7002.9            | 7133.9                |
| tasks/min                | 3.85              | 3.7                   |
| TPOT p50 ms              | 26.4              | 27.2                  |
| mean KV %                | 70.6              | 69.8                  |
| llama.cpp purges         | 0                 | 0                     |
| leftover erases          | 608               | 0                     |
| of which finished task's | 54                | 0                     |
| deadlock discards        | 4                 | 9                     |

## Reuse by request type

| run                 | request type   |   requests |   prompt tokens |   computed |   reused |   reused % |
|:--------------------|:---------------|-----------:|----------------:|-----------:|---------:|-----------:|
| E11b slot reuse     | continuation   |        331 |         1630575 |      57963 |  1572612 |       96.4 |
| E11b slot reuse     | new after tool |        568 |         2354612 |    2053505 |   301107 |       12.8 |
| E11b slot reuse     | new step 0     |        504 |          584722 |     389341 |   195381 |       33.4 |
| E7b control (erase) | continuation   |        326 |         1597042 |      70156 |  1526886 |       95.6 |
| E7b control (erase) | new after tool |        571 |         2379555 |    2379555 |        0 |        0   |
| E7b control (erase) | new step 0     |        506 |          593242 |     593242 |        0 |        0   |

## E11b reuse by slot choice (own = task's own previous slot, finished = a finished task's leftover, other = a live task's leftover, empty, cont = continuation in its own slot)

| kind     |   requests |   reused |   prompt |   reused_median |   predicted_median |   reused % |
|:---------|-----------:|---------:|---------:|----------------:|-------------------:|-----------:|
| cont     |        331 |  1572612 |  1630575 |          3266   |                0   |       96.4 |
| empty    |        610 |        0 |  1585658 |             0   |                0   |        0   |
| finished |        444 |   466139 |  1320999 |          1050   |             1050   |       35.3 |
| own      |         18 |    30349 |    32677 |          1681.5 |             1760.5 |       92.9 |

Predicted vs actual reused tokens (non-empty slots): median difference 0, |diff| <= 1 token for 58.0% of 793 requests; actual < predicted by > 16 tokens for 2 requests (cache lost before use).

## E11b: fate of a returning step's previous slot

|                             |   requests |
|:----------------------------|-----------:|
| reused its own slot         |         18 |
| slot taken by another task  |          0 |
| slot erased by memory guard |        550 |
