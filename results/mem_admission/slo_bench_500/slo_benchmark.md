# SLO benchmark: FIFO N4 vs E11b

Solo base: `results/solo_SLO/full500_v1` (retain mode), 500 tasks, reps per task: 3–3; solo latency p50 15.7 s, max 135.1 s; median rep-to-rep std 0.51% of the mean.

Runs: FIFO N4: 3 (results/mem_admission/full500_fifo_n4_rebuilt_r1, results/mem_admission/full500_fifo_n4_rebuilt_r2, results/mem_admission/full500_fifo_n4_rebuilt_r3); E11b: 3 (results/mem_admission/full500_e11b_cap2048_gbm_slotreuse_ownfirst, results/mem_admission/full500_e11b_cap2048_gbm_slotreuse_ownfirst_r2, results/mem_admission/full500_e11b_cap2048_gbm_slotreuse_ownfirst_r3)

Tasks scored (have a solo latency): 500.

## 1. Headline metrics (mean ± std over runs)

|                | FIFO N4      | E11b         |
|:---------------|:-------------|:-------------|
| completed      | 477.3 ± 2.3  | 500.0 ± 0.0  |
| tasks/min      | 4.1 ± 0.0    | 3.9 ± 0.0    |
| latency p50 s  | 2,847 ± 41   | 1,571 ± 170  |
| latency p95 s  | 4,751 ± 52   | 5,864 ± 113  |
| latency p99 s  | 5,654 ± 114  | 6,876 ± 115  |
| latency mean s | 2,590 ± 31   | 2,085 ± 62   |
| slowdown p50   | 153.2 ± 1.2  | 55.8 ± 1.1   |
| slowdown p90   | 518.5 ± 13.5 | 384.1 ± 9.7  |
| slowdown p95   | 1,009 ± 208  | 606.6 ± 67.3 |
| slowdown p99   | inf          | 1,374 ± 32   |
| SLO 2x met %   | 1.2 ± 0.0    | 0.8 ± 0.0    |
| SLO 5x met %   | 2.6 ± 0.0    | 11.9 ± 0.3   |
| SLO 10x met %  | 3.9 ± 0.1    | 27.9 ± 1.8   |
| SLO 20x met %  | 6.5 ± 0.1    | 35.3 ± 1.0   |
| SLO 50x met %  | 16.3 ± 0.3   | 45.9 ± 0.3   |
| SLO 100x met % | 33.9 ± 0.6   | 63.1 ± 2.0   |
| SLO 200x met % | 61.4 ± 0.3   | 76.9 ± 0.9   |
| SLO 500x met % | 89.1 ± 0.7   | 93.1 ± 1.2   |

## 2. Per task, averaged over runs: head-to-head

|                                             |       E11b |
|:--------------------------------------------|-----------:|
| tasks with lower mean slowdown than FIFO N4 | 324        |
| higher                                      | 176        |
| equal (incl. both failed)                   |   0        |
| median ratio (system / ref), finite only    |   0.578241 |
| tasks meeting 100x in all runs              | 261        |
| FIFO N4 tasks meeting 100x in all runs      | 158        |

## 3. Segregations (mean over runs; solo-length terciles at 11.5 s and 22.5 s)

### By class

|                  |   SLO 100x % · E11b |   SLO 100x % · FIFO N4 |   SLO 200x % · E11b |   SLO 200x % · FIFO N4 |   SLO 20x % · E11b |   SLO 20x % · FIFO N4 |   SLO 50x % · E11b |   SLO 50x % · FIFO N4 |   slowdown p50 · E11b |   slowdown p50 · FIFO N4 |
|:-----------------|--------------------:|-----------------------:|--------------------:|-----------------------:|-------------------:|----------------------:|-------------------:|----------------------:|----------------------:|-------------------------:|
| ('mbpp', 350)    |                47.5 |                   41   |                  67 |                   68.1 |               17.7 |                   7.6 |               28   |                  19.9 |                 106.1 |                    126.3 |
| ('nl2bash', 150) |                99.6 |                   17.6 |                 100 |                   45.8 |               76.2 |                   4   |               87.8 |                   8   |                   6.8 |                    218.8 |

