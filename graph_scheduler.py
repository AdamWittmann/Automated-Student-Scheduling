# graph_scheduler.py — Create, delete, and sync shifts in MS Teams via the Graph API

import requests
import logging
from datetime import date, datetime, timedelta
from dateutil.parser import isoparse
import pytz

from graph_auth import get_graph_token

logger = logging.getLogger(__name__)


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# DEPLOYMENT: change this to the local timezone of the scheduling site
TIMEZONE = pytz.timezone("America/New_York")

DAY_MAP = {
    "Mon": 0, "Tue": 1, "Wed": 2,
    "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6
}

# Return the date of the upcoming Monday (or today if it is Monday)
def get_upcoming_monday(today=None):
    if today is None:
        today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7)


# Return (Monday, Sunday) date pair for a given week
def get_week_window(monday):
    return monday, monday + timedelta(days=6)

# Fetch all team members and return a {displayName: userId} map
def get_team_members(team_id, token):
    resp = requests.get(
        f"{GRAPH_BASE}/teams/{team_id}/members",
        headers={"Authorization": f"Bearer {token}"}
    )
    resp.raise_for_status()

    return {
        m["displayName"]: m["userId"]
        for m in resp.json()["value"]
        if "userId" in m
    }
# Retrieve all assigned shifts for the team from Graph API
def get_all_shifts(team_id, token):
    resp = requests.get(
        f"{GRAPH_BASE}/teams/{team_id}/schedule/shifts",
        headers={"Authorization": f"Bearer {token}"}
    )
    resp.raise_for_status()
    return resp.json()["value"]

# Retrieve all open (unassigned/claimable) shifts for the team
def get_all_open_shifts(team_id, token):
    resp = requests.get(
        f"{GRAPH_BASE}/teams/{team_id}/schedule/openShifts",
        headers={"Authorization": f"Bearer {token}"}
    )
    resp.raise_for_status()
    return resp.json()["value"]

# Delete all open shifts falling within the given Monday–Sunday range; returns (deleted, failed)
def delete_open_shifts_for_week(team_id, token, week_start, week_end):
    try:
        open_shifts = get_all_open_shifts(team_id, token)
    except Exception as e:
        logger.error("Could not fetch open shifts: %s", e)
        return 0, 0
    
    deleted_count = 0
    failed_count = 0

    for shift in open_shifts:
        try:
            start = isoparse(shift["sharedOpenShift"]["startDateTime"]).date()

            if week_start <= start <= week_end:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
                
                if "@odata.etag" in shift:
                    headers["If-Match"] = shift["@odata.etag"]
                
                resp = requests.delete(
                    f"{GRAPH_BASE}/teams/{team_id}/schedule/openShifts/{shift['id']}",
                    headers=headers
                )
                resp.raise_for_status()
                deleted_count += 1
                
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 429:
                logger.warning("Rate limited (429) deleting open shift %s", shift.get('id'))
            else:
                logger.error("Failed to delete open shift %s: HTTP %s", shift.get('id'), status)
            failed_count += 1
            continue
        except Exception as e:
            logger.exception("Unexpected error deleting open shift %s", shift.get('id'))
            failed_count += 1
            continue
    
    logger.info("Open shift delete complete: %d deleted, %d failed", deleted_count, failed_count)
    return deleted_count, failed_count
# Delete all assigned shifts falling within the given Monday–Sunday range; returns (deleted, failed)
def delete_shifts_for_week(team_id, token, week_start, week_end):
    shifts = get_all_shifts(team_id, token)
    deleted_count = 0
    failed_count = 0

    for shift in shifts:
        try:
            start = isoparse(shift["sharedShift"]["startDateTime"]).date()

            if week_start <= start <= week_end:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
                
                if "@odata.etag" in shift:
                    headers["If-Match"] = shift["@odata.etag"]
                
                resp = requests.delete(
                    f"{GRAPH_BASE}/teams/{team_id}/schedule/shifts/{shift['id']}",
                    headers=headers
                )
                resp.raise_for_status()
                deleted_count += 1
                
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 429:
                logger.warning("Rate limited (429) deleting shift %s", shift.get('id'))
            else:
                logger.error("Failed to delete shift %s: HTTP %s", shift.get('id'), status)
            failed_count += 1
            continue
        except Exception as e:
            logger.exception("Unexpected error deleting shift %s", shift.get('id'))
            failed_count += 1
            continue
    
    logger.info("Assigned shift delete complete: %d deleted, %d failed", deleted_count, failed_count)
    return deleted_count, failed_count


