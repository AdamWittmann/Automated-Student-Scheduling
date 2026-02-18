# app.py
from schedule_log import save_schedule_log, load_schedule_log, list_saved_schedules
from flask import Flask, render_template, request, redirect, jsonify, session
from scheduling_logic import create_availability_matrix, run_schedule_optimization
from graph_scheduler import regenerate_weekly_schedule, delete_shifts_for_week, get_upcoming_monday
from graph_auth import get_graph_token
from datetime import timedelta, date
from dotenv import load_dotenv
import pandas as pd
import os
import requests
from msal import ConfidentialClientApplication
from functools import wraps

#Restricting Graph API call routes to be blocked until owner verification is complete
def require_owner(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return jsonify({"success": False, "message": "Not authenticated"}), 401
        if session.get('role') != 'owner':
            return jsonify({"success": False, "message": "Not authorized"}), 403
        return f(*args, **kwargs)
    return decorated

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "some-random-secret")
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'

TEAM_ID = os.getenv("TEAM_ID")
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

#ALLOW DOMAINS 
#FOR PROD -> INCLUDE NEW DOMAIN
@app.after_request
def add_headers(resp):
    resp.headers.pop('X-Frame-Options', None)
    resp.headers.pop('Content-Security-Policy', None)
    resp.headers['ngrok-skip-browser-warning'] = 'true'
    return resp

#MS teams blocks pop ups... even though trying to redirect to MS Auth page
#This application will handle accessing through a normal browser and through teams differently, but essentially the same


#Teams auth section
@app.route('/auth-start')
def auth_start():
    return render_template('auth_start.html')

@app.route('/auth-end')
def auth_end():
    return render_template('auth_end.html')

@app.route('/check-auth')
def check_auth():
    return jsonify({"authenticated": 'user' in session})
#END TEAMS AUTH SECTION

# Authentication |for prod-> change urls
msal_app = ConfidentialClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    client_credential=CLIENT_SECRET
)


#Directs to MS login page
@app.route('/login')
def login():
    redirect_uri = request.url_root.rstrip('/') + '/callback'
    redirect_uri = redirect_uri.replace('http://', 'https://')
    auth_url = msal_app.get_authorization_request_url(
        scopes=["User.Read"],
        redirect_uri=redirect_uri,
        prompt="select_account",
        state="teams" if request.args.get('from') == 'teams' else "web"
    )
    return redirect(auth_url)

#Part of auth, tells app how to finish sign in
@app.route('/callback')
def callback():
    code = request.args.get('code')
    redirect_uri = request.url_root.rstrip('/') + '/callback'
    redirect_uri = redirect_uri.replace('http://', 'https://')
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=["User.Read"],
        redirect_uri=redirect_uri
    )
    
    if 'access_token' in result:
        session['user'] = result.get('id_token_claims')
        print("✅ Logged in as:", session['user'].get('name', 'unknown'))
        
        # If in Teams popup, close it; otherwise redirect normally
        if request.args.get('state') == 'teams':
            return redirect('/auth-end')
        return redirect('/')
    else:
        print("❌ Login failed:", result.get('error_description'))
        return "Login failed", 401
#Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


#Check user status for redirects. Supervisors should be marked as a Team Owner within MS Teams
def get_user_role(user_id, team_id):
    token = get_graph_token()
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/teams/{team_id}/members",
        headers={"Authorization": f"Bearer {token}"}
    )
    resp.raise_for_status()
    members = resp.json()["value"]
    print(f"🔍 Looking for user_id: {user_id}")
    for member in members:
        print(f"   Team member: {member.get('displayName')} → userId: {member.get('userId')} → roles: {member.get('roles', [])}")
        if member.get("userId") == user_id:
            roles = member.get("roles", [])
            return "owner" if "owner" in roles else "member"
    return None


#For base page get the upcoming mondays (scheduling week goes monday-sunday)
def get_next_n_mondays(n=8):
    """Generate list of upcoming Monday dates with their Sunday end dates"""
    weeks = []
    current_monday = get_upcoming_monday()
    
    for i in range(n):
        monday = current_monday + timedelta(weeks=i)
        sunday = monday + timedelta(days=6)
        weeks.append({
            'monday': monday,
            'sunday': sunday
        })
    
    return weeks





