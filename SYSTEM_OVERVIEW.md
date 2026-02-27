# Workforce Management System – Overview

This document describes how the system works: how demand (required employees per shift) is defined, how it is calculated from customer flow when using estimation, and how the scheduler assigns people and chooses roster size. It is written so that someone who does not read code but is comfortable with math can follow the full logic.

---

## How to make the system work

**Prerequisite**: Python is already installed on your machine (e.g. Python 3.10 or newer).

### Step 1: Install uv

**uv** is a fast tool to manage Python environments and install packages. Install it with:

- **Windows** (PowerShell):  
  `irm https://astral.sh/uv/install.ps1 | iex`

- **macOS / Linux**:  
  `curl -LsSf https://astral.sh/uv/install.sh | sh`

After installation, close and reopen your terminal (or restart your shell) so the `uv` command is available.

### Step 2: Create a virtual environment

Open a terminal, go to the folder where you extracted the zip (the one that contains `requirements.txt` and the three scripts), then run:

```text
uv venv
```

This creates a folder named `.venv` in the current directory. All dependencies will be installed inside this environment, so they do not affect the rest of your system.

### Step 3: Install the requirements

Still in the same folder, run:

```text
uv pip install -r requirements.txt
```

This installs the packages listed in `requirements.txt` (including OR-Tools, the solver used by the system) into the `.venv` you just created.

### Step 4: Run the system

You can run the system in two ways.

**Option A – Standalone optimizer** (demand set by command-line bases):

```text
uv run python store_staffing_optimizer.py --year=2025 --month=1 --current_employees=10 --min_employees=8 --max_employees=14
```

Optional: change demand bases, e.g.  
`--demand_base_M_weekday=6 --demand_base_A_weekday=5 --demand_base_M_weekend=4 --demand_base_A_weekend=3`

**Option B – Demo** (more options: direct fake demand or demand from customer-flow estimation):

```text
uv run python store_optimizer_demo.py --year=2025 --month=1 --demand=direct
```

For demand from estimation:  
`uv run python store_optimizer_demo.py --year=2025 --month=1 --demand=estimation --rule=ratio`

Use `--help` to see all parameters:

```text
uv run python store_staffing_optimizer.py --help
uv run python store_optimizer_demo.py --help
```

**Note**: `uv run` automatically uses the `.venv` in the current folder and runs Python with the packages you installed. You do not need to “activate” the virtual environment by hand.

---

## System overview: how everything is calculated

### 1. What the system does (inputs and outputs)

**Inputs**

- **Time horizon**: one month (year + month); we get a list of days \(d = 0, 1, \ldots, D-1\).
- **Shifts**: for example **O** (Off), **M** (Morning), **A** (Afternoon). Work shifts are M and A; O means the person does not work that day. With `--store_shifts`, each store can have a different number of work shifts (e.g. S1, S2, …, S6 for 6 shifts).
- **Demand**: for each day \(d\) and each work shift \(s\) (e.g. M, A), a number \(r_{d,s}\) = *required* number of employees that must be assigned to that shift on that day.
- **Roster bounds**: current roster size \(N_{\text{current}}\), and a range \([N_{\min}, N_{\max}]\) of roster sizes we are allowed to try (e.g. to recommend hiring or reducing).

**Outputs**

- A **schedule**: for each employee \(e\) and each day \(d\), which shift they work (O, M, or A).
- A **recommended roster size** \(N^*\) in \([N_{\min}, N_{\max}]\) that meets demand and minimizes the objective (and, if tied, stays closest to \(N_{\text{current}}\)).

So the system does two things: (1) it can *compute* demand \(r_{d,s}\) from customer flow (estimation); (2) it *assigns* employees to shifts so that every \((d,s)\) has at least \(r_{d,s}\) people, and it chooses the best roster size in the allowed range.

---

### 2. Where demand \(r_{d,s}\) comes from (two ways)

Demand is *required number of employees per day per shift*. It can be set in two ways.

#### 2.1 Direct demand (no customer flow)

We set \(r_{d,s}\) directly, possibly depending on weekday vs weekend.

- **Parameters**: four numbers — base required for Morning and Afternoon on *weekdays*, and base for Morning and Afternoon on *weekends* (e.g. \(b^{\text{M}}_{\text{wd}}, b^{\text{A}}_{\text{wd}}, b^{\text{M}}_{\text{we}}, b^{\text{A}}_{\text{we}}\)).
- **Rule**: For each day \(d\), if \(d\) is a weekday (Monday–Friday), then  
  \(r_{d,\text{M}} = b^{\text{M}}_{\text{wd}},\quad r_{d,\text{A}} = b^{\text{A}}_{\text{wd}}\).  
  If \(d\) is a weekend day,  
  \(r_{d,\text{M}} = b^{\text{M}}_{\text{we}},\quad r_{d,\text{A}} = b^{\text{A}}_{\text{we}}\).

