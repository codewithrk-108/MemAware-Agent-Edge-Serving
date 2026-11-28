## Run comparison

|                          | E12 slot reuse   | E11b (no running cap)   |
|:-------------------------|:-----------------|:------------------------|
| completed                | 500/500          | 500/500                 |
| prompt tokens            | 4570894          | 4569909                 |
| computed                 | 1090706          | 2500809                 |
| reused                   | 3480188          | 2069100                 |
| reused %                 | 76.1             | 45.3                    |
| p50 s                    | 2749.6           | 1603.8                  |
| p95 s                    | 7192.8           | 5958.1                  |
| p99 s                    | 7786.3           | 7002.9                  |
| tasks/min                | 3.47             | 3.85                    |
| TPOT p50 ms              | 20.7             | 26.4                    |
| mean KV %                | 65.2             | 70.6                    |
| llama.cpp purges         | 0                | 0                       |
| leftover erases          | 202              | 608                     |
| of which finished task's | 20               | 54                      |
| deadlock discards        | 0                | 4                       |

## Reuse by request type

| run                   | request type   |   requests |   prompt tokens |   computed |   reused |   reused % |
|:----------------------|:---------------|-----------:|----------------:|-----------:|---------:|-----------:|
| E12 slot reuse        | continuation   |        335 |         1643660 |      59298 |  1584362 |       96.4 |
| E12 slot reuse        | new after tool |        568 |         2355258 |     852439 |  1502819 |       63.8 |
| E12 slot reuse        | new step 0     |        500 |          571976 |     178969 |   393007 |       68.7 |
| E11b (no running cap) | continuation   |        331 |         1630575 |      57963 |  1572612 |       96.4 |
| E11b (no running cap) | new after tool |        568 |         2354612 |    2053505 |   301107 |       12.8 |
| E11b (no running cap) | new step 0     |        504 |          584722 |     389341 |   195381 |       33.4 |

## E12 reuse by slot choice (own = task's own previous slot, finished = a finished task's leftover, other = a live task's leftover, empty, cont = continuation in its own slot)

| kind     |   requests |   reused |   prompt |   reused_median |   predicted_median |   reused % |
|:---------|-----------:|---------:|---------:|----------------:|-------------------:|-----------:|
| cont     |        335 |  1584362 |  1643660 |          3242   |                0   |       96.4 |
| empty    |        204 |        0 |   547673 |             0   |                0   |        0   |
| finished |        478 |   502129 |   943073 |          1050   |             1050   |       53.2 |
| own      |        386 |  1393697 |  1436488 |          2755.5 |             2838.5 |       97   |

Predicted vs actual reused tokens (non-empty slots): median difference 0, |diff| <= 1 token for 66.9% of 1199 requests; actual < predicted by > 16 tokens for 59 requests (cache lost before use).

## E12: fate of a returning step's previous slot

|                             |   requests |
|:----------------------------|-----------:|
| reused its own slot         |        386 |
| slot taken by another task  |          0 |
| slot erased by memory guard |        182 |
