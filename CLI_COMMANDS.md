# CLI Commands to Simulate Each Test

Run these from the project root (where `store_optimizer_demo.py` and `store_staffing_optimizer.py` live).

---

## store_optimizer_demo.py

### Help
```bash
python store_optimizer_demo.py --help
```

### Defaults
```bash
python store_optimizer_demo.py --params max_time_in_seconds:0.5
```

### Time and roster
```bash
python store_optimizer_demo.py --year 2024 --month 6 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --store_ids store_A,store_B --params max_time_in_seconds:0.5
python store_optimizer_demo.py --current_employees 12 --min_employees 10 --max_employees 15 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --seed 42 --params max_time_in_seconds:0.5
```

### Demand source
```bash
python store_optimizer_demo.py --demand direct --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand estimation --rule ratio --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand estimation --rule tiers --tiers 50,1,100,2,inf,4 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand estimation --rule formula --base_employees 2 --params max_time_in_seconds:0.5
```

### Estimation params
```bash
python store_optimizer_demo.py --demand estimation --customers_per_employee_M 25 --customers_per_employee_A 35 --min_employees_estimation 2 --params max_time_in_seconds:0.5
```

### Direct demand
```bash
python store_optimizer_demo.py --direct_base_M_weekday 6 --direct_base_A_weekday 5 --direct_base_M_weekend 5 --direct_base_A_weekend 4 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --direct_deterministic --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand_profile weekend_heavy --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand_profile weekday_heavy --params max_time_in_seconds:0.5
python store_optimizer_demo.py --store_ids A,B --store_profiles A:weekend_heavy,B:weekday_heavy --params max_time_in_seconds:0.5
```

### Optimizer constraints
Defaults: `--min_days_off_per_week 1`, `--max_shifts_per_week 6` (sum to 7), `--max_consecutive_work_days 5`, `--max_weekend_work_shifts_women None` (disabled). Use `0` to disable min/max shifts/consecutive.
```bash
python store_optimizer_demo.py --excess_penalty_M 2 --excess_penalty_A 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_shifts_per_week 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_days_off_per_week 1 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_consecutive_work_days 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_days_off_per_week 0 --max_shifts_per_week 0 --max_consecutive_work_days 0 --params max_time_in_seconds:0.5  # disable all
python store_optimizer_demo.py --sequence_constraints M:1,1,0,3,4,5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --weekly_sum_constraints O:1,2,7,2,3,4 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_weekend_work_shifts_women 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --target_min_women_ratio 0.35 --target_max_women_ratio 0.65 --params max_time_in_seconds:0.5
```

### Solver
```bash
python store_optimizer_demo.py --params max_time_in_seconds:0.5
python store_optimizer_demo.py --output_proto /tmp/demo_model.pbtxt --params max_time_in_seconds:0.5
```

### Error cases (expect non-zero exit)
```bash
python store_optimizer_demo.py --demand invalid
python store_optimizer_demo.py --demand estimation --rule invalid
python store_optimizer_demo.py --demand estimation --rule tiers --tiers 50,1,100
```

---

## store_staffing_optimizer.py

### Help
```bash
python store_staffing_optimizer.py --help
```

### Defaults
```bash
python store_staffing_optimizer.py --params max_time_in_seconds:0.5
```

### Time and roster
```bash
python store_staffing_optimizer.py --year=2024 --month=3 --params max_time_in_seconds:0.5
python store_staffing_optimizer.py --current_employees=12 --min_employees=10 --max_employees=14 --params max_time_in_seconds:0.5
```

### Demand bases
```bash
python store_staffing_optimizer.py --demand_base_M_weekday=6 --demand_base_A_weekday=5 --demand_base_M_weekend=5 --demand_base_A_weekend=4 --params max_time_in_seconds:0.5
```

### Solver
```bash
python store_staffing_optimizer.py --params max_time_in_seconds:0.5
python store_staffing_optimizer.py --output_proto /tmp/optimizer_model.pbtxt --params max_time_in_seconds:0.5
```
