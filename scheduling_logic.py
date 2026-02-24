# scheduling_logic.py — Core scheduling engine using Google OR-Tools CP-SAT solver.
# Two-phase optimization: (1) maximize shift coverage, (2) minimize hour unfairness across students.

import pandas as pd
import io
import re
from ortools.sat.python import cp_model

# DEPLOYMENT: adjust shift times, days, and required staff counts to match the new site's needs
# Format: (Day abbreviation, Start hour as float, End hour as float, Required staff count)
SHIFTS_CONFIG = [
    # Mon-Fri Shifts (7.25-9, 9-12, 12-15, 15-17, 17-19)
    ("Mon", 7.25, 9, 3), ("Mon", 9, 12, 4), ("Mon", 12, 15, 3), ("Mon", 15, 17, 4), ("Mon", 17, 19, 3),
    ("Tue", 7.25, 9, 3), ("Tue", 9, 12, 4), ("Tue", 12, 15, 4), ("Tue", 15, 17, 4), ("Tue", 17, 19, 3),
    ("Wed", 7.25, 9, 3), ("Wed", 9, 12, 4), ("Wed", 12, 15, 4), ("Wed", 15, 17, 4), ("Wed", 17, 19, 3),
    ("Thu", 7.25, 9, 3), ("Thu", 9, 12, 4), ("Thu", 12, 15, 4), ("Thu", 15, 17, 4), ("Thu", 17, 19, 3),
    ("Fri", 7.25, 9, 3), ("Fri", 9, 12, 4), ("Fri", 12, 15, 4), ("Fri", 15, 17, 3),
    # Weekend Shifts
    ("Sat", 10, 14, 2), ("Sun", 10, 14, 2),
]
# DEPLOYMENT: update if your institution allows more/fewer weekly hours per student
MAX_WEEKLY_HOURS = 20
SCALE = 100  # CP-SAT requires integers; multiply float hours by SCALE for precision


# Convert a time string like "09:00:00" to a float hour (9.0)
def time_str_to_float(time_str):
    time_str = time_str.strip()
    if not time_str:
        return 0.0

    # Handle cases like "07:15:00-09:00:00"
    if '-' in time_str:
        time_str = time_str.split('-')[0].strip()

    parts = time_str.split(':')
    if not parts:
        return 0.0

    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    seconds = int(parts[2]) if len(parts) > 2 else 0

    return hours + minutes / 60.0 + seconds / 3600.0


# Parse a list-like string from an Excel cell into individual time range strings
def parse_cell(raw_data):
    if not raw_data:
        return []
    raw_data = str(raw_data)
    # Remove quotes, brackets, and extra spaces
    cleaned = raw_data.strip().replace('[', '').replace(']', '').replace('"', '').replace("'", '')
    # Split by comma or similar separator to get individual ranges
    time_ranges = [r.strip() for r in re.split(r'[,;]', cleaned) if r.strip()]
    return time_ranges


# Read an uploaded Excel file and build the per-student availability matrix for the optimizer
def create_availability_matrix(excel_file_bytes):
    df = pd.read_excel(io.BytesIO(excel_file_bytes))
    # Filter out any rows where STUDENT NAME might be missing
    df = df.dropna(subset=['STUDENT NAME'])
    students = df['STUDENT NAME'].tolist()
    availability_matrix = {student: {} for student in students}

    day_map = {"Mon": "MONDAY", "Tue": "TUESDAY", "Wed": "WEDNESDAY", "Thu": "THURSDAY", "Fri": "FRIDAY",
               "Sat": "SATURDAY", "Sun": "SUNDAY"}

    for _, row in df.iterrows():
        student_name = row['STUDENT NAME']
        for day_abbr, start, end, _ in SHIFTS_CONFIG:
            shift_id = (day_abbr, start, end)

            full_day_column = day_map.get(day_abbr)
            raw_availability = row.get(full_day_column)

            parsed_ranges = []
            for rng_str in parse_cell(raw_availability):
                try:
                    # Robust splitting logic
                    parts = rng_str.split('-')
                    if len(parts) == 2:
                        rng_start = time_str_to_float(parts[0])
                        rng_end = time_str_to_float(parts[1])
                        parsed_ranges.append((rng_start, rng_end))
                except Exception:
                    continue

            # Check if the shift is fully covered by any available range
            is_available = any(rng_start <= start and end <= rng_end for (rng_start, rng_end) in parsed_ranges)

            availability_matrix[student_name][shift_id] = 1 if is_available else 0

    return students, availability_matrix


