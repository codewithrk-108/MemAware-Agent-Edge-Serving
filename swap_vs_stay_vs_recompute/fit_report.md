# Fit report

Data: {'measurements': 339, 'request_errors': 36, 'swap_errors': 0, 'requests': 113, 'reps': 3, 'kv_bytes_per_token': 114704.1995039956, 'restore_fully_reused': 301, 'restore_checked': 303}

- **swap_out / server**: {'a_out': 0.18559564866942116, 'b_out': 351.6172321998565} | R2 0.603 RMSE 418.2 ms MAPE 33.1% | CV R2 0.596 CV MAPE 33.4% | n 303
- **swap_out / client**: {'a_out': 0.1864995365391849, 'b_out': 372.45805525236983} | R2 0.575 RMSE 445.3 ms MAPE 32.7% | CV R2 0.566 CV MAPE 33.1% | n 303
- **swap_in / server**: {'a_in': 0.11459938761262559, 'b_in': 230.52656362647633} | R2 0.529 RMSE 300.6 ms MAPE 40.7% | CV R2 0.525 CV MAPE 41.0% | n 303
- **swap_in / client**: {'a_in': 0.11268402833819767, 'b_in': 274.8957939705604} | R2 0.481 RMSE 325.2 ms MAPE 40.7% | CV R2 0.475 CV MAPE 41.0% | n 303
- **recompute / server**: {'c': -34.50135242286755, 'd': 0.4839698337525293, 'e': -1.0544949106282522e-05} | R2 0.799 RMSE 496.7 ms MAPE 28.7% | CV R2 0.795 CV MAPE 29.3% | n 303
- **recompute / client**: {'c': 3.7765116755766535, 'd': 0.514581440233908, 'e': -1.2376910447809435e-05} | R2 0.712 RMSE 650.7 ms MAPE 32.8% | CV R2 0.704 CV MAPE 33.4% | n 303
- **recompute / server, e = 0**: {'c': 118.30150729435209, 'd': 0.3778907284534294, 'e': 0.0} | R2 0.792 MAPE 31.0% | CV R2 0.789 CV MAPE 31.4%
- **recompute / solo reference (no contention)**: {'c': 10.12880230864001, 'd': 0.0905827202613879, 'e': 7.937676430336066e-06} | R2 0.9999 MAPE 1.0% | n 339

Effective bandwidth (server slope): {'swap_out': 0.6180328058676858, 'swap_in': 1.00091459381724}

## Policy models (split length, contention)

- **swap_out_split**: {'b': 150.17468481527325, 'a_p': 0.14172213884005266, 'a_g': 0.4185150082254713} | R2 0.834 MAPE 19.1% | CV R2 0.830 CV MAPE 19.3% | n 303
- **swap_in_split**: {'b': 140.6031108530676, 'a_p': 0.09502128321523597, 'a_g': 0.21853726619349373} | R2 0.634 MAPE 35.3% | CV R2 0.621 CV MAPE 35.9% | n 303
- **recompute_contention**: {'c': -126.19336637137116, 'd': 0.5465647309085482, 'f_dec': -0.05343756413437029, 'f_pre': -0.08759187964290772, 'g': 0.022820245217477015} | R2 0.825 MAPE 25.6% | CV R2 0.814 CV MAPE 26.9% | n 303
