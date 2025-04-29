# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import datetime
import json
import graphviz  # For dependency graph
from collections import defaultdict
import numpy as np  # For business day calculations
import logging  # For debugging
import math
import calendar # For month names

# Basic logging setup - Change to DEBUG for more detail
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# --- Initial Setup and Session State ---
st.set_page_config(layout="wide", page_title="Advanced Project Planner")

# English month and day names for UI consistency
month_names_en = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}
day_names_en = {
    "Monday": "Monday", "Tuesday": "Tuesday", "Wednesday": "Wednesday",
    "Thursday": "Thursday", "Friday": "Friday", "Saturday": "Saturday", "Sunday": "Sunday"
}

# Initialization in session_state.
if 'config' not in st.session_state:
    st.session_state.config = {
        'project_start_date': datetime.date.today(),
        'exclude_weekends': True,
        'working_hours': {
            'default': {
                "Monday": 9.0, "Tuesday": 9.0, "Wednesday": 9.0,
                "Thursday": 9.0, "Friday": 7.0, "Saturday": 0.0, "Sunday": 0.0
            },
            'monthly_overrides': {}
        },
        'profit_margin_percent': 0.0
    }
# Ensure default values if 'config' already exists but lacks keys
st.session_state.config.setdefault('project_start_date', datetime.date.today())
st.session_state.config.setdefault('exclude_weekends', True)
if 'working_hours' not in st.session_state.config or not isinstance(st.session_state.config['working_hours'], dict) or 'default' not in st.session_state.config['working_hours']:
    st.session_state.config['working_hours'] = {
        'default': {
            "Monday": 9.0, "Tuesday": 9.0, "Wednesday": 9.0,
            "Thursday": 9.0, "Friday": 7.0, "Saturday": 0.0, "Sunday": 0.0
        },
        'monthly_overrides': {}
    }
st.session_state.config['working_hours'].setdefault('default', {
    "Monday": 9.0, "Tuesday": 9.0, "Wednesday": 9.0,
    "Thursday": 9.0, "Friday": 7.0, "Saturday": 0.0, "Sunday": 0.0
})
st.session_state.config['working_hours'].setdefault('monthly_overrides', {})
st.session_state.config.setdefault('profit_margin_percent', 0.0)

# Roles store availability_percent and rate_eur_hr
if 'roles' not in st.session_state:
    st.session_state.roles = {}
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'next_task_id' not in st.session_state:
    st.session_state.next_task_id = 1
if 'macrotasks' not in st.session_state:
    st.session_state.macrotasks = {} # {macro_name: color}
if 'last_macro' not in st.session_state:
    st.session_state.last_macro = None

# --- Data Consistency Check at Startup ---
# (Same validation logic as before, ensures tasks have correct structure)
for task in st.session_state.tasks:
    # Ensure 'dependencies' is a JSON string of a list
    if isinstance(task.get('dependencies'), list):
        task['dependencies'] = json.dumps(task['dependencies'])
    elif not isinstance(task.get('dependencies'), str):
        task['dependencies'] = '[]'
    else: # If it's a string, validate it's a list JSON
        try:
            parsed_deps = json.loads(task['dependencies'])
            if not isinstance(parsed_deps, list):
                task['dependencies'] = '[]'
        except (json.JSONDecodeError, TypeError):
            task['dependencies'] = '[]'

    # Ensure 'assignments' is a list of dictionaries
    if 'assignments' not in task:
        task['assignments'] = []
    elif isinstance(task.get('assignments'), str):
        try:
            parsed_assign = json.loads(task['assignments'])
            if isinstance(parsed_assign, list) and all(isinstance(d, dict) and 'role' in d and 'allocation' in d for d in parsed_assign):
                 task['assignments'] = parsed_assign
            else:
                 logging.warning(f"Task {task.get('id')}: Invalid assignment format parsed from string '{task['assignments']}'. Resetting.")
                 task['assignments'] = []
        except (json.JSONDecodeError, TypeError):
             logging.warning(f"Task {task.get('id')}: Failed to parse assignment string '{task['assignments']}'. Resetting.")
             task['assignments'] = []
    elif not isinstance(task.get('assignments'), list):
         logging.warning(f"Task {task.get('id')}: assignments field was not a list. Resetting.")
         task['assignments'] = []
    else: # If it's already a list, validate content
         valid_assignments = []
         for item in task['assignments']:
             if isinstance(item, dict) and 'role' in item and 'allocation' in item:
                 try:
                     item['allocation'] = float(item['allocation'])
                     if 0 <= item['allocation'] <= 100:
                         valid_assignments.append(item)
                     else:
                         logging.warning(f"Task {task.get('id')}: Invalid allocation value {item['allocation']} for role {item['role']}. Skipping assignment.")
                 except (ValueError, TypeError):
                     logging.warning(f"Task {task.get('id')}: Non-numeric allocation for role {item['role']}. Skipping assignment.")
             else:
                 logging.warning(f"Task {task.get('id')}: Invalid assignment item format: {item}. Skipping.")
         task['assignments'] = valid_assignments

    # Ensure other fields exist
    task.setdefault('macro', 'No Phase')
    task.setdefault('subtask', 'No Subtask')
    task.setdefault('phase_color', st.session_state.macrotasks.get(task['macro'], "#CCCCCC"))
    task['name'] = f"{str(task.get('macro','No Phase')).strip()} - {str(task.get('subtask','No Subtask')).strip()}"


# --- HELPER FUNCTIONS (Including adaptations from gant_generator.py) ---

def get_working_hours_for_date(target_date: datetime.date, working_hours_config: dict) -> float:
    """
    Gets the working hours for a specific date, considering monthly overrides.
    (Same as before)
    """
    if not isinstance(target_date, datetime.date) or not isinstance(working_hours_config, dict):
        return 0.0
    month = target_date.month
    day_name = target_date.strftime("%A")
    monthly_schedule = working_hours_config.get('monthly_overrides', {}).get(month)
    if monthly_schedule and isinstance(monthly_schedule, dict):
        return monthly_schedule.get(day_name, 0.0)
    else:
        default_schedule = working_hours_config.get('default', {})
        return default_schedule.get(day_name, 0.0)

def get_next_working_day(input_date: datetime.date, working_hours_config: dict, exclude_weekends: bool) -> datetime.date:
    """Finds the next date that is considered a working day."""
    next_day = input_date
    while True:
        day_hours = get_working_hours_for_date(next_day, working_hours_config)
        is_weekend = next_day.weekday() >= 5
        if day_hours > 0 and not (exclude_weekends and is_weekend):
            return next_day
        next_day += datetime.timedelta(days=1)
        # Safety break
        if (next_day - input_date).days > 30:
             logging.warning(f"Could not find next working day within 30 days of {input_date}. Returning original + 1.")
             return input_date + datetime.timedelta(days=1)


def calculate_end_date(start_date, duration_days, exclude_weekends=True, working_hours_config=None):
    """
    Calculates the end date based on start date and duration in working days.
    Considers days with 0 working hours (per config) as non-working days.
    (Same robust function as before)
    """
    if working_hours_config is None:
        working_hours_config = st.session_state.config['working_hours']

    if not isinstance(start_date, datetime.date) or not isinstance(duration_days, (int, float)) or duration_days <= 0:
        logging.warning(f"Invalid input for calculate_end_date: start={start_date}, duration={duration_days}")
        return None

    if duration_days < 1:
        start_day_hours = get_working_hours_for_date(start_date, working_hours_config)
        is_weekend = start_date.weekday() >= 5
        if start_day_hours > 0 and not (exclude_weekends and is_weekend):
            return start_date
        else:
            return get_next_working_day(start_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)


    duration_target = float(duration_days)
    current_date = start_date
    days_counted = 0.0
    last_valid_working_day = None

    # Ensure we start on a working day
    while True:
        day_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend = current_date.weekday() >= 5
        if day_hours > 0 and not (exclude_weekends and is_weekend):
            last_valid_working_day = current_date
            days_counted = 1.0
            break
        current_date = get_next_working_day(current_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)
        if (current_date - start_date).days > 30:
            logging.warning(f"Could not find starting working day for {start_date} in calculate_end_date.")
            return start_date

    if days_counted >= duration_target:
        return last_valid_working_day

    current_date += datetime.timedelta(days=1)
    while days_counted < duration_target:
        day_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend = current_date.weekday() >= 5
        if day_hours > 0 and not (exclude_weekends and is_weekend):
            days_counted += 1.0
            last_valid_working_day = current_date

        current_date += datetime.timedelta(days=1)
        if (current_date - start_date).days > duration_target * 7 + 30:
             logging.error(f"Potential infinite loop in calculate_end_date detected for start={start_date}, duration={duration_target}")
             return last_valid_working_day if last_valid_working_day else start_date

    return last_valid_working_day


def get_task_by_id(task_id, task_list):
    """Gets a task by its ID from the task list."""
    try:
        task_id_int = int(task_id)
        for task in task_list:
            if task.get('id') == task_id_int:
                return task
    except (ValueError, TypeError):
         logging.error(f"Invalid task_id type passed to get_task_by_id: {task_id}")
         return None
    return None

def get_role_rate(role_name):
    """Gets the hourly rate of a specific role."""
    role = st.session_state.roles.get(role_name, {})
    return role.get("rate_eur_hr", 0)

def parse_assignments(assign_input):
    """Parses and validates role assignments. Always returns a list."""
    # (Same as before)
    if isinstance(assign_input, list):
        valid_assignments = []
        for assign in assign_input:
            if isinstance(assign, dict) and 'role' in assign and 'allocation' in assign:
                try:
                    allocation_val = float(assign['allocation'])
                    if 0 <= allocation_val <= 100:
                        valid_assignments.append({'role': assign['role'], 'allocation': allocation_val})
                    else: logging.warning(f"Invalid allocation value {allocation_val} for role {assign['role']}. Skipping.")
                except (ValueError, TypeError): logging.warning(f"Non-numeric allocation for role {assign['role']}. Skipping.")
            else: logging.warning(f"Invalid assignment item format found: {assign}. Skipping.")
        return valid_assignments
    elif isinstance(assign_input, str) and assign_input.strip():
        try:
            assignments = json.loads(assign_input)
            return parse_assignments(assignments)
        except (json.JSONDecodeError, TypeError): logging.warning(f"Could not parse assignments string: {assign_input}")
        return []
    if assign_input is not None: logging.warning(f"Invalid input type for parse_assignments: {type(assign_input)}. Returning empty list.")
    return []

def compute_task_working_hours(start_date: datetime.date, end_date: datetime.date, working_hours_config: dict, exclude_weekends: bool) -> float:
    """Calculates total working hours between two dates using get_working_hours_for_date."""
    # (Same as before)
    if not isinstance(start_date, datetime.date) or not isinstance(end_date, datetime.date) or start_date > end_date: return 0.0
    total_hours = 0.0
    current_date = start_date
    while current_date <= end_date:
        is_weekend = current_date.weekday() >= 5
        if not exclude_weekends or (exclude_weekends and not is_weekend):
             day_hours = get_working_hours_for_date(current_date, working_hours_config)
             total_hours += day_hours
        current_date += datetime.timedelta(days=1)
    return total_hours

def calculate_task_cost_by_schedule(start_date, end_date, assignments_list, working_hours_config, exclude_weekends):
    """Calculates task cost based on duration, assignments, rates, and working hours."""
    # (Same as before)
    if not isinstance(start_date, datetime.date) or not isinstance(end_date, datetime.date) or start_date > end_date: return 0.0
    total_task_hours = compute_task_working_hours(start_date, end_date, working_hours_config, exclude_weekends)
    total_cost = 0.0
    valid_assignments = parse_assignments(assignments_list)
    for assign in valid_assignments:
        role = assign.get('role'); allocation = assign.get('allocation')
        if role and allocation is not None:
            hourly_rate = get_role_rate(role)
            role_hours = total_task_hours * (allocation / 100.0)
            total_cost += role_hours * hourly_rate
    return total_cost