# Convert a shift string like "Mon 7.25-9.0" into timezone-aware ISO start/end datetimes
def build_shift_datetimes(shift_str, week_monday):
    day, time_range = shift_str.split(" ")
    start_str, end_str = time_range.split("-")

    def parse_time(t):
        # Handles both "7.25" (float) and "7:15" (HH:MM) formats
        if ':' in t:
            h, m = t.split(':')
            return int(h) + int(m) / 60.0
        return float(t)

    start_f = parse_time(start_str)
    end_f = parse_time(end_str)

    shift_date = week_monday + timedelta(days=DAY_MAP[day])

    def to_hm(f):
        h = int(f)
        m = int(round((f - h) * 60))
        return h, m

    sh, sm = to_hm(start_f)
    eh, em = to_hm(end_f)

    start_dt = TIMEZONE.localize(
        datetime(shift_date.year, shift_date.month, shift_date.day, sh, sm)
    )
    end_dt = TIMEZONE.localize(
        datetime(shift_date.year, shift_date.month, shift_date.day, eh, em)
    )

    return start_dt.isoformat(), end_dt.isoformat()

# Create a single assigned shift for a specific user in MS Teams Shifts
def create_shift(team_id, token, user_id, start_dt, end_dt):
    payload = {
        "userId": user_id,
        "sharedShift": {
            "startDateTime": start_dt,
            "endDateTime": end_dt,
            "theme": "blue",
            "notes": "Auto-generated weekly schedule"
        }
    }

    resp = requests.post(
        f"{GRAPH_BASE}/teams/{team_id}/schedule/shifts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json=payload
    )
    resp.raise_for_status()
    
# Create an open (unassigned) shift that any team member can claim; used for understaffed slots
def create_open_shift(team_id, token, start_dt, end_dt, slot_count=1, notes=""):
    payload = {
        "sharedOpenShift": {
            "startDateTime": start_dt,
            "endDateTime": end_dt,
            "theme": "pink",  # Pink = open shift
            "notes": notes or "Open shift - available to all team members",
            "openSlotCount": slot_count
        }
    }

    resp = requests.post(
        f"{GRAPH_BASE}/teams/{team_id}/schedule/openShifts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json=payload
    )
    resp.raise_for_status()
    return resp.json()

# Publish a full week's schedule to Teams: clears old shifts, creates assigned + open shifts
def regenerate_weekly_schedule(team_id, display_schedule, week_monday=None):
    token = get_graph_token()

    if week_monday is None:
        week_monday = get_upcoming_monday()
    
    sunday = week_monday + timedelta(days=6)

    user_map = get_team_members(team_id, token)

    # Delete existing shifts and open shifts for this week
    deleted, failed = delete_shifts_for_week(team_id, token, week_monday, sunday)
    logger.info("Cleared %d assigned shifts for week of %s", deleted, week_monday.strftime('%m/%d/%Y'))
    
    deleted_open, failed_open = delete_open_shifts_for_week(team_id, token, week_monday, sunday)
    logger.info("Cleared %d open shifts for week of %s", deleted_open, week_monday.strftime('%m/%d/%Y'))

    # Create new shifts and track understaffed positions
    created_count = 0
    open_shifts_count = 0
    
    for item in display_schedule:
        start_dt, end_dt = build_shift_datetimes(item["shift"], week_monday)
        
        # Calculate how many slots are unfilled
        required = item["required"]
        assigned = item["assigned_count"]
        unfilled_slots = max(0, required - assigned)
        
        # Create assigned shifts
        if item["assigned_students"] != "UNSTAFFED":
            students = [s.strip() for s in item["assigned_students"].split(",")]

            for student in students:
                if student not in user_map:
                    logger.warning("Unknown Teams user skipped during publish: %s", student)
                    continue

                create_shift(
                    team_id,
                    token,
                    user_map[student],
                    start_dt,
                    end_dt
                )
                created_count += 1
        
        # Create open shift(s) for unfilled positions
        if unfilled_slots > 0:
            try:
                notes = f"Open slot - {item['shift']} ({unfilled_slots} position{'s' if unfilled_slots > 1 else ''} available)"
                create_open_shift(
                    team_id,
                    token,
                    start_dt,
                    end_dt,
                    slot_count=unfilled_slots,
                    notes=notes
                )
                open_shifts_count += 1
                logger.info("Created open shift for %s (%d slot(s))", item['shift'], unfilled_slots)
            except Exception as e:
                logger.error("Failed to create open shift for %s: %s", item['shift'], e)
    
    logger.info("Publish complete for week of %s: %d assigned shifts, %d open shifts created",
                week_monday.strftime('%m/%d/%Y'), created_count, open_shifts_count)