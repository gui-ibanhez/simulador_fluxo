# Manual Test Commands for New Features

Run from project root: `cd /home/gui/Desktop/simulador_fluxo`

---

## 1. Roster file (JSON)

```bash
python store_optimizer_demo.py \
  --roster_file test_data/roster.json \
  --direct_base_M_weekday 2 --direct_base_A_weekday 2 \
  --direct_base_M_weekend 1 --direct_base_A_weekend 1 \
  --direct_deterministic \
  --params max_time_in_seconds:0.5
```

## 2. Roster file (CSV)

```bash
python store_optimizer_demo.py \
  --roster_file test_data/roster.csv \
  --direct_base_M_weekday 2 --direct_base_A_weekday 2 \
  --direct_deterministic \
  --params max_time_in_seconds:0.5
```

## 3. Export schedule to JSON

```bash
python store_optimizer_demo.py \
  --roster_file test_data/roster.json \
  --direct_base_M_weekday 2 --direct_base_A_weekday 2 \
  --direct_base_M_weekend 1 --direct_base_A_weekend 1 \
  --direct_deterministic \
  --output_schedule test_data/schedule_feb_2025.json \
  --year 2025 --month 2 \
  --params max_time_in_seconds:0.5
```

## 4. Condition February on January schedule (previous_schedule)

```bash
python store_optimizer_demo.py \
  --roster_file test_data/roster.json \
  --previous_schedule test_data/schedule_jan_2025_3shifts.json \
  --store_shifts store:3 \
  --direct_base_by_day "1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1" \
  --direct_deterministic \
  --year 2025 --month 2 \
  --min_sunday_off_per_month 0 --min_sunday_off_women 0 \
  --params max_time_in_seconds:2.0
```

## 5. Full flow: export Jan → use as previous for Feb

```bash
# Step 1: Generate January schedule
python store_optimizer_demo.py \
  --roster_file test_data/roster.json \
  --store_shifts store:3 \
  --direct_base_by_day "1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1" \
  --direct_deterministic \
  --output_schedule test_data/schedule_jan.json \
  --year 2025 --month 1 \
  --min_sunday_off_per_month 0 --min_sunday_off_women 0 \
  --params max_time_in_seconds:2.0

# Step 2: Use it for February
python store_optimizer_demo.py \
  --roster_file test_data/roster.json \
  --previous_schedule test_data/schedule_jan.json \
  --store_shifts store:3 \
  --direct_base_by_day "1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1" \
  --direct_deterministic \
  --year 2025 --month 2 \
  --min_sunday_off_per_month 0 --min_sunday_off_women 0 \
  --params max_time_in_seconds:2.0
```

## 6. 6-shift store with fixture schedule

```bash
python store_optimizer_demo.py \
  --store_shifts store:6 \
  --roster_file test_data/roster_18.json \
  --max_employees 20 \
  --previous_schedule test_data/schedule_jan_2025_6shifts.json \
  --year 2025 --month 2 \
  --direct_base_by_day "1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1" \
  --direct_deterministic \
  --min_sunday_off_per_month 0 --min_sunday_off_women 0 \
  --params max_time_in_seconds:3.0
```

## 7. Stability penalty (stronger preference for previous schedule)

```bash
python store_optimizer_demo.py \
  --roster_file test_data/roster.json \
  --previous_schedule test_data/schedule_jan_2025_3shifts.json \
  --store_shifts store:3 \
  --stability_penalty 5 \
  --direct_base_by_day "1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1" \
  --direct_deterministic \
  --year 2025 --month 2 \
  --min_sunday_off_per_month 0 --min_sunday_off_women 0 \
  --params max_time_in_seconds:2.0
```

---

## Run automated tests

```bash
python -m pytest tests/test_cli_demo.py -v
```

Or only the roster/schedule tests:

```bash
python -m pytest tests/test_cli_demo.py::TestDemoRosterAndPreviousSchedule -v
```