@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if 'user' not in session:
        return render_template('login_landing.html', needs_auth=True)
    
    # Check role if not already cached in session
    if 'role' not in session:
        user_id = session['user'].get('oid')
        role = get_user_role(user_id, TEAM_ID)
        print(f"👤 {session['user'].get('name')} → role: {role} → oid: {user_id}")
        session['role'] = role

    # Students go to availability form
    if session['role'] == 'member':
        return redirect('/availability')
    
    # Non-team members blocked
    if session['role'] is None:
        return render_template('unauthorized.html')

    # Owners (supervisors) see the normal dashboard
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)

        # Get selected week from form (defaults to next Monday if not provided)
        selected_week_str = request.form.get('week_start')
        if selected_week_str:
            selected_week_start = date.fromisoformat(selected_week_str)
        else:
            selected_week_start = get_upcoming_monday()

        # Store in session so publish/reset can reference it
        session['selected_week_start'] = selected_week_start.isoformat()

        if file and file.filename.endswith(('.xlsx', '.xls')):
            try:
                file_bytes = file.read()

                students, availability_matrix = create_availability_matrix(file_bytes)
                schedule, student_hours, visual_grid_data = run_schedule_optimization(
                    students, availability_matrix
                )

                if schedule is None:
                    return render_template(
                        'schedule.html',
                        error="No feasible schedule could be found.",
                        schedule=None,
                        student_hours=None,
                        visual_grid_data=None,
                        available_weeks=get_next_n_mondays(),
                        selected_week=selected_week_start,
                        selected_week_end=selected_week_start + timedelta(days=6)
                    )

                # Save schedule to log immediately so it can be loaded by publish/reset
                save_schedule_log(schedule, selected_week_start)

                return render_template(
                    'schedule.html',
                    schedule=schedule,
                    student_hours=student_hours,
                    visual_grid_data=visual_grid_data,
                    available_weeks=get_next_n_mondays(),
                    selected_week=selected_week_start,
                    selected_week_end=selected_week_start + timedelta(days=6)
                )

            except Exception as e:
                return render_template(
                    'schedule.html',
                    error=f"An error occurred: {e}",
                    schedule=None,
                    student_hours=None,
                    visual_grid_data=None,
                    available_weeks=get_next_n_mondays(),
                    selected_week=selected_week_start,
                    selected_week_end=selected_week_start + timedelta(days=6)
                )

    # GET request - show upload form with week selector
    return render_template(
        'index.html',
        available_weeks=get_next_n_mondays(),
        default_week=get_upcoming_monday()
    )

@app.route('/availability')
def availability():
    if 'user' not in session:
        return redirect('/login')
    from scheduling_logic import SHIFTS_CONFIG
    shifts_config = [list(s) for s in SHIFTS_CONFIG]  # Convert tuples for JSON
    
    return render_template(
        'availability.html',
        user_name=session['user'].get('name', 'Student'),
        shifts_config=shifts_config,
        available_weeks=get_next_n_mondays(),
        default_week=get_upcoming_monday()
    )


@app.route('/submit-availability', methods=['POST'])
def submit_availability():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "No data received"}), 400

    week_start = data.get('week_start')
    max_hours = data.get('max_hours', 20)
    shifts = data.get('shifts', [])
    cwid = data.get('cwid', '').strip()

    if not shifts:
        return jsonify({"success": False, "message": "No shifts selected"}), 400
    if not cwid:
        return jsonify({"success": False, "message": "CWID is required"}), 400
    if not week_start:
        return jsonify({"success": False, "message": "Week not selected"}), 400

    user_claims = session.get('user', {})
    student_name = user_claims.get('name', 'Unknown')
    # preferred_username is the UPN (email) from Azure AD
    email = user_claims.get('preferred_username', '') or user_claims.get('email', '')
    if not email:
        return jsonify({"success": False, "message": "Could not determine email from login. Try signing out and back in."}), 400

    # Parse shifts into per-day time ranges
    # Shift keys look like "Mon|7-9" or "Mon|7.0-9.0"
    DAY_ORDER = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    day_shifts = {day: [] for day in DAY_ORDER}

    for shift_key in shifts:
        try:
            day_part, time_part = shift_key.split('|')
            start_str, end_str = time_part.split('-')
            start_f = float(start_str)
            end_f = float(end_str)

            # Convert float hours to HH:MM:SS
            def float_to_time(f):
                h = int(f)
                m = int(round((f - h) * 60))
                return f"{h:02d}:{m:02d}:00"

            time_range = f"{float_to_time(start_f)} - {float_to_time(end_f)}"
            if day_part in day_shifts:
                day_shifts[day_part].append(time_range)
        except (ValueError, IndexError):
            continue  # Skip malformed shift keys

    # Sort each day's shifts chronologically
    for day in DAY_ORDER:
        day_shifts[day].sort()

    # Build CSV row
    import csv
    import json
    from pathlib import Path

    csv_dir = Path('availability_submissions')
    csv_dir.mkdir(exist_ok=True)
    csv_path = csv_dir / f"availability_{week_start}.csv"

    # Column headers
    fieldnames = ['CWID', 'Student Name', 'Email', 'Max Hours',
                  'Monday', 'Tuesday', 'Wednesday', 'Thursday',
                  'Friday', 'Saturday', 'Sunday']

    day_to_col = {
        'Mon': 'Monday', 'Tue': 'Tuesday', 'Wed': 'Wednesday',
        'Thu': 'Thursday', 'Fri': 'Friday', 'Sat': 'Saturday', 'Sun': 'Sunday'
    }

    row = {
        'CWID': cwid,
        'Student Name': student_name,
        'Email': email,
        'Max Hours': max_hours,
    }
    for day_abbr, col_name in day_to_col.items():
        ranges = day_shifts[day_abbr]
        row[col_name] = json.dumps(ranges) if ranges else json.dumps([])

    # Read existing rows, replace if same CWID + same week, else append
    existing_rows = []
    if csv_path.exists():
        with open(csv_path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            existing_rows = [r for r in reader if r.get('CWID') != cwid]

    existing_rows.append(row)

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)

    return jsonify({"success": True, "message": "Availability saved successfully"})


