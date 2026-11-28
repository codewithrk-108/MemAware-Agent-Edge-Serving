## Run comparison

|                          | E11c slot reuse   | E7b control (erase)   |
|:-------------------------|:------------------|:----------------------|
| completed                | 50/50             | 50/50                 |
| prompt tokens            | 609334            | 609437                |
| computed                 | 331153            | 391468                |
| reused                   | 278181            | 217969                |
| reused %                 | 45.7              | 35.8                  |
| p50 s                    | 222.7             | 227.0                 |
| p95 s                    | 764.0             | 824.7                 |
| p99 s                    | 940.2             | 962.0                 |
| tasks/min                | 3.01              | 2.94                  |
| TPOT p50 ms              | 25.1              | 26.5                  |
| mean KV %                | 69.9              | 66.5                  |
| llama.cpp purges         | 0                 | 0                     |
| leftover erases          | 65                | 0                     |
| of which finished task's | 6                 | 0                     |
| deadlock discards        | 0                 | 0                     |

## Reuse by request type

| run                 | request type   |   requests |   prompt tokens |   computed |   reused |   reused % |
|:--------------------|:---------------|-----------:|----------------:|-----------:|---------:|-----------:|
| E11c slot reuse     | continuation   |         41 |          222243 |       5409 |   216834 |       97.6 |
| E11c slot reuse     | new after tool |         63 |          329921 |     284313 |    45608 |       13.8 |
| E11c slot reuse     | new step 0     |         50 |           57170 |      41431 |    15739 |       27.5 |
| E7b control (erase) | continuation   |         41 |          222175 |       4206 |   217969 |       98.1 |
| E7b control (erase) | new after tool |         63 |          330092 |     330092 |        0 |        0   |
| E7b control (erase) | new step 0     |         50 |           57170 |      57170 |        0 |        0   |

## E11c reuse by slot choice (own = task's own previous slot, finished = a finished task's leftover, other = a live task's leftover, empty, cont = continuation in its own slot)

| kind     |   requests |   reused |   prompt |   reused_median |   predicted_median |   reused % |
|:---------|-----------:|---------:|---------:|----------------:|-------------------:|-----------:|
| cont     |         41 |   216834 |   222243 |          3350   |                0   |       97.6 |
| empty    |         67 |        0 |   213915 |             0   |                0   |        0   |
| finished |         42 |    44079 |   155542 |          1048.5 |             1048.5 |       28.3 |
| own      |          4 |    17268 |    17634 |          3920.5 |             3921.5 |       97.9 |

Predicted vs actual reused tokens (non-empty slots): median difference 0, |diff| <= 1 token for 52.9% of 87 requests; actual < predicted by > 16 tokens for 0 requests (cache lost before use).

## E11c: fate of a returning step's previous slot

|                             |   requests |
|:----------------------------|-----------:|
| reused its own slot         |          4 |
| slot taken by another task  |          0 |
| slot erased by memory guard |         59 |