In the demo, “fake direct” demand can add a random variation: \(r_{d,s} = \max\bigl(1,\, b_s + \varepsilon\bigr)\) with \(\varepsilon \in \{-1,0,1\}\) (and \(b_s\) from weekday/weekend as above). So the *logic* is: base per shift and weekday/weekend; optionally a small random change.

#### 2.2 Demand from customer flow (estimation)

We start from **customer flow**: for each “period” (e.g. each day–shift pair), we have a number of customers \(C\). A **staffing rule** turns \(C\) into required employees \(r\) for that period. Then we map periods back to \((d,s)\) to get \(r_{d,s}\).

- **Period index**: Periods are ordered, e.g. day 0 Morning, day 0 Afternoon, day 1 Morning, … So period index \(p = d \cdot (\text{number of work shifts}) + \text{shift index}\).
- **Input**: \(C_{\text{store},p}\) = customers in that store in that period (e.g. from historical data or a fake generator).
- **Output**: \(r_{\text{store},p}\) = required employees for that store in that period. Then we convert to demand per day and shift: \(r_{d,s}\) for that store.

The three **staffing rules** are defined next; they are the only place where “how many employees per shift” is *calculated* from customers.

---

### 3. Staffing rules (customer flow → required employees)

For one period, we have **customers** \(C \ge 0\) and we compute **required employees** \(r \in \mathbb{Z}_{\ge 0}\). Notation: \(\lceil x \rceil\) = smallest integer \(\ge x\).

#### 3.1 Ratio rule

**Parameters**: \(N\) = customers per employee (e.g. 30), \(r_{\min}\) = minimum employees (e.g. 1).

**Formula**:
\[
r = \max\left\{\, r_{\min},\; \left\lceil \frac{C}{N} \right\rceil \,\right\},\qquad \text{with } N \ge 1.
\]
If \(C \le 0\), we set \(r = 0\). So we take the ceiling of \(C/N\) and never go below \(r_{\min}\). Different shifts (e.g. M and A) can have different \(N\) (e.g. \(N_{\text{M}}, N_{\text{A}}\)).

#### 3.2 Tiers rule

**Parameters**: A list of tiers \((C_1, r_1), (C_2, r_2), \ldots, (C_K, r_K)\) with \(C_1 < C_2 < \cdots < C_K\) (and often \(C_K = +\infty\)).

**Rule**: Find the first tier \(k\) such that \(C \le C_k\). Then \(r = r_k\). If \(C\) is above all \(C_k\), use the last tier’s \(r_K\).

Example: \((50, 1), (100, 2), (+\infty, 3)\) means:
- \(0 \le C \le 50 \Rightarrow r = 1\)
- \(51 \le C \le 100 \Rightarrow r = 2\)
- \(C > 100 \Rightarrow r = 3\)

So “required employees per shift” is a step function of customer count.

#### 3.3 Formula rule

**Parameters**: \(N\) = customers per employee, \(r_{\text{base}}\) = base employees, \(r_{\min}\) = minimum employees.

**Formula**:
\[
r = \max\left\{\, r_{\min},\; r_{\text{base}} + \left\lceil \frac{C}{N} \right\rceil \,\right\},\qquad N \ge 1.
\]
If \(C \le 0\), \(r = \max\{r_{\min}, r_{\text{base}}\}\). So we add a fixed base to the same “ceiling of \(C/N\)” as in the ratio rule, then enforce the minimum.

---

### 4. Scheduling model (how assignments and roster size are chosen)

The scheduler receives demand \(r_{d,s}\) for each day \(d\) and work shift \(s\), and a roster size \(N\) (number of employees). It decides *who* works *which* shift each day.

#### 4.1 Decision variables

- \(x_{e,s,d} \in \{0,1\}\): 1 if employee \(e\) works shift \(s\) on day \(d\), 0 otherwise.  
  Indices: \(e = 0,\ldots,N-1\); \(s \in \{\text{O}, \text{M}, \text{A}\}\); \(d = 0,\ldots,D-1\).

#### 4.2 Hard constraints

1. **Exactly one shift per day per employee**: For each \(e\) and \(d\),
   \[
   \sum_{s} x_{e,s,d} = 1.
   \]
   So each person is either Off, Morning, or Afternoon each day.

