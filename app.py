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
# In-memory storage of last generated schedule (demo-safe)
CURRENT_SCHEDULE = None
SELECTED_WEEK_START = None  # Track which Monday was selected

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
    global CURRENT_SCHEDULE, SELECTED_WEEK_START

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
            SELECTED_WEEK_START = date.fromisoformat(selected_week_str)
        else:
            SELECTED_WEEK_START = get_upcoming_monday()

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
                        selected_week=SELECTED_WEEK_START,
                        selected_week_end=SELECTED_WEEK_START + timedelta(days=6) if SELECTED_WEEK_START else None
                    )

                # 🔑 store for later publishing
                CURRENT_SCHEDULE = schedule

                return render_template(
                    'schedule.html',
                    schedule=schedule,
                    student_hours=student_hours,
                    visual_grid_data=visual_grid_data,
                    available_weeks=get_next_n_mondays(),
                    selected_week=SELECTED_WEEK_START,
                    selected_week_end=SELECTED_WEEK_START + timedelta(days=6) if SELECTED_WEEK_START else None
                )

            except Exception as e:
                return render_template(
                    'schedule.html',
                    error=f"An error occurred: {e}",
                    schedule=None,
                    student_hours=None,
                    visual_grid_data=None,
                    available_weeks=get_next_n_mondays(),
                    selected_week=SELECTED_WEEK_START,
                    selected_week_end=SELECTED_WEEK_START + timedelta(days=6) if SELECTED_WEEK_START else None
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
        shifts_config=shifts_config
    )

@app.route('/publish-to-teams', methods=['POST'])
@require_owner
def publish_to_teams():
    if CURRENT_SCHEDULE is None:
        return jsonify({"success": False, "message": "No schedule to publish"}), 400
    
    if SELECTED_WEEK_START is None:
        return jsonify({"success": False, "message": "No week selected"}), 400

    try:
        regenerate_weekly_schedule(
            team_id=TEAM_ID,
            display_schedule=CURRENT_SCHEDULE,
            week_monday=SELECTED_WEEK_START
        )

        week_end = SELECTED_WEEK_START + timedelta(days=6)
        message = f"✅ Schedule published to Microsoft Teams for {SELECTED_WEEK_START.strftime('%m/%d/%Y')} - {week_end.strftime('%m/%d/%Y')}"

        #Save schedule to logs
        save_schedule_log(CURRENT_SCHEDULE, SELECTED_WEEK_START)

        return jsonify({"success": True, "message": message})

        
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route('/reset-teams-schedule', methods=['POST'])
@require_owner
def reset_teams_schedule():
    if SELECTED_WEEK_START is None:
        return jsonify({"success": False, "message": "No week selected"}), 400
    
    monday = SELECTED_WEEK_START
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
    global CURRENT_SCHEDULE, SELECTED_WEEK_START
    week_monday = date.fromisoformat(week_date)
    schedule = load_schedule_log(week_monday)
    if schedule is None:
        return redirect('/history')
    
    CURRENT_SCHEDULE = schedule
    SELECTED_WEEK_START = week_monday
    
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