### By steps

|                   |   SLO 100x % · E11b |   SLO 100x % · FIFO N4 |   SLO 200x % · E11b |   SLO 200x % · FIFO N4 |   SLO 20x % · E11b |   SLO 20x % · FIFO N4 |   SLO 50x % · E11b |   SLO 50x % · FIFO N4 |   slowdown p50 · E11b |   slowdown p50 · FIFO N4 |
|:------------------|--------------------:|-----------------------:|--------------------:|-----------------------:|-------------------:|----------------------:|-------------------:|----------------------:|----------------------:|-------------------------:|
| ('1 step', 165)   |                39   |                   51.5 |                55.2 |                   78.4 |               21.8 |                   9.1 |               29.5 |                  27.9 |                 161.1 |                     98.1 |
| ('2 steps', 230)  |                71.3 |                   26.1 |                84.8 |                   51.2 |               48.3 |                   6.4 |               57.4 |                  12   |                  24.5 |                    195.9 |
| ('3+ steps', 105) |                83.2 |                   23.5 |                94   |                   57.1 |               27.9 |                   2.9 |               46.7 |                   7.6 |                  51.1 |                    180.3 |

### By solo length

|                 |   SLO 100x % · E11b |   SLO 100x % · FIFO N4 |   SLO 200x % · E11b |   SLO 200x % · FIFO N4 |   SLO 20x % · E11b |   SLO 20x % · FIFO N4 |   SLO 50x % · E11b |   SLO 50x % · FIFO N4 |   slowdown p50 · E11b |   slowdown p50 · FIFO N4 |
|:----------------|--------------------:|-----------------------:|--------------------:|-----------------------:|-------------------:|----------------------:|-------------------:|----------------------:|----------------------:|-------------------------:|
| ('long', 167)   |                77.8 |                   53.3 |                96.4 |                   86.2 |               20.2 |                   9.4 |               38.3 |                  25.1 |                  56.7 |                     95.7 |
| ('medium', 166) |                56.2 |                   30.1 |                74.3 |                   64.5 |               41.4 |                   4.2 |               48.6 |                  13.9 |                  60.5 |                    165.9 |
| ('short', 167)  |                55.3 |                   18.4 |                60.1 |                   33.5 |               44.3 |                   6   |               50.9 |                  10   |                  43.1 |                    332.9 |

### By arrival

|                      |   SLO 100x % · E11b |   SLO 100x % · FIFO N4 |   SLO 200x % · E11b |   SLO 200x % · FIFO N4 |   SLO 20x % · E11b |   SLO 20x % · FIFO N4 |   SLO 50x % · E11b |   SLO 50x % · FIFO N4 |   slowdown p50 · E11b |   slowdown p50 · FIFO N4 |
|:---------------------|--------------------:|-----------------------:|--------------------:|-----------------------:|-------------------:|----------------------:|-------------------:|----------------------:|----------------------:|-------------------------:|
| ('first half', 250)  |                61.6 |                   54   |                74.4 |                   76.7 |               36.1 |                  13.1 |               44.7 |                  31.1 |                  57.9 |                     92.5 |
| ('second half', 250) |                64.7 |                   13.9 |                79.5 |                   46.1 |               34.4 |                   0   |               47.2 |                   1.6 |                  53.3 |                    218   |

## 4. Run-to-run spread

|                     | FIFO N4     | E11b        |
|:--------------------|:------------|:------------|
| tasks/min range     | 4.10–4.18   | 3.85–3.90   |
| latency p95 range s | 4,695–4,798 | 5,739–5,958 |
| SLO 100x range %    | 33.4–34.6   | 62.0–65.4   |