@app.route('/publish-to-teams', methods=['POST'])
@require_owner
def publish_to_teams():
    # Get week from POST body first, fall back to session
    data = request.get_json(silent=True) or {}
    week_start_str = data.get('week_start') or session.get('selected_week_start')

    if not week_start_str:
        return jsonify({"success": False, "message": "No week selected"}), 400

    selected_week = date.fromisoformat(week_start_str)
    schedule = load_schedule_log(selected_week)

    if schedule is None:
        return jsonify({"success": False, "message": "No schedule found for this week. Generate one first."}), 400

    try:
        regenerate_weekly_schedule(
            team_id=TEAM_ID,
            display_schedule=schedule,
            week_monday=selected_week
        )

        week_end = selected_week + timedelta(days=6)
        message = f"✅ Schedule published to Microsoft Teams for {selected_week.strftime('%m/%d/%Y')} - {week_end.strftime('%m/%d/%Y')}"

        return jsonify({"success": True, "message": message})

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route('/reset-teams-schedule', methods=['POST'])
@require_owner
def reset_teams_schedule():
    # Get week from POST body first, fall back to session
    data = request.get_json(silent=True) or {}
    week_start_str = data.get('week_start') or session.get('selected_week_start')

    if not week_start_str:
        return jsonify({"success": False, "message": "No week selected"}), 400
    
    monday = date.fromisoformat(week_start_str)
    sunday = monday + timedelta(days=6)
    
    try:
        total_deleted = 0
        total_deleted_open = 0
        failed = 0
        failed_open = 0

        for attempt in range(3):
            token = get_graph_token()
            deleted, failed = delete_shifts_for_week(
                team_id=TEAM_ID,
                token=token,
                week_start=monday,
                week_end=sunday
            )
            
            from graph_scheduler import delete_open_shifts_for_week
            deleted_open, failed_open = delete_open_shifts_for_week(
                team_id=TEAM_ID,
                token=token,
                week_start=monday,
                week_end=sunday
            )

            total_deleted += deleted
            total_deleted_open += deleted_open

            if failed == 0 and failed_open == 0:
                break

        message = f"🧹 Reset complete for week of {monday.strftime('%m/%d/%Y')}: {total_deleted} assigned shifts deleted, {total_deleted_open} open shifts deleted"
        if failed > 0 or failed_open > 0:
            message += f" (⚠️ {failed + failed_open} shifts could not be deleted after 3 attempts)"
        
        return jsonify({
            "success": True, 
            "message": message, 
            "deleted": total_deleted, 
            "failed": failed,
            "deleted_open": total_deleted_open,
            "failed_open": failed_open
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route('/history', methods=['GET'])
def history():
    saved_weeks = list_saved_schedules()
    return render_template('history.html', saved_weeks=saved_weeks, timedelta=timedelta)

@app.route('/history/<week_date>', methods=['GET'])
def view_past_schedule(week_date):
    week_monday = date.fromisoformat(week_date)
    schedule = load_schedule_log(week_monday)
    if schedule is None:
        return redirect('/history')
    
    # Store in session so publish/reset can reference it
    session['selected_week_start'] = week_monday.isoformat()
    
    return render_template(
        'schedule.html',
        schedule=schedule,
        student_hours=None,
        visual_grid_data=None,
        available_weeks=get_next_n_mondays(),
        selected_week=week_monday,
        selected_week_end=week_monday + timedelta(days=6)
    )

if __name__ == '__main__':
    app.run(debug=True)