2. **Cover (no understaffing)**: For each day \(d\) and each *work* shift \(s\) (M, A),
   \[
   \sum_{e=0}^{N-1} x_{e,s,d} \ge r_{d,s}.
   \]
   The number of people assigned to shift \(s\) on day \(d\) must be at least the required \(r_{d,s}\). If \(r_{d,s} > N\) for some \((d,s)\), the problem is infeasible for that roster size (we need more than \(N\) people that day).
   
   **Special case: demand = 0** (shift closed): When \(r_{d,s} = 0\), no one can be assigned to shift \(s\) on day \(d\). This is used when a shift doesn't exist on a particular day (e.g., store closed on Sundays for certain shifts). The constraint becomes:
   \[
   \sum_{e=0}^{N-1} x_{e,s,d} = 0.
   \]

3. **Optional policy constraints** (if enabled): e.g. max working shifts per week per employee, min days off per week, max consecutive work days. These are linear or logical constraints on the \(x_{e,s,d}\).

#### 4.3 Objective (what we minimize)

The system produces two solutions with **different objectives**:

**Solution 1 (actual roster)**: Minimizes days off to maximize utilization.
- Adds penalty for each off day per employee: \(\sum_e \text{off\_days}_e \cdot w_{\text{off}}\)
- Effect: if demand is 3 for shift S1 but 5 people can work it, the solver prefers assigning all 5
- This is the schedule you'll actually use; everyone works as much as possible within constraints
- Controlled by `--minimize_off_days_penalty` (default 1, set to 0 to disable)

**Solution 2 (optimized roster)**: Minimizes excess staffing for workforce planning.
- **Excess cover**: For each \((d,s)\), define *assigned* \(a_{d,s} = \sum_e x_{e,s,d}\). We require \(a_{d,s} \ge r_{d,s}\). If we assign *more* than required, we penalize the excess. So we have penalty coefficients \(w_s\) (e.g. per shift) and
  \[
  \text{excess}_{d,s} = \max\bigl(0,\; a_{d,s} - r_{d,s}\bigr),
  \]
  and the objective includes \(w_{\text{M}} \cdot \text{excess}_{d,\text{M}} + w_{\text{A}} \cdot \text{excess}_{d,\text{A}}\) (summed over days). So the solver tries to meet demand exactly when it can, and otherwise minimizes overstaffing.
- Use this to determine if you need to hire, reduce, or transfer employees between stores

Both solutions respect all hard constraints (max shifts per week, min days off, etc.). Other soft constraints (e.g. sequence or weekly sum preferences, spread of Sunday shifts, spread of total shifts across roster) apply to both. **Total objective** = sum of all penalty terms. We **minimize** it.

#### 4.4 How the “best” roster size is chosen (iterative hire)

When the current roster is **understaffed** (no feasible schedule), the demo uses an **iterative hire** approach:

1. Check the current gender ratio (men vs women).
2. Hire **one** person: a woman if there are too few women, a man if there are too few men (relative to target ratio).
3. Re-run the optimization with the new roster.
4. If still infeasible and roster size \(< N_{\max}\), repeat from step 1.
5. Stop when a feasible schedule is found or \(N_{\max}\) is reached.

This ensures each hire is chosen to maintain gender balance, rather than hiring all at once.

When the current roster is **feasible**, we use it as-is (no hire search).

So the “quantity of employees per shift” is **never** decided by the scheduler: it is entirely given by the demand \(r_{d,s}\). The scheduler only decides *how many employees in total* to have (\(N^*\)) and *who works when*, so that every \(r_{d,s}\) is met and the total penalty is minimized.

#### 4.5 When the model is infeasible

Each constraint can make the optimization infeasible. Below: what causes infeasibility and how to fix it.