def parse_dependencies(dep_input):
    """Parses and validates dependencies. Always returns a list of integers."""
    # (Same as before)
    if isinstance(dep_input, list):
        valid_deps = []
        for d in dep_input:
            try: valid_deps.append(int(d))
            except (ValueError, TypeError): logging.warning(f"Invalid dependency format in list: {d}. Skipping.")
        return valid_deps
    elif isinstance(dep_input, str) and dep_input.strip():
        try:
            deps = json.loads(dep_input)
            if isinstance(deps, list): return parse_dependencies(deps)
            else: logging.warning(f"Dependencies string '{dep_input}' did not decode to a list.")
            return []
        except (json.JSONDecodeError, TypeError): logging.warning(f"Could not parse dependencies string: {dep_input}")
        return []
    if dep_input is not None and not isinstance(dep_input, list): logging.warning(f"Invalid input type for parse_dependencies: {type(dep_input)}. Returning empty list.")
    return []

def get_task_name(task_id, task_list):
    """Gets the name of a task by its ID."""
    task = get_task_by_id(task_id, task_list)
    return task.get('name', f"ID {task_id}?") if task else f"ID {task_id}?"

def format_dependencies_display(dep_str, task_list):
    """Formats dependencies list to show names instead of IDs."""
    dep_list = parse_dependencies(dep_str)
    return ", ".join([get_task_name(dep_id, task_list) for dep_id in dep_list]) if dep_list else "None"

def format_assignments_display(assignments_list):
    """Formats assignments list for readable display."""
    valid_assignments = parse_assignments(assignments_list)
    if not valid_assignments: return "None"
    return ", ".join([f"{a.get('role','?')} ({a.get('allocation',0):.0f}%)" for a in valid_assignments])

def get_working_segments(start_date: datetime.date, duration: float, exclude_weekends: bool, working_hours_config: dict) -> list:
    """Divides a task into segments of contiguous working days for Gantt."""
    # (Same as before)
    segments = []
    if not isinstance(start_date, datetime.date) or not isinstance(duration, (int, float)) or duration <= 0: return segments
    remaining_days_target = float(duration); days_accumulated = 0.0
    current_segment_start = None; last_processed_date = None
    current_date = start_date
    while days_accumulated < remaining_days_target:
        day_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend = current_date.weekday() >= 5
        is_working_day = day_hours > 0 and not (exclude_weekends and is_weekend)
        if is_working_day:
            if current_segment_start is None: current_segment_start = current_date
            days_accumulated += 1.0; last_processed_date = current_date
            if days_accumulated >= remaining_days_target:
                segments.append((current_segment_start, last_processed_date)); break
        elif current_segment_start is not None:
            segments.append((current_segment_start, last_processed_date)); current_segment_start = None
        current_date += datetime.timedelta(days=1)
        if (current_date - start_date).days > duration * 7 + 60:
            logging.error(f"Potential infinite loop in get_working_segments for start={start_date}, duration={duration}")
            if current_segment_start is not None and last_processed_date is not None: segments.append((current_segment_start, last_processed_date))
            break
    return segments

def get_ai_template_data():
    """Generates sample data for an AI project template."""
    # (Same as before, uses calculate_end_date which now considers working hours config)
    project_start_date = st.session_state.config.get('project_start_date', datetime.date.today())
    roles = {'Tech Lead': {"availability_percent": 100.0, "rate_eur_hr": 40.0},
             'AI Engineer': {"availability_percent": 100.0, "rate_eur_hr": 30.0}}
    tasks_structure = [
        {"id": 100, "macro": "Phase 0", "subtask": "Kick-off & Planning", "duration": 5, "assignments": [{"role": "Tech Lead", "allocation": 100}], "dependencies": [], "notes": "Align team, refine plan."},
        {"id": 1, "macro": "Phase 1", "subtask": "Benchmark Research", "duration": 3, "assignments": [{"role": "Tech Lead", "allocation": 30}, {"role": "AI Engineer", "allocation": 70}], "dependencies": [100], "notes": ""},
        {"id": 2, "macro": "Phase 1", "subtask": "Define Metrics", "duration": 2, "assignments": [{"role": "Tech Lead", "allocation": 50}, {"role": "AI Engineer", "allocation": 50}], "dependencies": [1], "notes": "Key evaluation metrics"},
        {"id": 3, "macro": "Phase 2", "subtask": "Develop Baseline Model", "duration": 10, "assignments": [{"role": "AI Engineer", "allocation": 100}], "dependencies": [2], "notes": "First functional version"}
    ]
    tasks = []; task_end_dates_map = {}; processed_ids = set()
    exclude_weekends = st.session_state.config.get('exclude_weekends', True)
    working_hours_config = st.session_state.config['working_hours']
    task_dict = {task['id']: task for task in tasks_structure}; ids_to_process = sorted(list(task_dict.keys()))
    max_iterations = len(ids_to_process) * 2; iterations = 0; calculation_ok = True
    while len(processed_ids) < len(ids_to_process) and iterations < max_iterations and calculation_ok:
        processed_in_iteration = False
        for task_id in ids_to_process:
            if task_id in processed_ids: continue
            task_data = task_dict[task_id]; dependencies = parse_dependencies(task_data.get('dependencies', []))
            deps_met = all(dep_id in processed_ids for dep_id in dependencies)
            if deps_met:
                start_date = calculate_dependent_start_date_for_scheduling(json.dumps(dependencies), task_end_dates_map, project_start_date, working_hours_config, exclude_weekends) # Use scheduling version
                if start_date is None: calculation_ok = False; break
                end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends, working_hours_config)
                if end_date is None: end_date = start_date
                final_task = task_data.copy()
                final_task['start_date'] = start_date; final_task['dependencies'] = json.dumps(dependencies)
                final_task['status'] = 'Pending'; final_task['notes'] = task_data.get('notes', '')
                final_task['parent_id'] = None; final_task['assignments'] = parse_assignments(task_data.get('assignments', []))
                final_task['phase_color'] = st.session_state.macrotasks.get(final_task.get('macro', ''), "#CCCCCC")
                final_task['name'] = f"{final_task.get('macro','No Phase')} - {final_task.get('subtask','No Subtask')}"
                tasks.append(final_task); task_end_dates_map[task_id] = end_date; processed_ids.add(task_id); processed_in_iteration = True
        if not calculation_ok: break
        iterations += 1
        if not processed_in_iteration and len(processed_ids) < len(ids_to_process):
            logging.error("Template Load: Could not resolve dependencies."); calculation_ok = False
            # Add remaining tasks with errors
            for task_id in ids_to_process:
                if task_id not in processed_ids:
                    task_data = task_dict[task_id]; start_date = project_start_date
                    end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends, working_hours_config) or start_date
                    final_task = task_data.copy(); final_task['start_date'] = start_date; final_task['dependencies'] = json.dumps(task_data.get('dependencies', []))
                    final_task['status'] = 'Pending (Dep Error?)'; final_task['notes'] = task_data.get('notes', ''); final_task['parent_id'] = None
                    final_task['assignments'] = parse_assignments(task_data.get('assignments', [])); final_task['phase_color'] = st.session_state.macrotasks.get(final_task.get('macro', ''), "#CCCCCC")
                    final_task['name'] = f"{final_task.get('macro','No Phase')} - {final_task.get('subtask','No Subtask')}"
                    tasks.append(final_task); task_end_dates_map[task_id] = end_date; processed_ids.add(task_id)
            break
    if not calculation_ok: st.error("Error calculating template dates. Data was not loaded."); return {}, [], 1
    next_id = max(task_dict.keys()) + 1 if task_dict else 1
    for task in tasks_structure:
         macro_name = task.get('macro')
         if macro_name and macro_name not in st.session_state.macrotasks: st.session_state.macrotasks[macro_name] = "#ADD8E6"
    return roles, tasks, next_id

# --- Resource Leveling Functions (Adapted from gant_generator.py) ---

def calculate_dependent_start_date_for_scheduling(dependencies_str, task_end_dates_map, default_start_date, working_hours_config, exclude_weekends):
    """Calculates the earliest possible start date based on dependencies, ensuring it's a working day."""
    dep_ids = parse_dependencies(dependencies_str)
    latest_dependency_finish = None
    if dep_ids:
        try:
            valid_end_dates = [task_end_dates_map[dep_id] for dep_id in dep_ids if dep_id in task_end_dates_map and isinstance(task_end_dates_map[dep_id], datetime.date)]
            if valid_end_dates:
                latest_dependency_finish = max(valid_end_dates)
            else:
                # Handle case where dependencies exist but have no valid end dates yet
                logging.warning(f"Dependencies {dep_ids} found, but no valid end dates available yet.")
                # Depending on strictness, could return None or default_start_date
                # Let's return default for now, assuming it will be rescheduled later if needed
                pass # Fall through to use default_start_date logic
        except KeyError as e:
            logging.error(f"Critical error: End date for dependency {e} not found.")
            return None # Cannot proceed if dependency data is missing

    earliest_start = default_start_date
    if latest_dependency_finish:
        earliest_start = latest_dependency_finish + datetime.timedelta(days=1)

    # Ensure the calculated start date is a working day
    return get_next_working_day(earliest_start, working_hours_config, exclude_weekends)


def check_hourly_availability(task_id, task_name, task_start_date, task_duration, task_assignments,
                              current_schedule_hours, max_available_hours_per_role_day,
                              working_hours_config, exclude_weekends):
    """
    Strictly checks if the sum of assigned hours exceeds the available hours for any role on any day of the task.
    Uses the planner's calculate_end_date and get_working_hours_for_date.
    """
    # Calculate the potential end date using the planner's robust function
    task_end_date = calculate_end_date(task_start_date, task_duration, exclude_weekends, working_hours_config)
    if task_end_date is None:
        logging.warning(f"[Check T{task_id}] Invalid end date calculated ({task_start_date}, {task_duration}). Cannot check availability.")
        return False # Cannot verify if end date is invalid

    current_date = task_start_date
    logging.debug(f"[Check T{task_id} '{task_name}'] Availability? {task_start_date} -> {task_end_date} ({task_duration}d)")

    while current_date <= task_end_date:
        # Get the total working hours for this specific day (handles monthly overrides)
        daily_total_working_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend = current_date.weekday() >= 5

        # Only check if it's considered a working day
        if daily_total_working_hours > 0 and not (exclude_weekends and is_weekend):
            # Get hours already scheduled for all roles on this specific date
            current_daily_scheduled = current_schedule_hours.get(current_date, defaultdict(float))
            logging.debug(f"  [Check T{task_id} @ {current_date}] Hours already scheduled: {dict(current_daily_scheduled)}")

            # Check each role assigned to the task being planned
            for assignment in task_assignments:
                role = assignment['role']
                allocation_pct = assignment['allocation']
                if allocation_pct <= 0: continue # Does not consume resources

                # Hours this task would add for the role on this day
                task_hourly_load_role_day = daily_total_working_hours * (allocation_pct / 100.0)

                # Hours THIS role already has scheduled from OTHER tasks on THIS day
                current_role_hours_day = current_daily_scheduled.get(role, 0.0)

                # MAXIMUM hours THIS role can work on THIS day (based on role availability %)
                # Note: max_available_hours_per_role_day already considers role availability %
                # The check uses the specific day's total working hours * role availability %
                role_info = st.session_state.roles.get(role, {})
                role_availability_pct = role_info.get('availability_percent', 100.0)
                max_role_hours_day = daily_total_working_hours * (role_availability_pct / 100.0)


                # *** Strict Check ***
                new_total_hours = current_role_hours_day + task_hourly_load_role_day
                tolerance = 1e-9 # Small tolerance for float comparisons

                logging.debug(f"    [Check T{task_id} @ {current_date} - {role}] Current: {current_role_hours_day:.4f}h, New Task Load: {task_hourly_load_role_day:.4f}h, Proposed Total: {new_total_hours:.4f}h, Role Limit: {max_role_hours_day:.4f}h")

                if new_total_hours > max_role_hours_day + tolerance:
                    logging.info(f"  [Check T{task_id} @ {current_date} - {role}] CONFLICT! Proposed Total ({new_total_hours:.4f}) > Role Limit ({max_role_hours_day:.4f})")
                    return False # Conflict -> Not available

        # Move to the next day
        current_date += datetime.timedelta(days=1)
        # We only need to check days up to the calculated end_date

    logging.debug(f"[Check T{task_id}] Availability OK for {task_start_date} -> {task_end_date}")
    return True # No conflicts found


