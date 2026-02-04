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

### Real data: roster, demand, work/rest constraints
Specify roster (e.g. 7 men, 8 women), per-day demand, max work days, min rest days:
```bash
python store_optimizer_demo.py --men 7 --women 8 \
  --direct_base_M_weekday 5,6,5,5,6 --direct_base_A_weekday 4,5,4,4,5 \
  --direct_base_M_weekend 4,3 --direct_base_A_weekend 3,3 \
  --max_shifts_per_week 6 --min_days_off_per_week 1 \
  --direct_deterministic --params max_time_in_seconds:0.5
```
- `--men 7 --women 8`: roster = 7 men + 8 women (15 total)
- `--direct_base_M_weekday 5,6,5,5,6`: Morning demand Mon–Fri
- `--direct_base_A_weekday 4,5,4,4,5`: Afternoon demand Mon–Fri
- `--direct_base_M_weekend 4,3`: Morning demand Sat, Sun
- `--direct_base_A_weekend 3,3`: Afternoon demand Sat, Sun
- `--max_shifts_per_week 6`: max 6 working days per week
- `--min_days_off_per_week 1`: min 1 rest day per week
- `--direct_deterministic`: use demand values exactly (no random ±1)

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
Single int = all days in group. Comma-sep = per-day: `--direct_base_M_weekday 5,6,5,5,6` = Mon–Fri, `--direct_base_M_weekend 4,3` = Sat,Sun.
```bash
python store_optimizer_demo.py --direct_base_M_weekday 6 --direct_base_A_weekday 5 --direct_base_M_weekend 5 --direct_base_A_weekend 4 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --direct_base_M_weekday 5,6,5,5,6 --direct_base_M_weekend 4,3 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --direct_deterministic --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand_profile weekend_heavy --params max_time_in_seconds:0.5
python store_optimizer_demo.py --demand_profile weekday_heavy --params max_time_in_seconds:0.5
python store_optimizer_demo.py --store_ids A,B --store_profiles A:weekend_heavy,B:weekday_heavy --params max_time_in_seconds:0.5
```

### Optimizer constraints
Defaults: `--min_days_off_per_week 1`, `--max_shifts_per_week 6`, `--max_consecutive_work_days 5`, `--min_sunday_off_per_month 1`, `--min_sunday_off_women 2`, `--women_sunday_off_alternate`, `--spread_sunday_shifts_penalty 10`, `--max_weekend_work_shifts_women None`. Use `0` to disable.
```bash
python store_optimizer_demo.py --excess_penalty_M 2 --excess_penalty_A 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_shifts_per_week 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_days_off_per_week 1 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_consecutive_work_days 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_days_off_per_week 0 --max_shifts_per_week 0 --max_consecutive_work_days 0 --params max_time_in_seconds:0.5  # disable all
python store_optimizer_demo.py --sequence_constraints M:1,1,0,3,4,5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --weekly_sum_constraints O:1,2,7,2,3,4 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_weekend_work_shifts_women 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_sunday_off_per_month 1 --min_sunday_off_women 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --no_women_sunday_off_alternate --params max_time_in_seconds:0.5
python store_optimizer_demo.py --spread_sunday_shifts_penalty 10 --params max_time_in_seconds:0.5
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