| Constraint | Infeasibility condition | Remedy |
|------------|-------------------------|--------|
| **Cover** | For some day \(d\) and shift \(s\), demand \(r_{d,s}\) exceeds roster size \(N\). Example: day requires 6 morning + 5 afternoon = 11 people, but \(N = 10\). | Increase roster (hire) or reduce demand. Solution 1 uses relaxed model for a best-effort schedule with current roster. |
| **Max shifts per week** | Total demand for work shifts in a week exceeds \(N \times \text{max\_shifts\_per\_week}\). Example: 8 people × 5 max = 40 shifts available, but demand needs 45. | Raise `max_shifts_per_week`, hire more people, or reduce demand. |
| **Min days off per week** | Each employee needs at least \(k\) days off per week (Monday–Sunday). Partial weeks at month boundaries are completed with the previous month when `--previous_schedule` is used. Default \(k=1\); use 0 to disable. | Lower `min_days_off_per_week`, hire more, or reduce demand. |
| **Min Sunday off per month** | Each employee must have at least \(k\) Sundays off in the primary month. Default \(k=1\); use 0 to disable. If demand forces too many people to work Sundays, the model becomes infeasible. | Lower `min_sunday_off_per_month`, hire more people, or reduce Sunday demand. |
| **Max consecutive work days** | Demand pattern forces someone to work more than the limit in a row. Example: demand requires the same 5 people every day for 10 days, but max consecutive = 5. | Raise `max_consecutive_work_days`, hire more (spread load), or reduce demand. |
| **Max consecutive off days** | Demand pattern leaves someone with too many consecutive off days. Example: low demand on weekends forces 3+ consecutive off days but max consecutive off = 2. | Raise `max_consecutive_off_days`, reduce demand elsewhere, or disable with 0. |
| **Women's weekend cap** | Weekend demand exceeds total weekend shifts women can provide plus men's capacity. Example: 2 women × 4 max = 8 weekend shifts; need 20 total; 12 must come from men; if only 3 men, infeasible. | Hire more men, raise `max_weekend_work_shifts_women`, or reduce weekend demand. |
| **Sequence constraints** (hard part) | Hard min/max on consecutive work days cannot be satisfied. Example: demand forces 6 consecutive M shifts but hard_max = 5. | Relax hard bounds or adjust demand. |
| **Weekly sum constraints** (hard part) | Hard min/max on shifts per week per employee conflicts with cover. Example: demand forces 5 M shifts in a week but hard_max = 4. | Relax hard bounds, hire more, or reduce demand. |

**Legal rule (women alternate Sundays)**: When `women_sunday_off_alternate` is enabled (default), no woman may be off on two consecutive Sundays. This is enforced as a hard constraint in the model. After solving, the schedule is validated; if any violation is found, the run exits with error and no schedule is output.

---

### 5. End-to-end flow (summary)

1. **Demand**  
   - Either set directly (e.g. weekday/weekend bases \(b^{\text{M}}_{\text{wd}}, b^{\text{A}}_{\text{wd}}, b^{\text{M}}_{\text{we}}, b^{\text{A}}_{\text{we}}\)),  
   - Or compute from customer flow: for each period, apply one of the three rules (ratio / tiers / formula) to get required employees, then map to \(r_{d,s}\).

2. **Validation**  
   Check that for every day \(d\) we have \(r_{d,\text{M}}\) and \(r_{d,\text{A}}\) (and that no single day requires more people than the maximum roster we will try).

3. **Scheduling**  
   For each candidate roster size \(N\), solve the CP model (cover + optional constraints, minimize objective). Keep the best \(N^*\) and the corresponding schedule.

4. **Output**  
   Each run prints two solutions: Solution 1 (current roster) and Solution 2 (optimized). Use `--skip_optimization` to only generate Solution 1 (skips the roster search). Schedule (who works which shift each day) and recommendation (e.g. “hire \(N^* - N_{\text{current}}\)” or “reduce by \(N_{\text{current}} - N^*\)”).

---

## Implementation: modules and CLI

### 6. System at a glance (software)

The system is split into three parts:

1. **staffing_estimation.py**: Customer flow → required employees per (store, period). Implements the three rules (ratio, tiers, formula) with the exact formulas above.
2. **store_staffing_optimizer.py**: Demand \(r_{d,s}\) + roster size \(N\) + constraints → CP-SAT model → schedule. Can loop over \(N \in [N_{\min}, N_{\max}]\) and return best roster size and schedule.
3. **store_optimizer_demo.py**: Builds demand (direct or from estimation), calls the optimizer, exposes parameters via CLI.

```text
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│  staffing_          │     │  store_optimizer_   │     │  store_staffing_    │
│  estimation.py      │────▶│  demo.py             │────▶│  optimizer.py       │
│                     │     │                     │     │                     │
│  Customer flow      │     │  Demand (per day,   │     │  Schedule +         │
│  → Required         │     │  per shift) +        │     │  best roster size   │
│  employees          │     │  constraints + CLI  │     │  (CP-SAT)           │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
```

---

### 7. staffing_estimation.py

**Role**: Implement the three rules that convert customers \(C\) into required employees \(r\) for each (store, period).

- **Input**: `customer_flow[store_id]` = list of customer counts per period (e.g. day0_M, day0_A, day1_M, …).  
  Optional: `period_to_shift(period_ix)` so we can use different parameters per shift (e.g. \(N_{\text{M}}, N_{\text{A}}\)).