def update_hourly_schedule(task_start_date, task_end_date, task_assignments, schedule_hours, working_hours_config, exclude_weekends):
    """Updates the `schedule_hours` tracker by adding the specific hours for the scheduled task."""
    if task_end_date is None: return # Should not happen if check passed

    current_date = task_start_date
    logging.debug(f"[Update Sch T?] Updating load {task_start_date} -> {task_end_date}")
    while current_date <= task_end_date:
        daily_total_working_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend = current_date.weekday() >= 5

        if daily_total_working_hours > 0 and not (exclude_weekends and is_weekend):
            if current_date not in schedule_hours: schedule_hours[current_date] = defaultdict(float)
            for assignment in task_assignments:
                role = assignment['role']; allocation_pct = assignment['allocation']
                if allocation_pct > 0:
                    task_hourly_load_role_day = daily_total_working_hours * (allocation_pct / 100.0)
                    schedule_hours[current_date][role] += task_hourly_load_role_day
                    logging.debug(f"  [Update Sch @ {current_date} - {role}] Added: {task_hourly_load_role_day:.2f}h -> New Total Day Load: {schedule_hours[current_date][role]:.2f}h")
        current_date += datetime.timedelta(days=1)


def replan_with_resource_leveling(tasks_to_plan, roles_config, config):
    """
    Replans the project using strict hourly resource leveling.
    Modifies the start_date of tasks in the input list directly.
    """
    working_hours_config = config['working_hours']
    exclude_weekends = config['exclude_weekends']
    project_start_date = config['project_start_date']

    # Calculate maximum available hours per role per day of the week (used in check_hourly_availability)
    # This map isn't strictly needed anymore as the check calculates daily max on the fly
    # but we keep the structure for potential future use or logging.
    max_available_hours_per_role_day = defaultdict(dict) # Example: {'Dev': {'Monday': 7.2, ...}}
    for role, info in roles_config.items():
        availability_pct = info.get('availability_percent', 100.0)
        default_schedule = working_hours_config.get('default',{})
        for day_name, hours in default_schedule.items():
             max_available_hours_per_role_day[role][day_name] = hours * (availability_pct / 100.0) if hours > 0 else 0.0

    # Initial sort by ID (for prioritization)
    tasks_to_plan.sort(key=lambda t: t['id'])

    task_end_dates = {} # Stores calculated end dates {task_id: end_date}
    resource_schedule_hours = {} # Tracks HOURLY load per day per role {date: {role: hours}}
    unscheduled_task_ids = [t['id'] for t in tasks_to_plan]
    task_map = {t['id']: t for t in tasks_to_plan} # Map ID to task dict for easy access

    logging.info(f"Starting resource leveling replan. Project Start Default: {project_start_date}")
    # Logging the example max map based on default schedule
    logging.info(f"Example Max Role Availability (Hours based on Default Schedule): { {r: dict(h) for r, h in max_available_hours_per_role_day.items()} }")


    MAX_ITERATIONS = len(tasks_to_plan) * 15 # Increased limit for complex leveling
    current_iteration = 0

    # Main loop: Schedules ONE task per iteration if possible
    while unscheduled_task_ids and current_iteration < MAX_ITERATIONS:
        current_iteration += 1
        scheduled_in_this_iteration = False
        task_id_to_schedule = None

        # Find the first task (by original ID) that is ready (deps met)
        for task_id in unscheduled_task_ids: # Iterates in original ID order
            task = task_map[task_id]
            dependencies = parse_dependencies(task.get('dependencies', '[]'))
            if all(dep_id in task_end_dates for dep_id in dependencies):
                task_id_to_schedule = task_id
                break # Found the next task to try scheduling

        if task_id_to_schedule is None:
            if unscheduled_task_ids:
                 # This might happen if there's a circular dependency or data issue
                 logging.error(f"Iteration {current_iteration}: No tasks ready to schedule, but {len(unscheduled_task_ids)} remain ({unscheduled_task_ids}). Check dependencies.")
            break # Exit loop if nothing can be scheduled

        # --- Try to schedule the selected task ---
        task = task_map[task_id_to_schedule]
        dependencies = parse_dependencies(task.get('dependencies', '[]'))
        duration = task.get('duration', 1.0)
        assignments = parse_assignments(task.get('assignments', []))

        # Calculate the earliest possible start based on dependencies
        earliest_start_based_on_deps = calculate_dependent_start_date_for_scheduling(
            json.dumps(dependencies), task_end_dates, project_start_date, working_hours_config, exclude_weekends
        )
        if earliest_start_based_on_deps is None:
             logging.error(f"Iteration {current_iteration}: Cannot determine dependency start date for Task {task_id_to_schedule}. Skipping.")
             # This task remains unscheduled, loop continues
             continue


        current_check_date = earliest_start_based_on_deps
        found_slot = False
        attempts = 0
        MAX_SLOT_SEARCH_DAYS = 365 * 2 # Limit search to 2 years ahead

        logging.debug(f"Iter {current_iteration}: Attempting to schedule Task {task_id_to_schedule} ('{task['name']}'). Earliest start (deps): {earliest_start_based_on_deps}")

        while not found_slot and attempts < MAX_SLOT_SEARCH_DAYS:
            attempts += 1
            # Check resource availability for the current_check_date
            is_available = check_hourly_availability(
                task_id_to_schedule, task['name'], current_check_date, duration, assignments,
                resource_schedule_hours, max_available_hours_per_role_day, # Pass the map (though check uses dynamic calc)
                working_hours_config, exclude_weekends
            )

            if is_available:
                # Slot found! Schedule the task
                task['start_date'] = current_check_date
                # Recalculate end date based on the scheduled start date
                task_end_date = calculate_end_date(current_check_date, duration, exclude_weekends, working_hours_config)
                if task_end_date is None:
                    logging.error(f"Error calculating final end date for Task {task_id_to_schedule} after finding slot. Using start date.")
                    task_end_date = current_check_date # Fallback

                # Update the resource schedule tracker with the hours consumed by this task
                update_hourly_schedule(task['start_date'], task_end_date, assignments, resource_schedule_hours, working_hours_config, exclude_weekends)

                # Store the end date for dependent tasks
                task_end_dates[task_id_to_schedule] = task_end_date

                # Mark as scheduled and remove from unscheduled list
                unscheduled_task_ids.remove(task_id_to_schedule)
                found_slot = True
                scheduled_in_this_iteration = True
                logging.info(f"Iter {current_iteration}: SCHEDULED Task {task_id_to_schedule} ('{task['name']}') | Start: {task['start_date']} | End: {task_end_date} | Duration: {duration}d")

            else:
                # Slot not available at current_check_date, try the next working day
                logging.debug(f"  Slot not found at {current_check_date} for T{task_id_to_schedule}. Trying next working day.")
                current_check_date = get_next_working_day(current_check_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)

        if not found_slot:
             logging.warning(f"Iter {current_iteration}: Could NOT find slot for Task {task_id_to_schedule} ('{task['name']}') within {MAX_SLOT_SEARCH_DAYS} days search limit. It remains unscheduled.")
             # Task stays in unscheduled_task_ids and will be retried if loop continues

    # --- End of Main Loop ---
    if unscheduled_task_ids:
        logging.warning(f"Resource leveling replan finished with {len(unscheduled_task_ids)} tasks unscheduled: {unscheduled_task_ids}")
        st.warning(f"Replanning finished, but {len(unscheduled_task_ids)} tasks could not be scheduled due to resource constraints or dependency issues: IDs {unscheduled_task_ids}")
    else:
        logging.info("Resource leveling replan completed successfully.")
        st.success("Project dates recalculated successfully using resource leveling.")

    # The input list 'tasks_to_plan' has been modified in place with new start dates.
    # No need to return a new list, the session state is updated directly.


# --- MAIN INTERFACE WITH TABS ---
st.title("🚀 Advanced Project Planner")
tab_tasks, tab_gantt, tab_deps, tab_resources, tab_costs, tab_config = st.tabs([
    "📝 Tasks", "📊 Gantt", "🔗 Dependencies", "👥 Resources", "💰 Costs", "⚙️ Settings/Data"
])

