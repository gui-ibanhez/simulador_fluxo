import datetime as dt
from store_staffing_optimizer import solve_once, build_complete_week_dates, status_name
from ortools.sat.python import cp_model

# February 2026 starts on a Sunday. 
# build_complete_week_dates will return 5 weeks (Jan 26 - Mar 1).
dates, primary_start, primary_end = build_complete_week_dates(2026, 2)
print(f"Extended range: {dates[0]} to {dates[-1]} ({len(dates)} days)")

shifts = ["O", "S1", "S2", "S3"]
# Demand for all days, simplified
demand = [{"S1": 1, "S2": 1, "S3": 1} for _ in dates]
constraints = {
    "min_days_off_per_week": 2,
    "max_shifts_per_week": 5,
}
roster = [{"id": f"emp_{i}", "gender": "M"} for i in range(10)]

try:
    result = solve_once(10, dates, shifts, demand, constraints, "max_time_in_seconds:3.0", "", False, roster)
    print("Status:", status_name(result["status"]))
    if result["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("SUCCESS: Infeasibility fixed by complete week scheduling!")
    else:
        print("FAILURE: Still infeasible.")
except Exception as e:
    print("Error:", e)
