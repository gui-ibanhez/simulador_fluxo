from ortools.sat.python import cp_model

model = cp_model.CpModel()

# Variables
x = model.new_int_var(0, 100, "x")
y = model.new_int_var(0, 100, "y")
z = model.new_int_var(0, 100, "z")
# Constraints
model.add(x + y <= 30)
model.add(x + z >= 15)
model.add(y + z >= 25)
# Objective
model.maximize(30 * x + 50 * y + 100 * z)

# Solve
solver = cp_model.CpSolver()
status_code = solver.solve(model)
status_name = solver.status_name(status_code)

# Print the solver status and the optimal solution.
print(f"{status_name} ({status_code})")
print(f"x={solver.value(x)},  y={solver.value(y)}, z={solver.value(z)}")