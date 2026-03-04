# schedule_log.py — Persist and retrieve generated schedules as CSV files

import os
import logging
import pandas as pd
from datetime import time, date

logger = logging.getLogger(__name__)


# DEPLOYMENT: change this path if schedule logs should live elsewhere (e.g. a shared volume)
LOG_DIR = "schedule_logs"
os.makedirs("schedule_logs", exist_ok=True)

# Save a generated schedule (list of dicts) to a CSV named by week Monday date
def save_schedule_log(schedule, week_monday):
    try:
        df = pd.DataFrame(schedule)
        path = f"{LOG_DIR}/{week_monday.isoformat()}.csv"
        df.to_csv(path, index=False)
        logger.info("Schedule saved for week %s (%d shifts)", week_monday, len(schedule))
    except Exception as e:
        logger.exception("Failed to save schedule log for week %s", week_monday)
        raise


# Return a reverse-sorted list of Monday dates for which schedule CSVs exist
def list_saved_schedules():
    try:
        files = [f for f in os.listdir(LOG_DIR) if f.endswith(".csv")]
        dates = []
        for f in files:
            try:
                dates.append(date.fromisoformat(f.replace(".csv", "")))
            except ValueError:
                logger.warning("Skipping unrecognized file in schedule_logs: %s", f)
                continue
        return sorted(dates, reverse=True)
    except Exception as e:
        logger.exception("Failed to list saved schedules")
        return []


# Load a saved schedule CSV back into a list of dicts for rendering or publishing
def load_schedule_log(week_monday):
    path = f"{LOG_DIR}/{week_monday.isoformat()}.csv"
    if not os.path.exists(path):
        logger.warning("No schedule log found for week %s", week_monday)
        return None
    try:
        df = pd.read_csv(path)
        logger.info("Schedule loaded for week %s (%d shifts)", week_monday, len(df))
        return df.to_dict(orient="records")
    except Exception as e:
        logger.exception("Failed to read schedule log for week %s", week_monday)
        return None