# Run the two-phase CP-SAT optimizer:
#   Phase 1 — maximize total shift coverage
#   Phase 2 — with coverage locked, minimize the gap between most- and least-scheduled students
# Returns (display_schedule, student_hours, visual_grid_data) or (None, None, None) if infeasible
def run_schedule_optimization(students, availability_matrix, student_max_hours=None):

    shifts_with_id = []
    shift_lengths = {}
    total_required = 0.0

    for idx, (day, start, end, required) in enumerate(SHIFTS_CONFIG):
        shift_id = (day, start, end)
        shifts_with_id.append((f"S{idx + 1}", day, start, end, required))
        shift_length = end - start
        shift_lengths[shift_id] = shift_length
        total_required += required * shift_length

    # --- Model Setup ---
    model = cp_model.CpModel()

    # Create decision variables
    x = {}
    for sid, day, start, end, _ in shifts_with_id:
        shift_id = (day, start, end)
        for i in students:
            if availability_matrix[i].get(shift_id, 0) == 1:
                x[shift_id, i] = model.NewBoolVar(f"x_{sid}_{i}")

    # --- Constraints and Objectives ---

    # 1. Coverage Variables (total number of people assigned to a shift)
    coverage_sum = model.NewIntVar(0, int(total_required * SCALE), "coverage_sum")
    coverage_terms = []
    coverage = {}

    for sid, day, start, end, required in shifts_with_id:
        shift_id = (day, start, end)
        shift_vars = [x[shift_id, i] for i in students if (shift_id, i) in x]

        coverage[shift_id] = model.NewIntVar(0, len(students), f"coverage_{sid}")
        model.Add(coverage[shift_id] == sum(shift_vars))

        if required > 0:
            shift_hours = int(shift_lengths[shift_id] * SCALE)
            coverage_hours = model.NewIntVar(0, shift_hours * required, f"coverage_hours_{sid}")
            model.Add(coverage_hours == coverage[shift_id] * shift_hours)
            coverage_terms.append(coverage_hours)

    model.Add(coverage_sum == sum(coverage_terms))

    # 2. Per-Student Maximum Weekly Hours Constraint
    total_hours = {}
    global_max_scaled = int(MAX_WEEKLY_HOURS * SCALE)

    for i in students:
        # Use per-student max if provided, otherwise fall back to global cap
        if student_max_hours and i in student_max_hours:
            per_student_max = int(student_max_hours[i] * SCALE)
        else:
            per_student_max = global_max_scaled

        student_hour_terms = []
        for sid, day, start, end, required in shifts_with_id:
            shift_id = (day, start, end)
            if (shift_id, i) in x:
                shift_length_scaled = int(shift_lengths[shift_id] * SCALE)
                student_hour_terms.append(x[shift_id, i] * shift_length_scaled)

        total_hours[i] = model.NewIntVar(0, per_student_max, f"total_hours_{i}")
        model.Add(total_hours[i] == sum(student_hour_terms))
        model.Add(total_hours[i] <= per_student_max)

    # --------------------------
    # PHASE 1: Maximize coverage
    # --------------------------
    model.Maximize(coverage_sum)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None, None

    best_coverage = solver.Value(coverage_sum)

    # --------------------------------------------------
    # PHASE 2: Minimize Unfairness (Maximize Fairness)
    # --------------------------------------------------

    # Use the same model object for the second phase, adding a constraint
    model.Add(coverage_sum == best_coverage)

    all_student_hours = list(total_hours.values())
    if all_student_hours:
        # Use the global max as the upper bound for the fairness variables
        # since individual caps may differ
        fairness_upper_bound = global_max_scaled
        max_hours_scaled = model.NewIntVar(0, fairness_upper_bound, "max_hours_scaled")
        min_hours_scaled = model.NewIntVar(0, fairness_upper_bound, "min_hours_scaled")

        model.AddMaxEquality(max_hours_scaled, all_student_hours)
        model.AddMinEquality(min_hours_scaled, all_student_hours)

        fairness_term = max_hours_scaled - min_hours_scaled
        model.Minimize(fairness_term)

        # Re-solve for fairness
        solver.Solve(model)

    # --- Extract Results ---
    schedule = {}
    final_student_hours = {i: 0.0 for i in students}

    # New structure for the visual grid
    visual_assignments = {student: {} for student in students}
    shift_keys = []

    for sid, day, start, end, required in shifts_with_id:
        shift_id = (day, start, end)
        shift_key = f"{day} {start:.2f}-{end:.2f}"

        if shift_key not in shift_keys:
            shift_keys.append(shift_key)

        assigned_students = []
        current_shift_length = shift_lengths[shift_id]

        for i in students:
            is_assigned = (shift_id, i) in x and solver.BooleanValue(x[shift_id, i])

            # Populate visual grid data
            visual_assignments[i][shift_key] = 1 if is_assigned else 0

            if is_assigned:
                assigned_students.append(i)
                final_student_hours[i] += current_shift_length

        schedule[shift_key] = {
            'required': required,
            'assigned_count': len(assigned_students),
            'assigned_students': ", ".join(assigned_students) if assigned_students else "UNSTAFFED"
        }

    # Re-format the schedule for display
    display_schedule = []
    for sid, day, start, end, required in shifts_with_id:
        shift_key = f"{day} {start:.2f}-{end:.2f}"
        item = schedule.get(shift_key)
        if item:
            item['shift'] = shift_key
            display_schedule.append(item)

    # Final Visual Grid Data Structure
    visual_grid_data = {
        'students': sorted(students),
        'shift_keys': shift_keys,
        'assignments': visual_assignments
    }

    return display_schedule, final_student_hours, visual_grid_data