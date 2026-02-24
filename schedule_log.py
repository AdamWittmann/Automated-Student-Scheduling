# schedule_log.py — Persist and retrieve generated schedules as CSV files

import os
import pandas as pd
from datetime import time, date


# DEPLOYMENT: change this path if schedule logs should live elsewhere (e.g. a shared volume)
LOG_DIR = "schedule_logs"
os.makedirs("schedule_logs", exist_ok=True)

# Save a generated schedule (list of dicts) to a CSV named by week Monday date
def save_schedule_log(schedule, week_monday):
    df = pd.DataFrame(schedule)
    df.to_csv(f"{LOG_DIR}/{week_monday.isoformat()}.csv", index=False)


# Return a reverse-sorted list of Monday dates for which schedule CSVs exist
def list_saved_schedules():
    files = [f for f in os.listdir(LOG_DIR) if f.endswith(".csv")]
    dates = []
    for f in files:
        try:
            dates.append(date.fromisoformat(f.replace(".csv", "")))
        except ValueError:
            continue
    return sorted(dates, reverse=True)


# Load a saved schedule CSV back into a list of dicts for rendering or publishing
def load_schedule_log(week_monday):
    path = f"{LOG_DIR}/{week_monday.isoformat()}.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df.to_dict(orient="records")