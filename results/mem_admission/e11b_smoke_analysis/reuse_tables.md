## Run comparison

|                   | E11b slot reuse   | E7b control (erase)   |
|:------------------|:------------------|:----------------------|
| completed         | 50/50             | 50/50                 |
| prompt tokens     | 609557            | 609437                |
| computed          | 261617            | 391468                |
| reused            | 347940            | 217969                |
| reused %          | 57.1              | 35.8                  |
| p50 s             | 232.6             | 227.0                 |
| p95 s             | 678.7             | 824.7                 |
| p99 s             | 928.4             | 962.0                 |
| tasks/min         | 3.1               | 2.94                  |
| TPOT p50 ms       | 25.1              | 26.5                  |
| mean KV %         | 70.6              | 66.5                  |
| llama.cpp purges  | 0                 | 0                     |
| leftover erases   | 60                | 0                     |
| deadlock discards | 0                 | 0                     |

## Reuse by request type

| run                 | request type   |   requests |   prompt tokens |   computed |   reused |   reused % |
|:--------------------|:---------------|-----------:|----------------:|-----------:|---------:|-----------:|
| E11b slot reuse     | continuation   |         41 |          222272 |       6581 |   215691 |       97   |
| E11b slot reuse     | new after tool |         63 |          330115 |     212569 |   117546 |       35.6 |
| E11b slot reuse     | new step 0     |         50 |           57170 |      42467 |    14703 |       25.7 |
| E7b control (erase) | continuation   |         41 |          222175 |       4206 |   217969 |       98.1 |
| E7b control (erase) | new after tool |         63 |          330092 |     330092 |        0 |        0   |
| E7b control (erase) | new step 0     |         50 |           57170 |      57170 |        0 |        0   |

## E11b reuse by slot choice (own = task's own previous slot, finished = a finished task's leftover, other = a live task's leftover, empty, cont = continuation in its own slot)

| kind     |   requests |   reused |   prompt |   reused_median |   predicted_median |   reused % |
|:---------|-----------:|---------:|---------:|----------------:|-------------------:|-----------:|
| cont     |         41 |   215691 |   222272 |            3350 |                  0 |       97   |
| empty    |         62 |        0 |   157933 |               0 |                  0 |        0   |
| finished |         40 |    41984 |   138286 |            1049 |               1049 |       30.4 |
| own      |         11 |    90265 |    91066 |            7972 |               7973 |       99.1 |

Predicted vs actual reused tokens (non-empty slots): median difference 0, |diff| <= 1 token for 55.4% of 92 requests; actual < predicted by > 16 tokens for 0 requests (cache lost before use).

## E11b: fate of a returning step's previous slot

|                             |   requests |
|:----------------------------|-----------:|
| reused its own slot         |         11 |
| slot taken by another task  |          0 |
| slot erased by memory guard |         52 |
