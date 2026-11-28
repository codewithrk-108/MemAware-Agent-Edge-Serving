Slowdown = completion under load / solo latency (retain mode, Study A). Failed tasks count as misses (slowdown = inf). Reference for head-to-head: FIFO N4 (rebuilt).

|                                     |   FIFO N4 (rebuilt) |   E11b |   E13 K=0 |
|:------------------------------------|--------------------:|-------:|----------:|
| tasks scored                        |                50.0 |   50.0 |      50.0 |
| failed                              |                 4.0 |    0.0 |       0.0 |
| slowdown p50                        |                14.6 |   11.3 |      16.3 |
| slowdown p90                        |                46.1 |   28.3 |      60.6 |
| slowdown p95                        |               inf   |   37.0 |      96.3 |
| slowdown mean (ok only)             |                17.2 |   15.3 |      25.3 |
| SLO 2x met %                        |                 4.0 |    2.0 |       2.0 |
| SLO 5x met %                        |                12.0 |   16.0 |      18.0 |
| SLO 10x met %                       |                30.0 |   40.0 |      38.0 |
| SLO 20x met %                       |                60.0 |   80.0 |      58.0 |
| SLO 50x met %                       |                92.0 |   96.0 |      86.0 |
| tasks faster than FIFO N4 (rebuilt) |                 0.0 |   27.0 |      23.0 |
| tasks slower than FIFO N4 (rebuilt) |                 0.0 |   23.0 |      27.0 |

## By class and step count

|                         |   ('SLO 10x met %', 'E11b') |   ('SLO 10x met %', 'E13 K=0') |   ('SLO 10x met %', 'FIFO N4 (rebuilt)') |   ('slowdown p50', 'E11b') |   ('slowdown p50', 'E13 K=0') |   ('slowdown p50', 'FIFO N4 (rebuilt)') |
|:------------------------|----------------------------:|-------------------------------:|-----------------------------------------:|---------------------------:|------------------------------:|----------------------------------------:|
| ('mbpp', '1 step')      |                        47.1 |                           17.6 |                                     35.3 |                       13.4 |                          25.5 |                                    12.6 |
| ('mbpp', '2 steps')     |                        33.3 |                           33.3 |                                     22.2 |                       11.0 |                          16.3 |                                    12.7 |
| ('mbpp', '3+ steps')    |                        55.6 |                           77.8 |                                     44.4 |                        9.2 |                           6.4 |                                    21.2 |
| ('nl2bash', '2 steps')  |                        16.7 |                           41.7 |                                     16.7 |                       12.6 |                          14.6 |                                    18.3 |
| ('nl2bash', '3+ steps') |                        66.7 |                           33.3 |                                     33.3 |                        9.3 |                          17.3 |                                    20.6 |
