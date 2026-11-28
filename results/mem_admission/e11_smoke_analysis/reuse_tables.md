## Run comparison

|                   | E11 slot reuse   | E7b control (erase)   |
|:------------------|:-----------------|:----------------------|
| completed         | 50/50            | 50/50                 |
| prompt tokens     | 609493           | 609437                |
| computed          | 258142           | 391468                |
| reused            | 351351           | 217969                |
| reused %          | 57.6             | 35.8                  |
| p50 s             | 219.2            | 227.0                 |
| p95 s             | 713.3            | 824.7                 |
| p99 s             | 909.1            | 962.0                 |
| tasks/min         | 3.12             | 2.94                  |
| TPOT p50 ms       | 24.6             | 26.5                  |
| mean KV %         | 70.2             | 66.5                  |
| llama.cpp purges  | 0                | 0                     |
| leftover erases   | 16               | 0                     |
| deadlock discards | 0                | 0                     |

## Reuse by request type

| run                 | request type   |   requests |   prompt tokens |   computed |   reused |   reused % |
|:--------------------|:---------------|-----------:|----------------:|-----------:|---------:|-----------:|
| E11 slot reuse      | continuation   |         41 |          222280 |       7129 |   215151 |       96.8 |
| E11 slot reuse      | new after tool |         63 |          330043 |     231621 |    98422 |       29.8 |
| E11 slot reuse      | new step 0     |         50 |           57170 |      19392 |    37778 |       66.1 |
| E7b control (erase) | continuation   |         41 |          222175 |       4206 |   217969 |       98.1 |
| E7b control (erase) | new after tool |         63 |          330092 |     330092 |        0 |        0   |
| E7b control (erase) | new step 0     |         50 |           57170 |      57170 |        0 |        0   |

## E11 reuse by slot choice (own = task's own previous slot, finished = a finished task's leftover, other = a live task's leftover, empty, cont = continuation in its own slot)

| kind   |   requests |   reused |   prompt |   reused_median |   predicted_median |   reused % |
|:-------|-----------:|---------:|---------:|----------------:|-------------------:|-----------:|
| cont   |         41 |   215151 |   222280 |            3294 |                  0 |       96.8 |
| empty  |         18 |        0 |    32579 |               0 |                  0 |        0   |
| other  |         91 |    95497 |   313702 |            1048 |               1048 |       30.4 |
| own    |          4 |    40703 |    40932 |           11856 |              11857 |       99.4 |

Predicted vs actual reused tokens (non-empty slots): median difference 0, |diff| <= 1 token for 69.9% of 136 requests; actual < predicted by > 16 tokens for 0 requests (cache lost before use).

## E11: fate of a returning step's previous slot

|                             |   requests |
|:----------------------------|-----------:|
| reused its own slot         |          4 |
| slot taken by another task  |         50 |
| slot erased by memory guard |          9 |
