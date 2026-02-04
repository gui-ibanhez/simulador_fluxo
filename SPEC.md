# Workforce Management – Spec

## Goals

1. **Estimate how many employees are needed per store** based on customer flow (e.g. customers per hour per store).
2. **Schedule the workforce** so that each store has enough staff in each time period, respecting constraints (availability, max hours, etc.).

---

## Part 1: Staffing estimation (customer flow → required employees)

### Inputs

- **Customer flow**: number of customers per store per time period (e.g. hourly or per shift).
  - Example: `{ "store_A": [120, 80, 200, ... ], "store_B": [50, 60, ... ] }` for each hour or shift.
- **Staffing rule**: how to convert flow into required employees.
  - Option A: **Ratio** – e.g. 1 employee per N customers (per period), with a minimum (e.g. at least 1).
  - Option B: **Tiers** – e.g. 0–50 customers → 1, 51–100 → 2, 101–200 → 3, etc.
  - Option C: **Formula** – e.g. `ceil(customers / N) + base`, with configurable N and base.

### Outputs

- **Required employees per store per time period**: e.g. `{ ("store_A", "Mon-09"): 3, ("store_A", "Mon-10"): 4, ... }`.
- Same structure can represent “shifts” if each period is a shift (e.g. Morning/Afternoon/Evening).

### Assumptions

- One time granularity for the whole system (e.g. all periods are 1 hour or all are “shift”).
- Minimum 1 employee per store per period when there is any customer flow (configurable).

---

## Part 2: Workforce scheduling (requirements → assignments)

### Inputs

- **Demand**: required number of employees per (store, shift) from Part 1.
- **Employees**: list of employees, each with:
  - Optional: **availability** per (store, shift) – which (store, shift) they can work.
  - Optional: **max shifts per week** (or per period).
  - Optional: **cost** or **preference** per (employee, store, shift) for the objective.
- **Shifts**: list of shift IDs that align with the periods used in Part 1 (e.g. same time slots).

### Constraints

- For each (store, shift): **assigned employees ≥ required** (demand from Part 1).
- Each employee is assigned at most once per shift (no double-booking in the same shift).
- Optional: per-employee max number of shifts (e.g. max 5 per week).
- Optional: only assign where availability is True.
- See **SYSTEM_OVERVIEW.md §4.5** for how each constraint can cause infeasibility and remedies.

### Objective

- Minimize total cost (if cost per assignment is given), or
- Minimize total assignments (use fewest people), or
- Minimize understaffing (penalize gaps between assigned and required).

### Outputs

- **Assignments**: list of (employee, store, shift) meaning “this employee works this store in this shift”.
- Summary: employees used per store, per shift, and total.

---

## Data flow

1. **Customer flow** (per store, per period)  
   → **Staffing estimation**  
   → **Demand** (required employees per store, per shift/period)

2. **Demand** + **Employees** + **Shifts**  
   → **Scheduler (OR-Tools)**  
   → **Assignments** (who works where and when)

---

## Implementation notes

- **Estimation**: pure Python; configurable rule (ratio, tiers, or formula) in one module.
- **Scheduling**: OR-Tools CP-SAT; variables `x[employee, store, shift]`; constraints as above; objective as chosen.
- **Formats**: use dicts/lists and clear naming; optional CSV/JSON I/O can be added later without changing this spec.