# --- Settings and Data Tab ---
with tab_config:
    st.header("⚙️ General Settings and Data Management")

    # --- Project Actions Section ---
    st.subheader("🚀 Project Actions")
    col_new, col_load_template = st.columns(2)
    with col_new:
        # Button to create an empty project (with confirmation)
        if st.button("✨ Create New Empty Project", help="Deletes all current tasks and roles."):
            if 'confirm_new' not in st.session_state or not st.session_state.confirm_new:
                st.session_state.confirm_new = True
                st.warning("Are you sure? All data will be deleted. Press again to confirm.")
            else:
                # Reset session state
                st.session_state.tasks = []
                st.session_state.roles = {}
                st.session_state.macrotasks = {}
                st.session_state.last_macro = None
                st.session_state.next_task_id = 1
                st.session_state.config = { # Reset config
                    'project_start_date': datetime.date.today(), 'exclude_weekends': True,
                    'working_hours': {'default': {"Monday": 9.0, "Tuesday": 9.0, "Wednesday": 9.0,"Thursday": 9.0, "Friday": 7.0, "Saturday": 0.0, "Sunday": 0.0}, 'monthly_overrides': {}},
                    'profit_margin_percent': 0.0}
                st.success("Empty project created.")
                del st.session_state.confirm_new
                st.rerun()

    with col_load_template:
        # Button to load AI template (with confirmation)
        if st.button("📋 Load AI Template", help="Loads a sample template, replacing current data."):
            if 'confirm_load' not in st.session_state or not st.session_state.confirm_load:
                st.session_state.confirm_load = True
                st.warning("Are you sure? Current data will be replaced. Press again to confirm.")
            else:
                logging.info("Loading AI template via button.")
                template_result = get_ai_template_data()
                if template_result[1]:
                    default_roles, default_tasks, default_next_id = template_result
                    st.session_state.roles = default_roles
                    st.session_state.tasks = default_tasks # Load template tasks
                    st.session_state.next_task_id = default_next_id
                    # Optionally reset config or keep current
                    # st.session_state.config = ... # Reset if needed
                    st.success("AI template loaded.")
                    del st.session_state.confirm_load
                    st.rerun()
                else:
                    del st.session_state.confirm_load


    st.divider()

    # --- General Project Settings Section ---
    st.subheader("🔧 General Project Settings")
    config_changed_flag = False # Use a different name to avoid scope issues

    # Project Start Date
    current_start_date_cfg = st.session_state.config.get('project_start_date', datetime.date.today())
    new_start_date_cfg = st.date_input(
        "Default Project Start Date", value=current_start_date_cfg, key="project_start_date_config",
        help="Default date used for new tasks without dependencies and as earliest start for leveled plan."
    )
    if new_start_date_cfg != current_start_date_cfg:
        st.session_state.config['project_start_date'] = new_start_date_cfg
        config_changed_flag = True
        st.success(f"Project start date updated to {new_start_date_cfg.strftime('%Y-%m-%d')}.")

    # Exclude Weekends
    exclude_weekends_current_cfg = st.session_state.config.get('exclude_weekends', True)
    exclude_weekends_new_cfg = st.checkbox(
        "Exclude Saturdays and Sundays from duration calculation", value=exclude_weekends_current_cfg, key="exclude_weekends_toggle"
    )
    if exclude_weekends_new_cfg != exclude_weekends_current_cfg:
        st.session_state.config['exclude_weekends'] = exclude_weekends_new_cfg
        config_changed_flag = True
        st.success(f"Weekend exclusion {'enabled' if exclude_weekends_new_cfg else 'disabled'}.")

    if config_changed_flag:
        st.info("Change detected in general settings. Consider recalculating project dates with resource leveling.")
        st.rerun()

    st.divider()

    # --- Role Management Section ---
    # (No changes needed here, same as before)
    st.subheader("👥 Role Management")
    roles_col1, roles_col2 = st.columns([0.4, 0.6])
    with roles_col1:
        with st.form("role_form_config"):
            role_name = st.text_input("Role Name (New or Existing to Update)")
            role_rate = st.number_input("Hourly Rate (€/hour)", min_value=0.0, step=1.0, format="%.2f")
            role_availability = st.number_input("Availability (%)", min_value=0.0, max_value=100.0, value=100.0, step=1.0, help="Max % of daily working hours this role can be allocated.")
            submitted_role = st.form_submit_button("Add/Update Role")
            if submitted_role and role_name.strip():
                st.session_state.roles[role_name.strip()] = {"availability_percent": role_availability, "rate_eur_hr": role_rate}
                st.success(f"Role '{role_name.strip()}' added/updated.")
                st.rerun()
            elif submitted_role: st.error("Role name cannot be empty.")
        st.markdown("---")
        role_to_delete = st.selectbox("Delete Role", options=[""] + sorted(list(st.session_state.roles.keys())), index=0, key="delete_role_select_config", help="Select a role to delete (only if not assigned).")
        if st.button("Delete Selected Role", key="delete_role_btn_config") and role_to_delete:
            role_in_use = any(assign.get('role') == role_to_delete for task in st.session_state.tasks for assign in parse_assignments(task.get('assignments', [])))
            if role_in_use: st.warning(f"Role '{role_to_delete}' is assigned to tasks and cannot be deleted.")
            else: del st.session_state.roles[role_to_delete]; st.success(f"Role '{role_to_delete}' deleted."); st.rerun()
    with roles_col2:
        st.write("**Current Roles (Editable: Rate, Availability):**")
        if st.session_state.roles:
            roles_list = [{"Role": name, "Hourly Rate (€/h)": data.get("rate_eur_hr", 0), "Availability (%)": data.get("availability_percent", 100)} for name, data in st.session_state.roles.items()]
            roles_editor_df = pd.DataFrame(roles_list)
            original_roles_editor_df = roles_editor_df.copy()
            edited_roles_df = st.data_editor(roles_editor_df, key="roles_editor", use_container_width=True, hide_index=True,
                column_config={ "Role": st.column_config.TextColumn(disabled=True), "Hourly Rate (€/h)": st.column_config.NumberColumn(required=True, min_value=0.0, format="%.2f €"), "Availability (%)": st.column_config.NumberColumn(required=True, min_value=0.0, max_value=100.0, format="%.1f %%")}, num_rows="fixed")
            if not edited_roles_df.equals(original_roles_editor_df):
                st.info("Changes detected in roles. Updating...")
                roles_updated = False
                for index, row in edited_roles_df.iterrows():
                    role_name = row["Role"]; original_row = original_roles_editor_df.iloc[index]
                    if row["Hourly Rate (€/h)"] != original_row["Hourly Rate (€/h)"] or row["Availability (%)"] != original_row["Availability (%)"]:
                        if role_name in st.session_state.roles:
                            st.session_state.roles[role_name]["rate_eur_hr"] = row["Hourly Rate (€/h)"]
                            st.session_state.roles[role_name]["availability_percent"] = row["Availability (%)"]
                            roles_updated = True
                        else: logging.error(f"Role '{role_name}' found in edited roles table but not in session state.")
                if roles_updated: st.success("Roles updated."); st.rerun()
                else: st.info("No net changes detected to save.")
        else: st.info("No roles defined.")

    st.divider()

    # --- Macro Task Section ---
    # (No changes needed here, same as before)
    with st.expander("➕ Manage Macro Tasks (Phases)", expanded=False):
        st.subheader("Define and Edit Macro Tasks / Phases")
        macro_form_col, macro_table_col = st.columns(2)
        with macro_form_col:
            with st.form("macro_tasks_form", clear_on_submit=True):
                macro_name_new = st.text_input("New Macro Task / Phase Name")
                macro_color_new = st.color_picker("Associated Color", value="#ADD8E6", key="macro_color_picker_new")
                submitted_macro = st.form_submit_button("Add New Macro/Phase")
                if submitted_macro:
                    if not macro_name_new or not macro_name_new.strip(): st.error("Macro task/phase name is required.")
                    elif macro_name_new.strip() in st.session_state.macrotasks: st.warning(f"Macro/phase '{macro_name_new.strip()}' already exists.")
                    else: st.session_state.macrotasks[macro_name_new.strip()] = macro_color_new; st.success(f"Macro Task/Phase '{macro_name_new.strip()}' added."); st.rerun()
            st.markdown("---")
            macro_to_delete = st.selectbox("Delete Macro Task / Phase", options=[""] + sorted(list(st.session_state.macrotasks.keys())), index=0, key="delete_macro_select")
            if st.button("Delete Selected Macro/Phase", key="delete_macro_btn") and macro_to_delete:
                macro_in_use = any(task.get('macro') == macro_to_delete for task in st.session_state.tasks)
                if macro_in_use: st.warning(f"Macro '{macro_to_delete}' is assigned to tasks. Cannot delete. Edit tasks first.")
                else: del st.session_state.macrotasks[macro_to_delete]; st.success(f"Macro Task/Phase '{macro_to_delete}' deleted."); st.rerun()
        with macro_table_col:
            st.write("**Macro Tasks / Phases (Editable: Color):**")
            if st.session_state.macrotasks:
                macros_list = [{"Macro/Phase": name, "Color": color} for name, color in st.session_state.macrotasks.items()]
                macros_editor_df = pd.DataFrame(macros_list); original_macros_editor_df = macros_editor_df.copy()
                edited_macros_df = st.data_editor(macros_editor_df, key="macros_editor", use_container_width=True, hide_index=True, column_config={"Macro/Phase": st.column_config.TextColumn(disabled=True), "Color": st.column_config.TextColumn(required=True, help="Edit hex color code (e.g., #FF0000).")}, num_rows="fixed")
                if not edited_macros_df.equals(original_macros_editor_df):
                    st.info("Changes detected in macro colors. Updating...")
                    macros_updated = False; tasks_to_update = False
                    for index, row in edited_macros_df.iterrows():
                        macro_name = row["Macro/Phase"]; new_color = row["Color"]
                        if new_color != original_macros_editor_df.iloc[index]["Color"]:
                            if macro_name in st.session_state.macrotasks: st.session_state.macrotasks[macro_name] = new_color; macros_updated = True; tasks_to_update = True
                            else: logging.error(f"Macro '{macro_name}' found in edited macros table but not in session state.")
                    if tasks_to_update:
                        for i, task in enumerate(st.session_state.tasks):
                            task_macro = task.get('macro')
                            if task_macro in st.session_state.macrotasks:
                                new_task_color = st.session_state.macrotasks[task_macro]
                                if st.session_state.tasks[i].get('phase_color') != new_task_color: st.session_state.tasks[i]['phase_color'] = new_task_color
                    if macros_updated: st.success("Macro colors updated."); st.rerun()
                    else: st.info("No net changes detected to save.")
            else: st.info("No macro tasks defined.")

    st.divider()

    # --- Working Hours Configuration Section ---
    # (No changes needed here, same as before)
    st.subheader("🕒 Working Hours Configuration")
    hours_config_changed_flag = False # Use different name
    days_of_week_hrs = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    st.markdown("**Default Schedule (Applies if no specific monthly schedule exists)**")
    cols_days_default_hrs = st.columns(len(days_of_week_hrs))
    default_hours_cfg = st.session_state.config['working_hours'].get('default', {})
    for i, day in enumerate(days_of_week_hrs):
        with cols_days_default_hrs[i]:
            current_val_hrs = default_hours_cfg.get(day, 0.0)
            new_val_hrs = st.number_input(f"{day_names_en[day][:3]}", min_value=0.0, max_value=24.0, value=current_val_hrs, step=0.5, key=f"working_default_{day}", help=f"Default hours for {day_names_en[day]}")
            if new_val_hrs != current_val_hrs: hours_config_changed_flag = True; st.session_state.config['working_hours']['default'][day] = new_val_hrs
    st.markdown("**Specific Monthly Schedules (Optional)**"); st.caption("Define different schedules here for specific months (e.g., reduced hours in summer).")
    monthly_overrides_cfg = st.session_state.config['working_hours'].get('monthly_overrides', {})
    if monthly_overrides_cfg:
        st.write("Defined specific schedules:"); overrides_list_cfg = []
        for month_num, schedule in sorted(monthly_overrides_cfg.items()):
             if isinstance(schedule, dict): schedule_str = ", ".join([f"{day_names_en[d][:3]}: {h}h" for d, h in schedule.items() if h > 0]);
             else: schedule_str = "Invalid format"
             if not schedule_str: schedule_str = "All days 0h"
             overrides_list_cfg.append({"Month": month_names_en.get(month_num, f"Month {month_num}"), "Schedule": schedule_str})
        if overrides_list_cfg: st.table(pd.DataFrame(overrides_list_cfg))
        st.markdown("---")
    col_month_select_cfg, col_month_edit_cfg = st.columns([0.3, 0.7])
    with col_month_select_cfg:
        selected_month_cfg = st.selectbox("Select Month to Add/Edit Schedule:", options=[None] + list(range(1, 13)), format_func=lambda x: month_names_en[x] if x else "Choose a month...", key="month_override_select")
    if selected_month_cfg:
        with col_month_edit_cfg:
            st.write(f"**Editing Schedule for {month_names_en[selected_month_cfg]}**")
            current_month_schedule_cfg = monthly_overrides_cfg.get(selected_month_cfg, {})
            if not isinstance(current_month_schedule_cfg, dict): current_month_schedule_cfg = {}
            new_month_schedule_cfg = {}; cols_days_month_cfg = st.columns(len(days_of_week_hrs))
            for i, day in enumerate(days_of_week_hrs):
                 with cols_days_month_cfg[i]:
                     default_val_cfg = current_month_schedule_cfg.get(day, default_hours_cfg.get(day, 0.0))
                     new_month_schedule_cfg[day] = st.number_input(f"{day_names_en[day][:3]} ({month_names_en[selected_month_cfg]})", min_value=0.0, max_value=24.0, value=default_val_cfg, step=0.5, key=f"working_month_{selected_month_cfg}_{day}", help=f"Hours for {day_names_en[day]} in {month_names_en[selected_month_cfg]}")
            col_save_cfg, col_delete_cfg, _ = st.columns([0.3, 0.4, 0.3])
            with col_save_cfg:
                if st.button(f"💾 Save {month_names_en[selected_month_cfg]} Schedule", key=f"save_month_{selected_month_cfg}"):
                    st.session_state.config['working_hours']['monthly_overrides'][selected_month_cfg] = new_month_schedule_cfg
                    st.success(f"Specific schedule for {month_names_en[selected_month_cfg]} saved."); hours_config_changed_flag = True; st.rerun()
            with col_delete_cfg:
                if selected_month_cfg in monthly_overrides_cfg:
                    if st.button(f"❌ Delete {month_names_en[selected_month_cfg]} Schedule", key=f"delete_month_{selected_month_cfg}"):
                        del st.session_state.config['working_hours']['monthly_overrides'][selected_month_cfg]
                        st.success(f"Specific schedule for {month_names_en[selected_month_cfg]} deleted."); hours_config_changed_flag = True; st.rerun()
    if hours_config_changed_flag: st.info("Change detected in hours configuration. Consider recalculating project dates.")

    st.divider()

    # --- Recalculate Dates Section ---
    st.subheader("🔄 Recalculate Plan with Resource Leveling")
    st.warning(
        "This will reschedule tasks based on dependencies AND **resource availability**. "
        "Tasks will be scheduled sequentially (by ID priority) into the earliest slot where all assigned roles have sufficient **daily hours available**, considering their max availability % and the working hours configuration (including monthly schedules). "
        "Tasks might be pushed later than dependencies alone would dictate due to resource conflicts."
    )
    if st.button("Replan with Resource Leveling", key="replan_leveled_btn"):
        if not st.session_state.tasks:
             st.info("No tasks to replan.")
        elif not st.session_state.roles:
             st.error("Cannot replan without defined roles. Please add roles first.")
        else:
            # Make a copy of tasks to potentially modify
            tasks_copy_for_replan = [t.copy() for t in st.session_state.tasks]
            logging.info("--- Starting Resource Leveling Replan ---")
            # Call the new replanning function - it modifies the list in place
            replan_with_resource_leveling(
                tasks_copy_for_replan,
                st.session_state.roles,
                st.session_state.config
            )
            # Update the session state with the modified task list
            st.session_state.tasks = tasks_copy_for_replan
            logging.info("--- Resource Leveling Replan Finished ---")
            # Rerun is handled within the replan function on success/warning
            st.rerun() # Ensure rerun happens even if only warnings occurred


    st.divider()

    # --- Profit Margin Section ---
    # (No changes needed here, same as before)
    st.subheader("📈 Profit Margin")
    current_margin_cfg = st.session_state.config.get('profit_margin_percent', 0.0)
    new_margin_cfg = st.number_input("Profit Margin (%) on Gross Cost", min_value=0.0, value=current_margin_cfg, step=1.0, format="%.2f", key="profit_margin_input", help="Enter desired margin percentage. Selling price = Gross Cost * (1 + Margin/100).")
    if new_margin_cfg != current_margin_cfg: st.session_state.config['profit_margin_percent'] = new_margin_cfg; st.success(f"Profit margin updated to {new_margin_cfg:.2f}%."); st.rerun()

    st.divider()

    # --- Export/Import Section ---
    # (No changes needed here, same as before, handles new config structure)
    st.subheader("💾 Project Data Management")
    col_export, col_import = st.columns(2)
    with col_export:
        st.write("**Export Plan**")
        export_data = {"roles": st.session_state.roles, "tasks": [], "next_task_id": st.session_state.next_task_id, "config": st.session_state.config, "macrotasks": st.session_state.macrotasks }
        for task in st.session_state.tasks:
            task_copy = task.copy()
            if isinstance(task_copy.get('start_date'), datetime.date): task_copy['start_date'] = task_copy['start_date'].isoformat()
            task_copy.pop('end_date', None) # Don't export calculated end date
            task_copy['assignments'] = parse_assignments(task_copy.get('assignments', []))
            task_copy['dependencies'] = json.dumps(parse_dependencies(task_copy.get('dependencies', '[]')))
            export_data["tasks"].append(task_copy)
        config_export = {}
        try: config_export = json.loads(json.dumps(export_data["config"], default=str))
        except Exception as e: logging.error(f"Error preparing config for export: {e}"); config_export = {"error": "Serialization failed"}
        if isinstance(config_export.get('project_start_date'), datetime.date): config_export['project_start_date'] = config_export['project_start_date'].isoformat()
        # Convert monthly override keys back to string for JSON
        if 'working_hours' in config_export and 'monthly_overrides' in config_export['working_hours']:
            config_export['working_hours']['monthly_overrides'] = {str(k): v for k, v in config_export['working_hours']['monthly_overrides'].items()}

        export_data["config"] = config_export
        try:
            json_str = json.dumps(export_data, indent=2, ensure_ascii=False)
            st.download_button(label="Download Plan (JSON)", data=json_str, file_name=f"project_plan_{datetime.date.today()}.json", mime="application/json")
        except Exception as e: st.error(f"Error generating JSON for export: {e}"); logging.error(f"JSON export error: {e}", exc_info=True)
    with col_import:
        st.write("**Import Plan**")
        uploaded_file = st.file_uploader("Upload plan JSON file", type=["json"])
        if uploaded_file is not None:
            if st.button("Confirm Import", key="confirm_import_btn"):
                try:
                    imported_data = json.load(uploaded_file)
                    if "roles" in imported_data and "tasks" in imported_data and "next_task_id" in imported_data and "config" in imported_data:
                        imported_tasks = []
                        for task_data in imported_data["tasks"]:
                            if isinstance(task_data.get('start_date'), str):
                                try: task_data['start_date'] = datetime.date.fromisoformat(task_data['start_date'])
                                except ValueError: task_data['start_date'] = datetime.date.today()
                            elif not isinstance(task_data.get('start_date'), datetime.date): task_data['start_date'] = datetime.date.today()
                            task_data.pop('end_date', None)
                            task_data['assignments'] = parse_assignments(task_data.get('assignments', []))
                            task_data['dependencies'] = json.dumps(parse_dependencies(task_data.get('dependencies', '[]')))
                            task_data.setdefault('status', 'Pending'); task_data.setdefault('notes', ''); task_data.setdefault('parent_id', None)
                            task_data.setdefault('macro', 'No Phase'); task_data.setdefault('subtask', task_data.get('name', 'No Subtask')); task_data.setdefault('phase_color', '#CCCCCC')
                            task_data['name'] = f"{task_data['macro']} - {task_data['subtask']}"
                            imported_tasks.append(task_data)
                        imported_config = imported_data["config"]
                        if isinstance(imported_config.get('project_start_date'), str):
                            try: imported_config['project_start_date'] = datetime.date.fromisoformat(imported_config['project_start_date'])
                            except ValueError: imported_config['project_start_date'] = datetime.date.today()
                        elif not isinstance(imported_config.get('project_start_date'), datetime.date): imported_config['project_start_date'] = datetime.date.today()
                        imported_overrides = imported_config.get('working_hours', {}).get('monthly_overrides', {}); valid_overrides = {}
                        if isinstance(imported_overrides, dict):
                             for k, v in imported_overrides.items():
                                 try:
                                     month_int = int(k)
                                     if 1 <= month_int <= 12 and isinstance(v, dict):
                                         valid_schedule = {}; all_days_imp = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                                         for day in all_days_imp:
                                             hours = v.get(day); hour_float = 0.0
                                             try: hour_float = float(hours);
                                             except (ValueError, TypeError): pass
                                             valid_schedule[day] = hour_float if 0.0 <= hour_float <= 24.0 else 0.0
                                         valid_overrides[month_int] = valid_schedule
                                 except (ValueError, TypeError): logging.warning(f"Invalid month key '{k}' in monthly_overrides during import.")
                        if 'working_hours' in imported_config and isinstance(imported_config['working_hours'], dict): imported_config['working_hours']['monthly_overrides'] = valid_overrides
                        else: imported_config['working_hours'] = {'default': {day: 9.0 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]} | {day: 0.0 for day in ["Saturday", "Sunday"]}, 'monthly_overrides': valid_overrides}
                        st.session_state.roles = imported_data["roles"]; st.session_state.tasks = imported_tasks; st.session_state.next_task_id = imported_data["next_task_id"]
                        st.session_state.config = imported_config; st.session_state.macrotasks = imported_data.get("macrotasks", {})
                        st.session_state.config.setdefault('project_start_date', datetime.date.today()); st.session_state.config.setdefault('exclude_weekends', True)
                        st.session_state.config.setdefault('working_hours', {'default': {day: 9.0 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]} | {day: 0.0 for day in ["Saturday", "Sunday"]}, 'monthly_overrides': {}})
                        st.session_state.config['working_hours'].setdefault('default', {day: 9.0 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]} | {day: 0.0 for day in ["Saturday", "Sunday"]})
                        st.session_state.config['working_hours'].setdefault('monthly_overrides', {})
                        st.session_state.config.setdefault('profit_margin_percent', 0.0)
                        for i, task in enumerate(st.session_state.tasks): st.session_state.tasks[i]['phase_color'] = st.session_state.macrotasks.get(task['macro'], "#CCCCCC")
                        st.success("Plan imported successfully."); st.info("Refreshing application..."); st.rerun()
                    else: st.error("JSON file missing expected structure ('roles', 'tasks', 'next_task_id', 'config').")
                except json.JSONDecodeError: st.error("Error: Uploaded file is not valid JSON.")
                except Exception as e: st.error(f"Unexpected error importing file: {e}"); logging.error(f"File import error: {e}", exc_info=True)
            else: st.info("JSON file selected. Press 'Confirm Import' to load data (replaces current plan).")