- **Output**: `(store_id, period_ix) → required_employees`.
- **Rules**: ratio, tiers, formula — as in §3. Per-shift ratios use a dict (e.g. `{"M": 15, "A": 25}`) and `period_to_shift`.

---

### 8. store_staffing_optimizer.py

**Role**: Build and solve the scheduling model for one store and one month.

- **Inputs**: `num_employees` \(N\), `dates`, `shifts` (e.g. O, M, A), `demand` = list of dicts \(r_{d,s}\), `constraints` (excess penalties, optional sequence/weekly/max shifts/min days off/max consecutive work).
- **Model**: Variables \(x_{e,s,d}\); exactly one shift per day per employee; cover \(\sum_e x_{e,s,d} \ge r_{d,s}\); objective = sum of excess cover penalties (and any other soft penalties).
- **Entry points**:  
  - `solve_once(...)`: solve for a fixed \(N\).  
  - `solve_store(...)`: solve for current roster, then loop \(N\) from min to max and return best schedule and roster size.

**Demand in the optimizer**: When run from the command line, demand is built by `default_demand(dates, base_m_weekday, base_a_weekday, base_m_weekend, base_a_weekend)` — same weekday/weekend base logic as in §2.1. These four bases can be set via CLI: `--demand_base_M_weekday`, `--demand_base_A_weekday`, `--demand_base_M_weekend`, `--demand_base_A_weekend`.

---

### 9. store_optimizer_demo.py

**Role**: Example app that (a) builds demand (direct fake or from estimation), (b) calls the optimizer once per store, (c) exposes all parameters via CLI.

**Demand sources**:

1. **Direct** (`--demand=direct`): Uses `make_fake_demand(...)` with bases for weekday and weekend (and optional random ±1). Bases can be passed as four parameters: `base_M_weekday`, `base_A_weekday`, `base_M_weekend`, `base_A_weekend`, or as two pairs `base_weekday=(M,A)`, `base_weekend=(M,A)`. CLI: `--direct_base_M_weekday`, `--direct_base_A_weekday`, `--direct_base_M_weekend`, `--direct_base_A_weekend`.
2. **Estimation** (`--demand=estimation`): Generates fake customer flow, then calls `estimate_required_employees` (ratio/tiers/formula) and converts the result to demand per day/shift. CLI: `--rule`, `--customers_per_employee_M`, `--customers_per_employee_A`, `--tiers`, `--base_employees`, `--min_employees_estimation`, etc.

**Flow**: Parse CLI → build dates and demand → validate → for each store, call optimizer (current roster then search over \(N\)) → print schedule and hire/reduce recommendation.

---

### 10. CLI summary

**store_staffing_optimizer.py** (standalone):

- Time: `--year`, `--month`
- Roster: `--current_employees`, `--min_employees`, `--max_employees`
- Demand (default_demand): `--demand_base_M_weekday`, `--demand_base_A_weekday`, `--demand_base_M_weekend`, `--demand_base_A_weekend`
- Solver: `--params`, `--output_proto`

**store_optimizer_demo.py**:

- Same time/roster plus `--store_ids`, `--seed`
- Demand source: `--demand` (direct | estimation), `--rule` (ratio | tiers | formula)
- Direct bases: `--direct_base_M_weekday`, `--direct_base_A_weekday`, `--direct_base_M_weekend`, `--direct_base_A_weekend`
- Estimation: `--customers_per_employee_M`, `--customers_per_employee_A`, `--min_employees_estimation`, `--base_employees`, `--tiers`
- Constraints: `--excess_penalty_M`, `--excess_penalty_A`, `--max_shifts_per_week`, `--min_days_off_per_week`, `--max_consecutive_work_days`
- Solver: `--params`, `--output_proto`

Run `python store_staffing_optimizer.py --help` or `python store_optimizer_demo.py --help` for the full list.

---

### 11. File summary

| File | Purpose |
|------|--------|
| **staffing_estimation.py** | Customer flow → required employees per (store, period). Implements ratio, tiers, and formula rules (§3). Supports per-shift parameters via `period_to_shift` and dicts. |
| **store_staffing_optimizer.py** | Demand \(r_{d,s}\) + roster size + constraints → CP-SAT model → schedule. Searches over roster size; cover is strict (no understaffing). Demand from `default_demand` with CLI-set weekday/weekend bases. |
| **store_optimizer_demo.py** | Builds demand per store (direct or from estimation), calls optimizer per store, prints schedule and hire/reduce recommendation. All parameters via CLI; `make_fake_demand` accepts base_weekday/base_weekend or base_M_weekday, base_A_weekday, base_M_weekend, base_A_weekend. |

For product goals and data structures, see **SPEC.md**.
