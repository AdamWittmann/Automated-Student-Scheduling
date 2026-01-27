import requests
from datetime import date, datetime, timedelta
from dateutil.parser import isoparse
import pytz

from graph_auth import get_graph_token


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEZONE = pytz.timezone("America/New_York")

DAY_MAP = {
    "Mon": 0, "Tue": 1, "Wed": 2,
    "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6
}

def get_upcoming_monday(today=None):
    if today is None:
        today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7)


def get_week_window(monday):
    return monday, monday + timedelta(days=6)

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
def get_all_shifts(team_id, token):
    resp = requests.get(
        f"{GRAPH_BASE}/teams/{team_id}/schedule/shifts",
        headers={"Authorization": f"Bearer {token}"}
    )
    resp.raise_for_status()
    return resp.json()["value"]

def get_all_open_shifts(team_id, token):
    """Get all open shifts for the team"""
    resp = requests.get(
        f"{GRAPH_BASE}/teams/{team_id}/schedule/openShifts",
        headers={"Authorization": f"Bearer {token}"}
    )
    resp.raise_for_status()
    return resp.json()["value"]

def delete_open_shifts_for_week(team_id, token, week_start, week_end):
    """
    Delete all open shifts within the specified week range.
    
    Returns:
        tuple: (deleted_count, failed_count)
    """
    try:
        open_shifts = get_all_open_shifts(team_id, token)
    except Exception as e:
        print(f"⚠️ Could not fetch open shifts: {e}")
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
                
                # Include eTag to prevent conflicts
                if "@odata.etag" in shift:
                    headers["If-Match"] = shift["@odata.etag"]
                
                resp = requests.delete(
                    f"{GRAPH_BASE}/teams/{team_id}/schedule/openShifts/{shift['id']}",
                    headers=headers
                )
                resp.raise_for_status()
                deleted_count += 1
                
        except requests.exceptions.HTTPError as e:
            print(f"❌ Failed to delete open shift {shift.get('id')}: {e.response.status_code}")
            failed_count += 1
            continue
        except Exception as e:
            print(f"❌ Unexpected error deleting open shift {shift.get('id')}: {e}")
            failed_count += 1
            continue
    
    return deleted_count, failed_count
def delete_shifts_for_week(team_id, token, week_start, week_end):
    """
    Delete all shifts within the specified week range.
    
    Returns:
        tuple: (deleted_count, failed_count)
    """
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
                
                # Include eTag to prevent conflicts
                if "@odata.etag" in shift:
                    headers["If-Match"] = shift["@odata.etag"]
                
                resp = requests.delete(
                    f"{GRAPH_BASE}/teams/{team_id}/schedule/shifts/{shift['id']}",
                    headers=headers
                )
                resp.raise_for_status()
                deleted_count += 1
                
        except requests.exceptions.HTTPError as e:
            print(f"❌ Failed to delete shift {shift.get('id')}: {e.response.status_code}")
            failed_count += 1
            continue
        except Exception as e:
            print(f"❌ Unexpected error deleting shift {shift.get('id')}: {e}")
            failed_count += 1
            continue
    
    return deleted_count, failed_count
def build_shift_datetimes(shift_str, week_monday):
    day, time_range = shift_str.split(" ")
    start_f, end_f = map(float, time_range.split("-"))

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

#Shift Creation
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

def create_open_shift(team_id, token, start_dt, end_dt, slot_count=1, notes=""):
    """
    Create an open shift that any team member can pick up.
    
    Args:
        team_id: MS Teams team ID
        token: Auth token
        start_dt: Shift start datetime (ISO format)
        end_dt: Shift end datetime (ISO format)
        slot_count: Number of open slots available (default 1)
        notes: Optional notes for the shift
    """
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

def regenerate_weekly_schedule(team_id, display_schedule, week_monday=None):
    """
    Generate schedule for a specific week.
    Posts open shifts for any understaffed positions.
    
    Args:
        team_id: MS Teams team ID
        display_schedule: Schedule data from optimizer
        week_monday: Date object for the Monday of target week (defaults to upcoming Monday)
    """
    token = get_graph_token()

    if week_monday is None:
        week_monday = get_upcoming_monday()
    
    sunday = week_monday + timedelta(days=6)

    user_map = get_team_members(team_id, token)

    # Delete existing shifts and open shifts for this week
    deleted, failed = delete_shifts_for_week(team_id, token, week_monday, sunday)
    print(f"🧹 Cleared {deleted} assigned shifts for week of {week_monday.strftime('%m/%d/%Y')}")
    
    deleted_open, failed_open = delete_open_shifts_for_week(team_id, token, week_monday, sunday)
    print(f"🧹 Cleared {deleted_open} open shifts for week of {week_monday.strftime('%m/%d/%Y')}")

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
                    print(f"⚠️ Unknown Teams user: {student}")
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
                print(f"📌 Created open shift for {item['shift']} ({unfilled_slots} slots)")
            except Exception as e:
                print(f"❌ Failed to create open shift for {item['shift']}: {e}")
    
    print(f"✅ Created {created_count} assigned shifts for week of {week_monday.strftime('%m/%d/%Y')}")
    print(f"📌 Created {open_shifts_count} open shifts for understaffed positions")