# --- Common Data Preparation (Calculations) ---
# (No change needed here - it calculates display values based on current state)
tasks_list_for_df = st.session_state.tasks
current_config = st.session_state.config
current_working_hours = current_config['working_hours']
current_exclude_weekends = current_config['exclude_weekends']
if tasks_list_for_df:
     tasks_df_list_copy = [t.copy() for t in tasks_list_for_df]
     tasks_df = pd.DataFrame(tasks_df_list_copy)
     tasks_df['duration'] = pd.to_numeric(tasks_df['duration'], errors='coerce').fillna(1.0)
     tasks_df['start_date'] = pd.to_datetime(tasks_df['start_date'], errors='coerce').dt.date
     tasks_df['assignments'] = tasks_df['assignments'].apply(parse_assignments)
     tasks_df['macro'] = tasks_df['macro'].fillna('No Phase').astype(str)
     tasks_df['subtask'] = tasks_df['subtask'].fillna('No Subtask').astype(str)
     tasks_df['name'] = tasks_df['macro'] + " - " + tasks_df['subtask']
     tasks_df['phase_color'] = tasks_df['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))
     # Calculate end_date and cost based on potentially replanned start_date
     tasks_df['end_date'] = tasks_df.apply(lambda row: calculate_end_date(row['start_date'], row['duration'], current_exclude_weekends, current_working_hours) if pd.notna(row['start_date']) else pd.NaT, axis=1)
     tasks_df['end_date'] = pd.to_datetime(tasks_df['end_date'], errors='coerce').dt.date
     tasks_df['cost'] = tasks_df.apply(lambda row: calculate_task_cost_by_schedule(row['start_date'], row['end_date'], row['assignments'], current_working_hours, current_exclude_weekends) if isinstance(row['start_date'], datetime.date) and isinstance(row['end_date'], datetime.date) else 0.0, axis=1)
     valid_end_dates = tasks_df.dropna(subset=['id', 'end_date'])
     task_end_dates_map = pd.Series(valid_end_dates.end_date.values, index=valid_end_dates.id).to_dict()
else:
     tasks_df = pd.DataFrame(columns=['id', 'macro', 'subtask', 'phase_color', 'name', 'start_date', 'duration', 'assignments', 'dependencies', 'status', 'notes', 'end_date', 'cost'])
     task_end_dates_map = {}


# --- Tasks Tab (Editing and Creation) ---
with tab_tasks:
    st.header("📝 Detailed Task Management")
    with st.expander("➕ Add New Task", expanded=False):
        with st.form("new_task_form_v3_10", clear_on_submit=True): # Updated key
            st.write("Define the details of the new task:")
            # Macro Task/Phase selection/input
            if st.session_state.macrotasks:
                macro_options = [""] + sorted(list(st.session_state.macrotasks.keys()))
                default_macro_index = macro_options.index(st.session_state.last_macro) if st.session_state.last_macro in macro_options else 0
                selected_macro = st.selectbox("Macro Task / Phase (*)", options=macro_options, index=default_macro_index, help="Select the phase or macro task.")
                phase_color = st.session_state.macrotasks.get(selected_macro, "#CCCCCC")
            else:
                selected_macro = st.text_input("Macro Task / Phase (*)", help="No macro tasks defined. Enter a name.")
                phase_color = st.color_picker("Color for this Phase", value="#ADD8E6", key="newtask_phase_color")
            # Subtask name
            subtask_name = st.text_input("Subtask Name (*)", help="Specific name for this task.")
            task_name_preview = f"{selected_macro.strip()} - {subtask_name.strip()}" if selected_macro and selected_macro.strip() and subtask_name and subtask_name.strip() else ""
            if task_name_preview: st.caption(f"Full name will be: {task_name_preview}")
            # Start date and duration
            default_new_task_start_date = st.session_state.config.get('project_start_date', datetime.date.today())
            task_start_date_manual = st.date_input("Start Date (Manual or Calculated)", value=default_new_task_start_date)
            task_duration = st.number_input("Duration (working days) (*)", min_value=0.5, step=0.5, value=1.0, format="%.1f")
            # Dependencies
            dep_options = {task['id']: f"{task['name']} (ID: {task['id']})" for task in sorted(st.session_state.tasks, key=lambda x: x.get('start_date', datetime.date.min))}
            task_dependencies_ids = st.multiselect("Dependencies (Task will start after these)", options=list(dep_options.keys()), format_func=lambda x: dep_options.get(x, f"ID {x}?"), help="Select prerequisite tasks. Start date will be calculated.")
            # Status and Notes
            task_status = st.selectbox("Initial Status", options=["Pending", "In Progress", "Completed", "Blocked"], index=0)
            task_notes = st.text_area("Additional Notes")
            # Assignments
            st.markdown("--- \n ### Assignments (Define % allocation per role)")
            assignment_data = {}
            if st.session_state.roles:
                cols = st.columns(len(st.session_state.roles))
                for i, role in enumerate(sorted(st.session_state.roles.keys())):
                    with cols[i]: assignment_data[role] = st.number_input(f"{role} (%)", min_value=0, max_value=100, value=0, step=5, key=f"newtask_alloc_{role}")
            else: st.warning("No roles defined. Go to '⚙️ Settings/Data' to add them.")
            # Submit
            submitted_new_task = st.form_submit_button("✅ Add Task to Plan")
            # Process submission
            if submitted_new_task:
                final_selected_macro = selected_macro.strip() if selected_macro else ""; final_subtask_name = subtask_name.strip() if subtask_name else ""
                if not final_selected_macro or not final_subtask_name or task_duration <= 0: st.error("Please complete required fields (*): Macro Task/Phase, Subtask, and Duration (>0).")
                else:
                    task_start_date = task_start_date_manual # Start with manual
                    if task_dependencies_ids:
                        # Recalculate start based on deps using the *scheduling* function
                        computed_start_date = calculate_dependent_start_date_for_scheduling(
                            json.dumps(task_dependencies_ids), task_end_dates_map, # Use pre-calculated end dates map
                            default_new_task_start_date, # Default if deps fail
                            st.session_state.config['working_hours'],
                            st.session_state.config['exclude_weekends']
                        )
                        if computed_start_date is not None:
                            task_start_date = computed_start_date
                            st.info(f"Start date automatically calculated: {task_start_date.strftime('%Y-%m-%d')} based on dependencies.")
                        else:
                            st.warning("Could not automatically calculate start date. Using manual date (adjusted to working day).")
                            task_start_date = get_next_working_day(task_start_date_manual, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])
                    else: # No dependencies, just ensure manual start is a working day
                        task_start_date = get_next_working_day(task_start_date_manual, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])

                    new_task_id = st.session_state.next_task_id; st.session_state.next_task_id += 1
                    st.session_state.last_macro = final_selected_macro
                    new_assignments = [{'role': role, 'allocation': alloc} for role, alloc in assignment_data.items() if alloc > 0]
                    final_phase_color = st.session_state.macrotasks.get(final_selected_macro, phase_color)
                    new_task = {'id': new_task_id, 'macro': final_selected_macro, 'subtask': final_subtask_name, 'phase_color': final_phase_color, 'name': f"{final_selected_macro} - {final_subtask_name}", 'start_date': task_start_date, 'duration': task_duration, 'assignments': new_assignments, 'dependencies': json.dumps(task_dependencies_ids), 'status': task_status, 'notes': task_notes, 'parent_id': None}
                    st.session_state.tasks.append(new_task)
                    st.success(f"Task '{new_task['name']}' (ID: {new_task_id}) added successfully.")
                    st.rerun()
    st.divider()
    # Task List Editor
    st.subheader("📋 Task List (Editable)")
    st.caption("You can edit Macro/Phase, Subtask, Start Date, Duration, Dependencies (JSON list of IDs), Status, and Notes directly. Delete rows to remove tasks.")
    if not tasks_df.empty:
        tasks_df_display = tasks_df.copy()
        if not tasks_df_display.empty:
            tasks_df_display['assignments_display'] = tasks_df_display['assignments'].apply(format_assignments_display)
            tasks_df_display['dependencies_display'] = tasks_df_display['dependencies'].apply(lambda d: format_dependencies_display(d, st.session_state.tasks))
            tasks_df_display['cost_display'] = tasks_df_display['cost'].apply(lambda x: f"€ {x:,.2f}")
            tasks_df_display['end_date_display'] = tasks_df_display['end_date'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) and isinstance(x, datetime.date) else 'N/A')
            tasks_df_display['phase_color'] = tasks_df_display['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))
            tasks_df_display['name'] = tasks_df_display['macro'] + " - " + tasks_df_display['subtask']
            original_tasks_editor_df = tasks_df_display.copy() # Keep original for comparison
            column_config_tasks = {
                "id": st.column_config.NumberColumn("ID", disabled=True), "macro": st.column_config.TextColumn("Macro/Phase", required=True),
                "subtask": st.column_config.TextColumn("Subtask", required=True), "phase_color": st.column_config.TextColumn("Color", disabled=True),
                "name": st.column_config.TextColumn("Full Name", disabled=True, width="large"), "start_date": st.column_config.DateColumn("Start Date", required=True, format="YYYY-MM-DD", help="Edit start date. Replan if dependencies change!"),
                "duration": st.column_config.NumberColumn("Duration (days)", required=True, min_value=0.5, step=0.5, format="%.1f d"), "dependencies": st.column_config.TextColumn("Dependencies (IDs JSON)", help="Edit JSON IDs, e.g., [1, 3]. Replan after!"),
                "dependencies_display": st.column_config.TextColumn("Dependencies (Names)", disabled=True), "status": st.column_config.SelectboxColumn("Status", options=["Pending", "In Progress", "Completed", "Blocked", "Pending (Dep Error?)"]),
                "notes": st.column_config.TextColumn("Notes", width="medium"), "end_date": None, "end_date_display": st.column_config.TextColumn("End Date (Calc.)", disabled=True),
                "cost": None, "cost_display": st.column_config.TextColumn("Cost (€ Calc.)", disabled=True), "assignments": None, "assignments_display": st.column_config.TextColumn("Assignments", disabled=True)}
            cols_to_display_editor = ['id', 'macro', 'subtask', 'start_date', 'duration', 'dependencies_display', 'status', 'notes', 'end_date_display', 'cost_display', 'assignments_display', 'dependencies']
            edited_df_tasks = st.data_editor(tasks_df_display[cols_to_display_editor], column_config=column_config_tasks, key="task_editor_v3_12", num_rows="dynamic", use_container_width=True, hide_index=True) # Updated key

            # --- Process Edits, Additions, and Deletions ---
            if edited_df_tasks is not None:
                try:
                    updated_tasks_from_editor = []
                    processed_ids_editor = set()
                    needs_rerun_editor = False
                    dependency_updates_info = [] # Store info about updated dependencies

                    # Get original data needed for comparison and preserving unchanged fields
                    original_assignments_editor = {task['id']: task['assignments'] for task in st.session_state.tasks}
                    original_colors_editor = {task['id']: task.get('phase_color', '#CCCCCC') for task in st.session_state.tasks}
                    original_dependencies_editor = {task['id']: task.get('dependencies', '[]') for task in st.session_state.tasks}
                    original_task_map = {task['id']: task for task in st.session_state.tasks}

                    # Iterate through rows in the edited dataframe
                    for i, row in edited_df_tasks.iterrows():
                        task_id = row.get('id')
                        is_new_row = pd.isna(task_id) or task_id <= 0

                        if is_new_row:
                            # Handle new row addition
                            task_id = st.session_state.next_task_id
                            st.session_state.next_task_id += 1
                            current_assignments = [] # New tasks start with no assignments via editor
                            current_color = "#CCCCCC"
                            current_deps_str = '[]'
                            original_task_data = {} # No original data for new task
                            needs_rerun_editor = True # Adding a task requires rerun
                            logging.info(f"DataEditor: Detected new task row, assigning ID {task_id}")
                        else:
                            # Handle existing row (potential edit)
                            task_id = int(task_id)
                            current_assignments = original_assignments_editor.get(task_id, [])
                            current_color = original_colors_editor.get(task_id, '#CCCCCC')
                            current_deps_str = original_dependencies_editor.get(task_id, '[]')
                            original_task_data = original_task_map.get(task_id, {})

                        processed_ids_editor.add(task_id) # Mark this ID as present in the edited table

                        # --- Process edited fields ---
                        # Dependencies
                        raw_deps = row.get('dependencies')
                        deps_changed = False
                        if pd.notna(raw_deps) and raw_deps != current_deps_str:
                            if isinstance(raw_deps, str) and raw_deps.strip():
                                try:
                                    deps_list = parse_dependencies(raw_deps)
                                    deps_str = json.dumps(deps_list) # Ensure it's a valid JSON list string
                                except Exception as e:
                                    st.warning(f"Error parsing dependencies for Task ID {task_id}: {e}. Reverting to previous value.")
                                    deps_str = current_deps_str
                            else: # Handle empty or invalid input
                                deps_str = '[]'
                            if deps_str != current_deps_str:
                                deps_changed = True
                                needs_rerun_editor = True
                                logging.info(f"DataEditor: Dependency change detected for Task ID {task_id}")
                        else:
                            deps_str = current_deps_str # Keep original if no change or invalid input

                        # Other fields
                        macro_val = str(row.get("macro", "")).strip() or "No Phase"
                        subtask_val = str(row.get("subtask", "")).strip() or "No Subtask"
                        name_val = f"{macro_val} - {subtask_val}"
                        phase_color_val = st.session_state.macrotasks.get(macro_val, current_color)
                        start_date_val = pd.to_datetime(row.get('start_date'), errors='coerce').date() if pd.notna(row.get('start_date')) else (original_task_data.get('start_date') or datetime.date.today())
                        duration_val = 1.0
                        try: duration_val = max(0.5, float(row['duration'])) if pd.notna(row.get('duration')) else (original_task_data.get('duration') or 1.0)
                        except (ValueError, TypeError): duration_val = original_task_data.get('duration') or 1.0

                        status_val = str(row.get('status', original_task_data.get('status', 'Pending')))
                        notes_val = str(row.get('notes', original_task_data.get('notes', '')))

                        # Create the task dictionary for the updated list
                        task_data = {
                            'id': task_id,
                            'macro': macro_val,
                            'subtask': subtask_val,
                            'phase_color': phase_color_val,
                            'name': name_val,
                            'start_date': start_date_val,
                            'duration': duration_val,
                            'assignments': current_assignments, # Assignments are edited separately
                            'dependencies': deps_str,
                            'status': status_val,
                            'notes': notes_val,
                            'parent_id': original_task_data.get('parent_id') # Preserve parent_id if it exists
                        }
                        updated_tasks_from_editor.append(task_data)

                        # Check if any field (other than dependencies, handled above) changed
                        if not is_new_row and (
                            task_data['macro'] != original_task_data.get('macro') or
                            task_data['subtask'] != original_task_data.get('subtask') or
                            task_data['start_date'] != original_task_data.get('start_date') or
                            task_data['duration'] != original_task_data.get('duration') or
                            task_data['status'] != original_task_data.get('status') or
                            task_data['notes'] != original_task_data.get('notes')
                            ):
                            needs_rerun_editor = True
                            logging.info(f"DataEditor: Edit detected for Task ID {task_id}")


                    # --- Handle Deletions ---
                    original_ids_editor = set(original_task_map.keys())
                    deleted_ids_editor = original_ids_editor - processed_ids_editor

                    if deleted_ids_editor:
                        logging.info(f"DataEditor: Detected deletion of Task IDs: {deleted_ids_editor}")
                        needs_rerun_editor = True
                        final_task_list_editor = []
                        deleted_task_names = [original_task_map.get(del_id, {}).get('name', f'ID {del_id}') for del_id in deleted_ids_editor]

                        # Iterate through the tasks that *remain* after deletion
                        for task in updated_tasks_from_editor:
                            if task['id'] not in deleted_ids_editor:
                                current_deps = parse_dependencies(task.get('dependencies', '[]'))
                                # Find which of the deleted tasks this task depends on
                                deps_to_remove = set(current_deps) & deleted_ids_editor

                                if deps_to_remove:
                                    # Remove the dependencies on deleted tasks
                                    updated_deps = [dep for dep in current_deps if dep not in deleted_ids_editor]
                                    task['dependencies'] = json.dumps(updated_deps)
                                    # Record the change for user feedback
                                    dependency_updates_info.append(f"'{task['name']}' (ID {task['id']}): Removed dependencies on deleted task(s) {deps_to_remove}.")
                                    logging.info(f"DataEditor: Updated dependencies for Task ID {task['id']} due to deletion. Removed: {deps_to_remove}")

                                final_task_list_editor.append(task) # Keep the task (potentially with updated deps)
                        st.success(f"Tasks deleted: {', '.join(deleted_task_names)}.")
                        if dependency_updates_info:
                            st.info("Dependencies automatically updated for the following tasks:\n- " + "\n- ".join(dependency_updates_info))

                    else:
                        # No deletions, the final list is just the updated list
                        final_task_list_editor = updated_tasks_from_editor


                    # --- Apply Changes if Needed ---
                    if needs_rerun_editor:
                        # Check if the final list is actually different from the session state
                        # Convert both to comparable format (e.g., JSON string)
                        current_tasks_json = json.dumps(st.session_state.tasks, sort_keys=True, default=str)
                        final_tasks_json = json.dumps(final_task_list_editor, sort_keys=True, default=str)

                        if current_tasks_json != final_tasks_json:
                            st.session_state.tasks = final_task_list_editor
                            logging.info("DataEditor: Applying changes to session state.")
                            st.success("Task changes saved.")
                            st.rerun()
                        else:
                            logging.info("DataEditor: No net changes detected after processing edits/deletions.")
                            # Optionally show a message if only deletions happened but deps were updated
                            if deleted_ids_editor and dependency_updates_info and not any(t['id'] in processed_ids_editor for t in updated_tasks_from_editor if t['id'] not in original_ids_editor):
                                pass # Messages already shown
                            #else:
                            #    st.info("No net changes to save.")


                except Exception as e:
                    logging.error(f"Error processing data editor changes: {e}", exc_info=True)
                    st.error(f"An error occurred while processing table changes: {e}")
        else:
            st.info("No tasks to display in editor.")
    else:
        st.info("No tasks in plan. Add one or import.")

    st.divider()
    # Edit Assignments Section
    st.subheader("💼 Edit Role Assignments per Task")
    if not st.session_state.tasks: st.info("Create/import tasks first.")
    elif not st.session_state.roles: st.warning("No roles defined. Add in Settings/Data.")
    else:
        task_options_assign = {task['id']: f"{task['name']} (ID: {task['id']})" for task in sorted(st.session_state.tasks, key=lambda x: x.get('start_date', datetime.date.min))}
        selected_task_id_assign = st.selectbox("Select Task to Edit Assignments:", options=[None] + list(task_options_assign.keys()), format_func=lambda x: task_options_assign.get(x, "Choose a task..."), index=0, key="assign_task_selector")
        if selected_task_id_assign is not None:
            task_to_edit = get_task_by_id(selected_task_id_assign, st.session_state.tasks)
            if task_to_edit:
                st.write(f"**Editing Assignments for:** {task_to_edit['name']}")
                current_assignments = parse_assignments(task_to_edit.get('assignments', [])); current_allocations = {a['role']: a['allocation'] for a in current_assignments if isinstance(a, dict)}
                new_assignments_data = {}; st.write("**Define Allocation (%) for each Role:**"); cols_assign = st.columns(len(st.session_state.roles))
                for i, role in enumerate(sorted(st.session_state.roles.keys())):
                    with cols_assign[i]: default_alloc = current_allocations.get(role, 0); allocation = st.number_input(f"{role} (%)", min_value=0, max_value=100, value=int(default_alloc), step=5, key=f"alloc_{selected_task_id_assign}_{role}"); new_assignments_data[role] = allocation
                if st.button("💾 Save Assignments", key=f"save_assign_{selected_task_id_assign}"):
                    updated_assignments = [{'role': role, 'allocation': alloc} for role, alloc in new_assignments_data.items() if alloc > 0]
                    assignments_changed = False
                    for i, task in enumerate(st.session_state.tasks):
                        if task['id'] == selected_task_id_assign:
                            # Compare JSON strings for reliable list-of-dict comparison
                            if json.dumps(parse_assignments(task.get('assignments', [])), sort_keys=True) != json.dumps(updated_assignments, sort_keys=True):
                                st.session_state.tasks[i]['assignments'] = updated_assignments
                                assignments_changed = True
                            break
                    if assignments_changed: st.success(f"Assignments saved for '{task_to_edit['name']}'."); st.rerun()
                    else: st.info("No changes detected.")
            else: st.error(f"Task ID {selected_task_id_assign} not found.")


# --- Gantt Tab ---
# (No changes needed here, uses calculated data from tasks_df)
with tab_gantt:
    st.header("📊 Interactive Gantt Chart")
    if not tasks_df.empty and 'end_date' in tasks_df.columns and tasks_df['start_date'].notna().all() and tasks_df['end_date'].notna().all():
        gantt_df_source = tasks_df.copy()
        gantt_df_source['macro'] = gantt_df_source['macro'].fillna("No Phase").astype(str)
        gantt_df_source['phase_color'] = gantt_df_source['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))
        gantt_df_source['assignments_display'] = gantt_df_source['assignments'].apply(format_assignments_display)
        gantt_df_source['dependencies_display'] = gantt_df_source['dependencies'].apply(lambda d: format_dependencies_display(d, st.session_state.tasks))
        macro_colors = gantt_df_source.set_index('macro')['phase_color'].to_dict()
        plotly_data = []
        gantt_working_hours = st.session_state.config['working_hours']; gantt_exclude_weekends = st.session_state.config['exclude_weekends']
        for _, row in gantt_df_source.iterrows():
             if isinstance(row['start_date'], datetime.date) and isinstance(row['duration'], (int, float)) and row['duration'] > 0:
                 segments = get_working_segments(row['start_date'], row['duration'], gantt_exclude_weekends, gantt_working_hours)
                 for seg_start, seg_end in segments:
                      plotly_end_date = seg_end + datetime.timedelta(days=1); new_row = row.to_dict()
                      new_row['plotly_start'] = seg_start; new_row['plotly_end'] = plotly_end_date; plotly_data.append(new_row)
             else: logging.warning(f"Gantt: Skipping task ID {row['id']} ({row['name']}) due to invalid start_date or duration.")
        if plotly_data:
             segments_df = pd.DataFrame(plotly_data); segments_df['plotly_start'] = pd.to_datetime(segments_df['plotly_start']); segments_df['plotly_end'] = pd.to_datetime(segments_df['plotly_end'])
             segments_df = segments_df.sort_values(by='plotly_start')
             fig = px.timeline(segments_df, x_start="plotly_start", x_end="plotly_end", y="name", color="macro", color_discrete_map=macro_colors, title="Project Timeline", hover_name="name",
                 hover_data={"start_date": "|%Y-%m-%d", "end_date": "|%Y-%m-%d", "duration": True, "assignments_display": True, "dependencies_display": True, "status": True, "cost": ":.2f€", "notes": True, "plotly_start": False, "plotly_end": False, "macro": False, "phase_color": False, "assignments": False, "dependencies": False, "subtask": False}, custom_data=["id"])
             fig.update_layout(xaxis_title="Date", yaxis_title="Tasks", legend_title_text="Macro/Phase", yaxis=dict(autorange="reversed", tickfont=dict(size=10)), xaxis=dict(type='date', tickformat="%d-%b\n%Y"), title_x=0.5)
             st.plotly_chart(fig, use_container_width=True)
        else: st.info("Could not generate task segments for Gantt chart.")
    elif not tasks_df.empty: st.warning("Missing valid date data for Gantt chart.")
    else: st.info("Add tasks to visualize Gantt chart.")


# --- Dependencies Tab ---
# (No changes needed here)
with tab_deps:
    st.header("🔗 Dependency Visualization (Graph)")
    if not tasks_df.empty:
        try:
            dot = graphviz.Digraph(comment='Project Dependency Diagram'); dot.attr(rankdir='LR')
            task_list_for_graph = st.session_state.tasks
            status_colors_graph = {"Pending": "lightblue", "In Progress": "orange", "Completed": "lightgreen", "Blocked": "lightcoral", "Pending (Dep Error?)": "lightgrey"}
            valid_ids_for_graph = {task['id'] for task in task_list_for_graph}
            for task in task_list_for_graph:
                assign_display = format_assignments_display(task.get('assignments', []))
                node_label = f'''<{task.get('name', 'Unknown Name')}<BR/><FONT POINT-SIZE="10">ID: {task.get('id', '?')}<BR/>Dur: {task.get('duration', '?')}d | Status: {task.get('status', 'N/A')}<BR/>Assign: {assign_display}</FONT>>'''
                node_color = status_colors_graph.get(task.get('status', 'Pending'), 'lightgrey')
                dot.node(str(task['id']), label=node_label, shape='box', style='filled', fillcolor=node_color)
            for task in task_list_for_graph:
                dependencies = parse_dependencies(task.get('dependencies', '[]'))
                for dep_id in dependencies:
                    if dep_id in valid_ids_for_graph: dot.edge(str(dep_id), str(task['id']))
                    else: logging.warning(f"Graph Dep Warning: Dependency ID {dep_id} not found for edge to task {task['id']}")
            st.graphviz_chart(dot, use_container_width=True)
        except ImportError: st.error("'graphviz' library not installed/configured."); st.code("pip install graphviz"); st.info("Install Graphviz system-wide: https://graphviz.org/download/")
        except Exception as e: st.error(f"Error generating dependency graph: {e}"); logging.error(f"Dependency graph error: {e}", exc_info=True)
    else: st.info("Add tasks and dependencies to visualize graph.")


# --- Resources Tab ---
# (No changes needed here, uses calculated data from tasks_df)
with tab_resources:
    st.header("👥 Resource Workload")
    if (not tasks_df.empty and 'end_date' in tasks_df.columns and tasks_df['start_date'].notna().all() and tasks_df['end_date'].notna().all()):
        min_date = tasks_df['start_date'].min(); max_date = tasks_df['end_date'].max()
        if isinstance(min_date, datetime.date) and isinstance(max_date, datetime.date) and min_date <= max_date:
            load_data = []; resource_working_hours = st.session_state.config['working_hours']; resource_exclude_weekends = st.session_state.config['exclude_weekends']
            for _, task in tasks_df.iterrows():
                start = task['start_date']; end = task['end_date']; assignments = parse_assignments(task.get('assignments', []))
                if isinstance(start, datetime.date) and isinstance(end, datetime.date) and start <= end and assignments:
                    try: task_dates_dt = pd.date_range(start, end, freq='D'); task_dates = [d.date() for d in task_dates_dt]
                    except ValueError as e: logging.error(f"Resource Load: Error creating date range T{task['id']}: {e}"); continue
                    for date in task_dates:
                        daily_hours_capacity = get_working_hours_for_date(date, resource_working_hours); is_weekend = date.weekday() >= 5
                        if (resource_exclude_weekends and is_weekend) or daily_hours_capacity <= 0: continue
                        for assign in assignments:
                            role = assign.get('role'); allocation = assign.get('allocation')
                            if role and allocation is not None and allocation > 0:
                                load = daily_hours_capacity * (allocation / 100.0)
                                load_data.append({'Fecha': pd.to_datetime(date), 'Rol': role, 'Carga (h)': load, 'Tarea': task['name'], 'ID Tarea': task['id']})
            if load_data:
                load_df = pd.DataFrame(load_data); load_summary = load_df.groupby(['Fecha', 'Rol'])['Carga (h)'].sum().reset_index(); load_summary = load_summary.sort_values(by=['Fecha', 'Rol'])
                st.subheader("📈 Estimated Daily Workload per Role vs Capacity")
                dates_range_capacity = pd.date_range(min_date, max_date, freq='D'); capacity_list = []
                # Calculate daily capacity based on role availability
                capacity_per_role_day = defaultdict(lambda: defaultdict(float)) # {date: {role: capacity_hours}}
                all_roles = list(st.session_state.roles.keys())

                for d_dt in dates_range_capacity:
                    d = d_dt.date()
                    daily_total_hours = get_working_hours_for_date(d, resource_working_hours)
                    is_weekend = d.weekday() >= 5
                    is_working_day = daily_total_hours > 0 and not (resource_exclude_weekends and is_weekend)

                    if is_working_day:
                        for role in all_roles:
                            role_info = st.session_state.roles.get(role, {})
                            availability_pct = role_info.get('availability_percent', 100.0)
                            role_capacity_today = daily_total_hours * (availability_pct / 100.0)
                            capacity_per_role_day[d_dt][role] = role_capacity_today
                            capacity_list.append({"Fecha": d_dt, "Rol": role, "Capacity (h)": role_capacity_today})

                capacity_df = pd.DataFrame(capacity_list)

                # Combine load and capacity for plotting
                combined_df = pd.merge(load_summary, capacity_df, on=['Fecha', 'Rol'], how='outer').fillna(0)

                # Create the bar chart for load
                fig_load = px.bar(load_summary, x='Fecha', y='Carga (h)', color='Rol',
                                  title='Estimated Workload per Role vs Daily Capacity',
                                  labels={'Carga (h)': 'Estimated Working Hours', 'Fecha': 'Date', 'Rol':'Role'},
                                  hover_name='Rol',
                                  hover_data={'Fecha': '|%Y-%m-%d', 'Carga (h)': ':.1f h'})

                # Add capacity lines for each role
                if not capacity_df.empty:
                     # Use plotly graph objects for more control over lines
                     fig_go = go.Figure(fig_load.data) # Start with the bar chart data

                     colors = px.colors.qualitative.Plotly # Get default color sequence
                     role_colors = {role: colors[i % len(colors)] for i, role in enumerate(sorted(all_roles))}

                     for i, role in enumerate(sorted(all_roles)):
                         role_capacity_df = capacity_df[capacity_df['Rol'] == role].sort_values('Fecha')
                         if not role_capacity_df.empty:
                             fig_go.add_trace(go.Scatter(
                                 x=role_capacity_df['Fecha'],
                                 y=role_capacity_df['Capacity (h)'],
                                 mode='lines',
                                 name=f'{role} Capacity',
                                 line=dict(dash='dash', color=role_colors[role], width=1.5),
                                 hoverinfo='skip' # Or customize hover text
                             ))
                     # Update layout from original figure and add specifics
                     fig_go.update_layout(
                         xaxis_title="Date",
                         yaxis_title="Working Hours",
                         legend_title="Role / Capacity",
                         barmode='stack',
                         title_x=0.5,
                         xaxis=dict(type='date', tickformat="%d-%b\n%Y"),
                         legend=dict(traceorder="reversed") # Show bars first in legend
                     )
                     st.plotly_chart(fig_go, use_container_width=True)

                else: # Fallback if capacity calculation fails
                     st.plotly_chart(fig_load, use_container_width=True)


                st.subheader("📊 Total Load Summary (Estimated Person-Hours)")
                total_hours_summary = load_df.groupby('Rol')['Carga (h)'].sum().reset_index(); total_hours_summary.rename(columns={'Carga (h)': 'Total Estimated Hours', 'Rol': 'Role'}, inplace=True)
                total_hours_summary = total_hours_summary.sort_values(by='Total Estimated Hours', ascending=False)
                st.dataframe(total_hours_summary.style.format({'Total Estimated Hours': '{:,.1f} h'}), use_container_width=True, hide_index=True)
            else: st.info("No workload data generated.")
        else: st.warning("Cannot determine project dates for workload calculation.")
    elif not tasks_df.empty: st.warning("Missing valid date data for workload calculation.")
    else: st.info("Add tasks with assignments to visualize workload.")


# --- Costs Tab ---
# (No changes needed here, uses calculated data from tasks_df)
with tab_costs:
    st.header("💰 Estimated Costs Summary")
    if not tasks_df.empty and 'cost' in tasks_df.columns and tasks_df['cost'].notna().any():
        total_gross_cost = tasks_df['cost'].sum(); profit_margin_percent = st.session_state.config.get('profit_margin_percent', 0.0)
        profit_amount = total_gross_cost * (profit_margin_percent / 100.0); total_selling_price = total_gross_cost + profit_amount
        st.subheader("Overall Financial Summary")
        cost_cols = st.columns(4)
        with cost_cols[0]: st.metric(label="Total Estimated Gross Cost", value=f"€ {total_gross_cost:,.2f}")
        with cost_cols[1]: st.metric(label="Profit Margin", value=f"{profit_margin_percent:.2f} %")
        with cost_cols[2]: st.metric(label="Estimated Profit", value=f"€ {profit_amount:,.2f}")
        with cost_cols[3]: st.metric(label="Estimated Selling Price", value=f"€ {total_selling_price:,.2f}")
        st.divider()
        st.subheader("Cost Breakdown by Role")
        cost_by_role_data = []; cost_working_hours = st.session_state.config['working_hours']; cost_exclude_weekends = st.session_state.config['exclude_weekends']
        for _, task in tasks_df.iterrows():
            assignments = parse_assignments(task.get('assignments', [])); start = task['start_date']; end = task['end_date']
            if isinstance(start, datetime.date) and isinstance(end, datetime.date) and start <= end and assignments:
                task_hours = compute_task_working_hours(start, end, cost_working_hours, cost_exclude_weekends)
                for assign in assignments:
                    role = assign.get('role'); allocation = assign.get('allocation')
                    if role and allocation is not None and allocation > 0:
                        hourly_rate = get_role_rate(role); role_hours = task_hours * (allocation / 100.0); role_cost = role_hours * hourly_rate
                        cost_by_role_data.append({'Role': role, 'Cost (€)': role_cost})
        if cost_by_role_data:
            cost_by_role_df = pd.DataFrame(cost_by_role_data); cost_by_role_summary = cost_by_role_df.groupby('Role')['Cost (€)'].sum().reset_index(); cost_by_role_summary = cost_by_role_summary.sort_values(by='Cost (€)', ascending=False)
            col_cost_table, col_cost_chart = st.columns([0.6, 0.4])
            with col_cost_table: st.write("**Total Cost per Role**"); st.dataframe(cost_by_role_summary.style.format({'Cost (€)': '€ {:,.2f}'}), use_container_width=True, hide_index=True)
            with col_cost_chart:
                if not cost_by_role_summary.empty and cost_by_role_summary['Cost (€)'].sum() > 0:
                    fig_pie = px.pie(cost_by_role_summary, values='Cost (€)', names='Role', title='Cost Distribution by Role', hole=0.3); fig_pie.update_traces(textposition='inside', textinfo='percent+label'); fig_pie.update_layout(showlegend=False, title_x=0.5, margin=dict(l=0, r=0, t=30, b=0)); st.plotly_chart(fig_pie, use_container_width=True)
                else: st.info("No positive costs for chart.")
        else: st.info("Could not calculate cost breakdown by role.")
        st.divider()
        st.subheader("Cost Breakdown by Task")
        cost_by_task_df = tasks_df[['id', 'macro', 'subtask', 'cost']].copy(); cost_by_task_df.rename(columns={'cost': 'Estimated Cost (€)', 'macro': 'Macro/Phase', 'subtask':'Subtask'}, inplace=True)
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1: unique_macros = sorted(cost_by_task_df['Macro/Phase'].unique()); selected_macros = st.multiselect("Filter by Macro/Phase:", options=unique_macros, default=[], key="filter_macro_cost")
        with filter_col2: unique_subtasks = sorted(cost_by_task_df['Subtask'].unique()); selected_subtasks = st.multiselect("Filter by Subtask:", options=unique_subtasks, default=[], key="filter_subtask_cost")
        filtered_cost_df = cost_by_task_df.copy()
        if selected_macros: filtered_cost_df = filtered_cost_df[filtered_cost_df['Macro/Phase'].isin(selected_macros)]
        if selected_subtasks: filtered_cost_df = filtered_cost_df[filtered_cost_df['Subtask'].isin(selected_subtasks)]
        filtered_cost_df = filtered_cost_df.sort_values(by='Estimated Cost (€)', ascending=False)
        st.dataframe(filtered_cost_df[['Macro/Phase', 'Subtask', 'Estimated Cost (€)']].style.format({'Estimated Cost (€)': '€ {:,.2f}'}), use_container_width=True, hide_index=True)
        total_filtered_cost = filtered_cost_df['Estimated Cost (€)'].sum(); st.info(f"**Total Cost of Filtered Tasks:** € {total_filtered_cost:,.2f}")
    elif not tasks_df.empty: st.warning("Could not calculate costs.")
    else: st.info("Add tasks with assignments and define rates to see costs.")
