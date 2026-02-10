#Logic behind saving and retreiving schedules
import os
import pandas as pd
from datetime import time, date


LOG_DIR = "schedule_logs"
os.makedirs("schedule_logs", exist_ok=True)

def save_schedule_log(schedule, week_monday):
    df = pd.DataFrame(schedule)
    df.to_csv(f"{LOG_DIR}/{week_monday.isoformat()}.csv", index=False)


def list_saved_schedules():
    """Return a sorted list of week Monday dates that have saved schedules."""
    files = [f for f in os.listdir(LOG_DIR) if f.endswith(".csv")]
    dates = []
    for f in files:
        try:
            dates.append(date.fromisoformat(f.replace(".csv", "")))
        except ValueError:
            continue
    return sorted(dates, reverse=True)


def load_schedule_log(week_monday):
    """Load a saved schedule CSV back into a list of dicts."""
    path = f"{LOG_DIR}/{week_monday.isoformat()}.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df.to_dict(orient="records")