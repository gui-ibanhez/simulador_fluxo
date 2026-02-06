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
python store_optimizer_demo.py --store_shifts "store_A:6,store_B:3" --demand direct --direct_base_by_day 4 --direct_deterministic --params max_time_in_seconds:0.5
python store_optimizer_demo.py --current_employees 12 --min_employees 10 --max_employees 15 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --seed 42 --params max_time_in_seconds:0.5
```

### Roster file and schedule I/O
Load roster from JSON or CSV (overrides `--men`/`--women`). Export/import schedules for month-to-month continuity.
```bash
python store_optimizer_demo.py --roster_file roster.json --direct_base_M_weekday 2 --direct_base_A_weekday 2 --direct_deterministic --params max_time_in_seconds:0.5
python store_optimizer_demo.py --roster_file roster.csv --direct_base_M_weekday 2 --direct_base_A_weekday 2 --params max_time_in_seconds:0.5
```
- JSON: `[{"id": "E001", "gender": "M"}, ...]`
- CSV: header `id,gender` required

Export schedule for use as previous month:
```bash
python store_optimizer_demo.py --roster_file roster.json --output_schedule schedule_jan.json --year 2025 --month 1 --direct_base_by_day 1 --direct_deterministic --params max_time_in_seconds:0.5
```

Condition next month on previous schedule (soft stability + sequence continuity):
```bash
python store_optimizer_demo.py --roster_file roster.json --previous_schedule schedule_jan.json --year 2025 --month 2 --direct_base_by_day 1 --direct_deterministic --params max_time_in_seconds:0.5
python store_optimizer_demo.py --previous_schedule schedule_jan.json --stability_penalty 2 --params max_time_in_seconds:0.5
```
- `--previous_schedule`: JSON with `employee_ids`, `schedule`, `shifts`, `year`, `month`. Shifts must match current store.
- `--stability_penalty`: weight for deviating from previous (default 0 = disabled). Use e.g. 1 or 2 to enable.

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

### Dynamic shifts (variable shifts per store)
Use `--store_shifts` to set different shift counts per store (e.g. 3, 6, or 8 shifts). Omit for default M/A (2 shifts).
```bash
python store_optimizer_demo.py --store_ids store_A --store_shifts "store_A:6" --demand direct --direct_base_by_day "5,4,3,4,5,4;6,5,4,5,6,5;5,4,3,4,5,4;5,4,3,4,5,4;6,5,4,5,6,5;4,3,3,3,4,3;3,3,3,3,4,3" --direct_deterministic --excess_penalty 1 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --store_ids store_A,store_B --store_shifts "store_A:6,store_B:3" --demand direct --direct_base_by_day 4 --direct_deterministic --params max_time_in_seconds:0.5
python store_optimizer_demo.py --store_ids store_A --store_shifts "store_A:6" --demand estimation --rule ratio --customers_per_employee 30 --params max_time_in_seconds:0.5
```
- `--direct_base_by_day`: 7 groups (Mon–Sun) separated by `;`; each group has N comma-sep values (S1..Sn). Single int `4` = same for all.
- `--excess_penalty`: single int or comma-sep per shift (when `--store_shifts`).
- `--customers_per_employee`: single float or comma-sep per shift (when `--store_shifts`).
- `--sequence_constraints`, `--weekly_sum_constraints`: use shift names S1, S2, …, O.

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
Defaults: `--min_days_off_per_week 1`, `--max_shifts_per_week 6`, `--max_consecutive_work_days 5`, `--max_consecutive_off_days None`, `--min_sunday_off_per_month 1`, `--min_sunday_off_women 2`, `--women_sunday_off_alternate`, `--spread_sunday_shifts_penalty 10`, `--spread_shifts_penalty 0`, `--max_weekend_work_shifts_women None`, `--require_consecutive_off` disabled, `--minimize_off_days_penalty 1`. Use `0` to disable numeric constraints.
- `--require_consecutive_off`: When enabled, enforces that `min_days_off_per_week` must be consecutive (not scattered). Only applies when min_days_off >= 2. Uses rolling 7-day windows with automaton constraints.
- `--minimize_off_days_penalty`: Penalty per off day for Solution 1 (actual roster). When > 0, the solver minimizes days off to maximize utilization. Does NOT affect Solution 2 (optimized roster search). Default 1, set to 0 to disable.
```bash
python store_optimizer_demo.py --excess_penalty_M 2 --excess_penalty_A 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_shifts_per_week 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_days_off_per_week 1 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_consecutive_work_days 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_consecutive_off_days 2 --params max_time_in_seconds:0.5  # max 2 consecutive off days (e.g., Sat-Sun ok, Fri-Sat-Sun not ok)
python store_optimizer_demo.py --min_days_off_per_week 0 --max_shifts_per_week 0 --max_consecutive_work_days 0 --params max_time_in_seconds:0.5  # disable all
python store_optimizer_demo.py --max_shifts_per_week 5 --min_days_off_per_week 2 --require_consecutive_off --params max_time_in_seconds:0.5  # 2 days off must be consecutive
python store_optimizer_demo.py --sequence_constraints M:1,1,0,3,4,5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --weekly_sum_constraints O:1,2,7,2,3,4 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --max_weekend_work_shifts_women 2 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_sunday_off_per_month 1 --min_sunday_off_women 2 --params max_time_in_seconds:0.5
# When min_sunday_off_women > 0 and women_sunday_off_alternate: validation runs automatically after each schedule. Look for "Validation: women alternate Sundays OK" or "CONSTRAINT VIOLATIONS".
python store_optimizer_demo.py --no_women_sunday_off_alternate --params max_time_in_seconds:0.5
python store_optimizer_demo.py --spread_sunday_shifts_penalty 10 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --spread_shifts_penalty 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --min_work_days_per_month 15 --min_work_days_penalty 5 --params max_time_in_seconds:0.5
python store_optimizer_demo.py --minimize_off_days_penalty 1 --params max_time_in_seconds:0.5  # Solution 1 maximizes utilization (default)
python store_optimizer_demo.py --minimize_off_days_penalty 0 --params max_time_in_seconds:0.5  # Disable: Solution 1 uses same objective as Solution 2
python store_optimizer_demo.py --target_min_women_ratio 0.35 --target_max_women_ratio 0.65 --params max_time_in_seconds:0.5
```

### Fixed shift constraint
Each employee always works the same shift (e.g., assigned to S1, they only work S1 on all working days).
```bash
# Solver picks which shift each employee is assigned to
python store_optimizer_demo.py --store_shifts "store:3" --fixed_shift model --direct_base_by_day "3,3,3" --direct_deterministic --current_employees 9 --params max_time_in_seconds:0.5

# Roster defines each employee's shift (requires 'shift' field in roster JSON)
python store_optimizer_demo.py --store_shifts "store:3" --roster_file tests/fixtures/roster_with_shift.json --fixed_shift roster --direct_base_by_day "2,2,2" --direct_deterministic --params max_time_in_seconds:0.5
```
- `--fixed_shift off`: disabled (default) – employees can work any shift
- `--fixed_shift model`: solver picks one shift per employee
- `--fixed_shift roster`: shift pre-defined in roster JSON (`{"id": "E001", "shift": "S1"}`)

### Skip optimization (Solution 2)
By default, the optimizer generates two solutions: Solution 1 (actual roster schedule) and Solution 2 (optimized roster size search). Use `--skip_optimization` to only generate Solution 1:
```bash
python store_optimizer_demo.py --skip_optimization --params max_time_in_seconds:0.5  # Only Solution 1, no roster search
```
This is useful when you only need to generate a schedule for your current employees and don't need the optimization analysis.

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
