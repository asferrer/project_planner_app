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
import io # For Excel export

# Basic logging setup - Change to DEBUG for more detail
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(funcName)s - %(levelname)s - %(message)s')


# --- Initial Setup and Session State ---
st.set_page_config(layout="wide", page_title="Advanced Project Planner")

# English month and day names for UI consistency
# These are already in English, which is good.
MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}
DAY_NAMES_EN = {
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
            'monthly_overrides': {} # Keys are month numbers as strings, e.g., "7" for July
        },
        'profit_margin_percent': 0.0
    }
# Ensure default values if 'config' already exists but lacks keys
st.session_state.config.setdefault('project_start_date', datetime.date.today())
st.session_state.config.setdefault('exclude_weekends', True)
if 'working_hours' not in st.session_state.config or \
   not isinstance(st.session_state.config['working_hours'], dict) or \
   'default' not in st.session_state.config['working_hours']:
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
if 'phases' not in st.session_state: # Renamed from macrotasks for clarity
    st.session_state.phases = {} # {phase_name: color}
if 'last_phase' not in st.session_state: # Renamed from last_macro
    st.session_state.last_phase = None
if 'leveled_resource_schedule' not in st.session_state: # To store output of leveling
    st.session_state.leveled_resource_schedule = {}


# --- Data Consistency Check at Startup ---
for task in st.session_state.tasks:
    task.setdefault('effort_ph', 0.0)
    task.setdefault('duration_calc_days', 0.0)
    if isinstance(task.get('dependencies'), list):
        task['dependencies'] = json.dumps(task['dependencies'])
    elif not isinstance(task.get('dependencies'), str):
        task['dependencies'] = '[]'
    else:
        try:
            parsed_deps = json.loads(task['dependencies'])
            if not isinstance(parsed_deps, list):
                task['dependencies'] = '[]'
        except (json.JSONDecodeError, TypeError):
            task['dependencies'] = '[]'

    if 'assignments' not in task:
        task['assignments'] = []
    elif isinstance(task.get('assignments'), str):
        try:
            parsed_assign = json.loads(task['assignments'])
            if isinstance(parsed_assign, list) and all(isinstance(d, dict) and 'role' in d and 'allocation' in d for d in parsed_assign):
                 task['assignments'] = parsed_assign
            else:
                 task['assignments'] = []
        except (json.JSONDecodeError, TypeError):
             task['assignments'] = []
    elif not isinstance(task.get('assignments'), list):
         task['assignments'] = []
    else:
         valid_assignments = []
         for item in task['assignments']:
             if isinstance(item, dict) and 'role' in item and 'allocation' in item:
                 try:
                     item['allocation'] = float(item['allocation'])
                     if 0 <= item['allocation'] <= 100:
                         valid_assignments.append(item)
                 except (ValueError, TypeError):
                     pass # Skip invalid allocation values
         task['assignments'] = valid_assignments
    task.setdefault('phase', 'No Phase') # Renamed from 'macro'
    task.setdefault('subtask', 'No Subtask')
    task.setdefault('phase_color', st.session_state.phases.get(task['phase'], "#CCCCCC"))
    task['name'] = f"{str(task.get('phase','No Phase')).strip()} - {str(task.get('subtask','No Subtask')).strip()}"


# --- HELPER FUNCTIONS ---

def get_working_hours_for_date(target_date: datetime.date, working_hours_config: dict) -> float:
    """
    Calculates the working hours for a specific date, considering default and monthly overrides.
    Month keys in monthly_overrides are expected to be strings (e.g., "7" for July).
    Day name keys (e.g., "Monday") are used for both default and override schedules.

    Args:
        target_date: The date for which to calculate working hours.
        working_hours_config: Dictionary containing 'default' and 'monthly_overrides' schedules.

    Returns:
        The number of working hours for the target_date.
    """
    if not isinstance(target_date, datetime.date) or not isinstance(working_hours_config, dict):
        logging.warning(f"Invalid input to get_working_hours_for_date: date={target_date}, config_type={type(working_hours_config)}")
        return 0.0

    month_str = str(target_date.month) # Key for monthly_overrides, e.g., "7"
    day_name = target_date.strftime("%A") # Full day name, e.g., "Monday"

    monthly_overrides = working_hours_config.get('monthly_overrides', {})
    default_schedule = working_hours_config.get('default', {})

    # Check for monthly override first
    if month_str in monthly_overrides and isinstance(monthly_overrides[month_str], dict):
        # logging.debug(f"Date: {target_date}, Day: {day_name}, Month: {month_str}. Using monthly override: {monthly_overrides[month_str].get(day_name, 0.0)} hours")
        return monthly_overrides[month_str].get(day_name, 0.0)
    # Fallback to default schedule
    elif isinstance(default_schedule, dict):
        # logging.debug(f"Date: {target_date}, Day: {day_name}, Month: {month_str}. Using default schedule: {default_schedule.get(day_name, 0.0)} hours")
        return default_schedule.get(day_name, 0.0)
    else:
        logging.warning(f"Default schedule not found or not a dict for {target_date}. Working hours config: {working_hours_config}")
        return 0.0

def get_next_working_day(input_date: datetime.date, working_hours_config: dict, exclude_weekends: bool) -> datetime.date:
    """
    Finds the next working day from the input_date (inclusive),
    considering working hours and weekend exclusion.

    Args:
        input_date: The starting date for the search.
        working_hours_config: Configuration for working hours.
        exclude_weekends: Boolean indicating if weekends should be strictly excluded.

    Returns:
        The next working day.
    """
    next_day = input_date
    days_checked = 0
    while days_checked <= 365 * 2: # Safety break to prevent infinite loops
        day_hours = get_working_hours_for_date(next_day, working_hours_config)

        if day_hours > 0:
            if exclude_weekends:
                if next_day.weekday() < 5: # 0-4 corresponds to Monday-Friday
                    return next_day
            else: # If not excluding weekends, any day with >0 hours is a working day
                return next_day

        next_day += datetime.timedelta(days=1)
        days_checked += 1

    logging.warning(f"Could not find next working day within 2 years of {input_date}. Returning original + 1 day.")
    return input_date + datetime.timedelta(days=1)


def calculate_estimated_duration_from_effort(effort_ph: float, assignments: list, roles_config: dict, working_hours_config: dict, exclude_weekends: bool) -> float:
    """
    Estimates task duration in working days based on effort and resource allocation.
    Uses an average daily hours capacity derived from the default schedule.

    Args:
        effort_ph: Total effort in person-hours for the task.
        assignments: List of role assignments for the task.
        roles_config: Configuration for roles (availability, rate).
        working_hours_config: Configuration for working hours.
        exclude_weekends: Boolean indicating if weekends are excluded.

    Returns:
        Estimated duration in working days (rounded to nearest 0.5).
    """
    if effort_ph <= 0 or not assignments:
        return 0.5 # Minimum duration for any task

    default_schedule_hours = working_hours_config.get('default', {})

    avg_daily_hours_sum = 0
    avg_working_days_in_week = 0
    for day_name_key, hours in default_schedule_hours.items():
        if day_name_key in DAY_NAMES_EN: # Use the English day names mapping
            day_index = list(DAY_NAMES_EN.keys()).index(day_name_key)
            is_weekend_day_for_avg = day_index >= 5

            if hours > 0:
                if not (exclude_weekends and is_weekend_day_for_avg):
                    avg_daily_hours_sum += hours
                    avg_working_days_in_week += 1

    if avg_working_days_in_week == 0:
        logging.warning("Average working days per week is 0 based on default schedule and exclude_weekends. Cannot estimate duration.")
        return 999 # Indicates an error or impossibility

    avg_daily_hours_system_capacity = avg_daily_hours_sum / avg_working_days_in_week
    if avg_daily_hours_system_capacity <= 0:
        logging.warning("Average daily system capacity is 0. Cannot estimate duration.")
        return 999

    total_weighted_role_contribution_per_day = 0
    for assign in assignments:
        role_name = assign['role']
        allocation_pct = assign.get('allocation', 0) / 100.0
        if allocation_pct <=0: continue

        role_info = roles_config.get(role_name, {})
        role_availability_pct = role_info.get('availability_percent', 100.0) / 100.0

        role_effective_hours_on_task_per_day = (avg_daily_hours_system_capacity * role_availability_pct) * allocation_pct
        total_weighted_role_contribution_per_day += role_effective_hours_on_task_per_day

    if total_weighted_role_contribution_per_day <= 0:
        logging.warning(f"Total weighted role contribution is 0 for effort {effort_ph}. Cannot estimate duration accurately.")
        return 999

    estimated_days = effort_ph / total_weighted_role_contribution_per_day
    return max(0.5, math.ceil(estimated_days * 2) / 2) # Round up to nearest 0.5

def calculate_end_date_from_effort(start_date: datetime.date, effort_ph: float, assignments: list, roles_config: dict, working_hours_config: dict, exclude_weekends: bool) -> datetime.date:
    """
    Calculates the end date of a task by simulating work day by day based on actual daily working hours and resource allocation.

    Args:
        start_date: The start date of the task.
        effort_ph: Total effort in person-hours.
        assignments: List of role assignments.
        roles_config: Configuration for roles.
        working_hours_config: Configuration for working hours.
        exclude_weekends: Boolean indicating if weekends are excluded.

    Returns:
        The calculated end date of the task.
    """
    if not isinstance(start_date, datetime.date):
        logging.error(f"Invalid start_date type for calculate_end_date_from_effort: {start_date}")
        return start_date # Or raise error

    if effort_ph <= 0 or not assignments:
        # For zero effort tasks or tasks with no assignments, end date is the next working day from start.
        return get_next_working_day(start_date, working_hours_config, exclude_weekends)

    remaining_effort = float(effort_ph)
    current_date = start_date

    # Ensure the first day is a working day
    current_date = get_next_working_day(current_date, working_hours_config, exclude_weekends)

    days_simulated = 0
    MAX_SIM_DAYS = 365 * 5 # Safety break for 5 years

    while remaining_effort > 1e-6 and days_simulated < MAX_SIM_DAYS:
        days_simulated +=1
        daily_total_working_hours_system_for_this_day = get_working_hours_for_date(current_date, working_hours_config)

        if daily_total_working_hours_system_for_this_day > 0:
            effort_done_today = 0
            for assign in assignments:
                role_name = assign['role']
                allocation_to_task_pct = assign.get('allocation', 0) / 100.0
                if allocation_to_task_pct <= 0: continue

                role_detail = roles_config.get(role_name, {})
                role_general_availability_pct = role_detail.get('availability_percent', 100.0) / 100.0

                # Max hours this role can generally work today based on system hours and their availability
                role_max_hours_today_general = daily_total_working_hours_system_for_this_day * role_general_availability_pct
                # Hours this role dedicates to *this specific task* today
                role_hours_on_this_task_today = role_max_hours_today_general * allocation_to_task_pct

                effort_done_today += role_hours_on_this_task_today

            remaining_effort -= effort_done_today
            if remaining_effort <= 1e-6:
                return current_date # Task finishes on this day

        # Move to the next working day
        current_date = get_next_working_day(current_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)


    if days_simulated >= MAX_SIM_DAYS:
        logging.error(f"calculate_end_date_from_effort exceeded MAX_SIM_DAYS for task starting {start_date} with original effort {effort_ph}. Remaining: {remaining_effort:.2f} PH. Returning last simulated date: {current_date}.")
        return current_date # Return the date where simulation stopped

    return current_date # Should be caught by remaining_effort <= 1e-6 check

def calculate_end_date_from_duration(start_date: datetime.date, duration_days: float, exclude_weekends: bool, working_hours_config: dict) -> datetime.date:
    """
    Calculates end date based on a fixed number of working days.
    This function is used when duration is fixed, not derived from effort.

    Args:
        start_date: The start date of the task.
        duration_days: The duration of the task in working days.
        exclude_weekends: Boolean indicating if weekends are excluded.
        working_hours_config: Configuration for working hours.

    Returns:
        The calculated end date.
    """
    if not isinstance(start_date, datetime.date) or not isinstance(duration_days, (int, float)) or duration_days <= 0:
        return start_date # Or raise error

    current_date = get_next_working_day(start_date, working_hours_config, exclude_weekends)
    days_counted = 0.0

    # If duration is less than a full day (e.g., 0.5), it finishes on the start day.
    if duration_days < 1.0:
        return current_date

    days_counted = 1.0 # The start_date itself is the first day if it's a working day

    # Safety limit to prevent infinite loops in case of misconfiguration
    safety_limit_days = duration_days * 7 + 60 # Allow for weekends and some buffer
    days_iterated_in_loop = 0

    while days_counted < duration_days:
        current_date = get_next_working_day(current_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)
        days_counted += 1.0
        days_iterated_in_loop +=1
        if days_iterated_in_loop > safety_limit_days :
            logging.error(f"calculate_end_date_from_duration loop exceeded safety limit for start={start_date}, duration={duration_days}. Returning current_date: {current_date}")
            return current_date
    return current_date


def get_task_by_id(task_id: int, task_list: list) -> dict | None:
    """
    Retrieves a task from a list by its ID.

    Args:
        task_id: The ID of the task to find.
        task_list: The list of tasks to search within.

    Returns:
        The task dictionary if found, else None.
    """
    try:
        task_id_int = int(task_id)
        for task in task_list:
            if task.get('id') == task_id_int:
                return task
    except (ValueError, TypeError):
         logging.error(f"Invalid task_id type passed to get_task_by_id: {task_id}")
         return None
    return None

def get_role_rate(role_name: str) -> float:
    """
    Gets the hourly rate for a given role.

    Args:
        role_name: The name of the role.

    Returns:
        The hourly rate in EUR, or 0 if not found.
    """
    role = st.session_state.roles.get(role_name, {})
    return role.get("rate_eur_hr", 0.0)

def parse_assignments(assign_input: list | str) -> list:
    """
    Parses assignment input (list or JSON string) into a standardized list of assignment dicts.

    Args:
        assign_input: The assignment data.

    Returns:
        A list of valid assignment dictionaries, e.g., [{'role': 'Dev', 'allocation': 50.0}].
    """
    if isinstance(assign_input, list):
        valid_assignments = []
        for assign in assign_input:
            if isinstance(assign, dict) and 'role' in assign and 'allocation' in assign:
                try:
                    allocation_val = float(assign['allocation'])
                    if 0 <= allocation_val <= 100:
                        valid_assignments.append({'role': assign['role'], 'allocation': allocation_val})
                except (ValueError, TypeError): pass # Ignore invalid allocation
        return valid_assignments
    elif isinstance(assign_input, str) and assign_input.strip():
        try:
            assignments = json.loads(assign_input)
            return parse_assignments(assignments) # Recursive call for parsed list
        except (json.JSONDecodeError, TypeError): pass # Ignore malformed JSON
    return []


def calculate_task_cost_by_effort(task_effort_ph: float, assignments_list: list, roles_config: dict) -> float:
    """
    Calculates the cost of a task based on its effort and the rates of assigned roles.
    The effort is distributed among roles based on their allocation percentages for this task.

    Args:
        task_effort_ph: Total effort in person-hours for the task.
        assignments_list: List of assignments for the task.
        roles_config: Configuration for roles, including hourly rates.

    Returns:
        The total calculated cost for the task.
    """
    if task_effort_ph <= 0: return 0.0
    total_cost = 0.0
    valid_assignments = parse_assignments(assignments_list) # Ensure assignments are correctly formatted

    # Sum of allocation percentages specifically for this task
    # This is used to proportionally distribute the task_effort_ph among assigned roles.
    total_task_specific_allocation_sum = sum(assign.get('allocation', 0) for assign in valid_assignments)

    if total_task_specific_allocation_sum <= 0 and valid_assignments:
        # If there are assignments but total allocation is zero, cost is zero.
        return 0.0

    if not valid_assignments:
        # No roles assigned, so no cost.
        return 0.0

    for assign in valid_assignments:
        role_name = assign.get('role')
        allocation_for_this_role_on_task = assign.get('allocation', 0) # This is a percentage for this task

        if allocation_for_this_role_on_task <= 0:
            continue # This role does not contribute effort to this task

        # Proportion of this task's total effort that this role is responsible for
        proportion_of_effort_by_role = allocation_for_this_role_on_task / total_task_specific_allocation_sum if total_task_specific_allocation_sum > 0 else 0

        # Effort in PH contributed by this role to this specific task
        effort_by_this_role_for_task = task_effort_ph * proportion_of_effort_by_role
        hourly_rate = get_role_rate(role_name)
        total_cost += effort_by_this_role_for_task * hourly_rate

    return total_cost


def parse_dependencies(dep_input: list | str) -> list[int]:
    """
    Parses dependency input (list or JSON string of IDs) into a list of integer task IDs.

    Args:
        dep_input: The dependency data.

    Returns:
        A list of integer task IDs.
    """
    if isinstance(dep_input, list):
        valid_deps = []
        for d in dep_input:
            try: valid_deps.append(int(d))
            except (ValueError, TypeError): pass # Ignore non-integer dependencies
        return valid_deps
    elif isinstance(dep_input, str) and dep_input.strip():
        try:
            deps = json.loads(dep_input)
            if isinstance(deps, list): return parse_dependencies(deps) # Recursive call
        except (json.JSONDecodeError, TypeError): pass # Ignore malformed JSON
    return []

def get_task_name(task_id: int, task_list: list) -> str:
    """
    Gets the name of a task by its ID.

    Args:
        task_id: The ID of the task.
        task_list: The list of tasks.

    Returns:
        The task name, or a placeholder if not found.
    """
    task = get_task_by_id(task_id, task_list)
    return task.get('name', f"ID {task_id} (Not Found)") if task else f"ID {task_id} (Not Found)"

def format_dependencies_display(dep_str: str, task_list: list) -> str:
    """
    Formats a JSON string of dependency IDs into a comma-separated string of task names.

    Args:
        dep_str: JSON string of dependency task IDs.
        task_list: The list of all tasks.

    Returns:
        A comma-separated string of dependency names, or "None".
    """
    dep_list = parse_dependencies(dep_str)
    return ", ".join([get_task_name(dep_id, task_list) for dep_id in dep_list]) if dep_list else "None"

def format_assignments_display(assignments_list: list) -> str:
    """
    Formats a list of assignment dictionaries into a readable string.

    Args:
        assignments_list: List of assignment dictionaries.

    Returns:
        A comma-separated string of assignments, e.g., "Dev (50%), QA (25%)", or "None".
    """
    valid_assignments = parse_assignments(assignments_list)
    if not valid_assignments: return "None"
    return ", ".join([f"{a.get('role','Unknown Role')} ({a.get('allocation',0):.0f}%)" for a in valid_assignments])


def get_working_segments_from_dates(task_start_date: datetime.date, task_end_date: datetime.date, exclude_weekends: bool, working_hours_config: dict) -> list[tuple[datetime.date, datetime.date]]:
    """
    Identifies continuous working day segments for Gantt chart rendering,
    respecting actual working hours per day and weekend exclusion rules.

    Args:
        task_start_date: The start date of the task.
        task_end_date: The end date of the task.
        exclude_weekends: If True, Saturdays and Sundays are non-working unless overridden.
        working_hours_config: The working hours configuration.

    Returns:
        A list of tuples, where each tuple is a (segment_start_date, segment_end_date).
    """
    segments = []
    if not isinstance(task_start_date, datetime.date) or \
       not isinstance(task_end_date, datetime.date) or \
       task_start_date > task_end_date:
        return segments

    current_date = task_start_date
    current_segment_start = None

    while current_date <= task_end_date:
        day_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_gantt_working_day = day_hours > 0 # Initially, if hours > 0, it's a working day

        # If exclude_weekends is True, we need to be more specific for Sat/Sun
        if exclude_weekends and current_date.weekday() >= 5: # It's a Saturday or Sunday
            # Check if there's a specific monthly override for this weekend day that makes it a working day
            month_str = str(current_date.month)
            day_name = current_date.strftime("%A")
            monthly_override_for_day = working_hours_config.get('monthly_overrides', {}).get(month_str, {}).get(day_name)

            if monthly_override_for_day is not None: # There is a specific override for this month and day
                is_gantt_working_day = monthly_override_for_day > 0 # Work if override hours > 0
            else: # No monthly override, check default schedule for this weekend day
                default_weekend_hours = working_hours_config.get('default',{}).get(day_name,0)
                is_gantt_working_day = default_weekend_hours > 0 # Work if default weekend hours > 0
            # If exclude_weekends is strictly enforced, and it's a calendar weekend,
            # it's only a working day if explicitly configured with >0 hours.
            # The above logic handles this: if hours are 0 (either by default or override), it's not a working day.

        if is_gantt_working_day:
            if current_segment_start is None:
                current_segment_start = current_date
        else: # Not a working day (or end of a segment)
            if current_segment_start is not None:
                # Segment ended on the previous day
                segments.append((current_segment_start, current_date - datetime.timedelta(days=1)))
                current_segment_start = None

        # If it's the last day of the task and a segment is open, close it.
        if current_date == task_end_date and current_segment_start is not None:
             segments.append((current_segment_start, current_date))

        current_date += datetime.timedelta(days=1)
    return segments


def get_ai_project_template_data() -> tuple[dict, list, int]:
    """
    Provides a sample AI project template with tasks and roles.
    All text is in English.

    Returns:
        A tuple containing:
        - roles_config: Dictionary of roles for the template.
        - tasks: List of task dictionaries for the template.
        - next_task_id: The next available task ID after template tasks.
    """
    project_start_date = st.session_state.config.get('project_start_date', datetime.date.today())
    roles_cfg = {
        'Tech Lead': {"availability_percent": 50.0, "rate_eur_hr": 46.0},
        'AI Engineer': {"availability_percent": 50.0, "rate_eur_hr": 27.0},
        'Senior AI Engineer': {"availability_percent": 50.0, "rate_eur_hr": 37.0} # Renamed for clarity
    }
    # Tasks structure with English names
    tasks_structure = [
        {"id": 1, "phase": "Phase 0", "subtask": "Kick-off & Planning", "effort_ph": 20, "assignments": [{"role": "Tech Lead", "allocation": 50}, {"role": "Senior AI Engineer", "allocation": 25}], "dependencies": [], "notes": "Align team, refine plan."},
        {"id": 2, "phase": "Phase 1", "subtask": "Benchmark Research", "effort_ph": 40, "assignments": [{"role": "AI Engineer", "allocation": 50}, {"role": "Senior AI Engineer", "allocation": 50}], "dependencies": [1], "notes": "Investigate SOTA models."},
        {"id": 3, "phase": "Phase 1", "subtask": "Define Metrics & Setup", "effort_ph": 16, "assignments": [{"role": "Tech Lead", "allocation": 25}, {"role": "Senior AI Engineer", "allocation": 25}], "dependencies": [2], "notes": "Key evaluation metrics and environment setup."},
        {"id": 4, "phase": "Phase 2", "subtask": "Fine-tune VLM", "effort_ph": 80, "assignments": [{"role": "AI Engineer", "allocation": 50}, {"role": "Senior AI Engineer", "allocation": 50}], "dependencies": [3], "notes": "Adapt selected Vision Language Model."},
        {"id": 5, "phase": "Phase 3", "subtask": "Develop RAG Prototype", "effort_ph": 60, "assignments": [{"role": "Tech Lead", "allocation": 25},{"role": "AI Engineer", "allocation": 50}], "dependencies": [4], "notes": "Build local Retrieval Augmented Generation system."}
    ]
    tasks = []
    task_end_dates_map = {}
    processed_ids = set()
    exclude_weekends_cfg = st.session_state.config.get('exclude_weekends', True)
    working_hours_cfg = st.session_state.config['working_hours']
    task_dict_template = {task['id']: task for task in tasks_structure}
    ids_to_process_template = sorted(list(task_dict_template.keys()))
    max_iterations_template = len(ids_to_process_template) * 2 # Simple heuristic for loop break
    iterations_template = 0
    calculation_ok_template = True

    while len(processed_ids) < len(ids_to_process_template) and iterations_template < max_iterations_template and calculation_ok_template:
        processed_in_iteration_template = False
        for task_id_template in ids_to_process_template:
            if task_id_template in processed_ids: continue

            task_data_template = task_dict_template[task_id_template]
            dependencies_template = parse_dependencies(task_data_template.get('dependencies', []))
            deps_met_template = all(dep_id in processed_ids for dep_id in dependencies_template)

            if deps_met_template:
                start_date_template = calculate_dependent_start_date_for_scheduling(
                    json.dumps(dependencies_template), task_end_dates_map, project_start_date, working_hours_cfg, exclude_weekends_cfg
                )
                if start_date_template is None: # Critical error in dependency calculation
                    calculation_ok_template = False; break

                effort_ph_template = task_data_template.get('effort_ph', 1) # Default to 1 if not specified
                assignments_template = parse_assignments(task_data_template.get('assignments', []))

                # Estimate duration based on effort (for initial display, leveling will refine)
                duration_calc_days_template = calculate_estimated_duration_from_effort(
                    effort_ph_template, assignments_template, roles_cfg, working_hours_cfg, exclude_weekends_cfg
                )
                # Calculate end date based on effort (more accurate than fixed duration for templates)
                end_date_template = calculate_end_date_from_effort(
                    start_date_template, effort_ph_template, assignments_template, roles_cfg, working_hours_cfg, exclude_weekends_cfg
                )
                if end_date_template is None: # Should not happen if start_date is valid
                    end_date_template = start_date_template # Fallback

                final_task_template = task_data_template.copy()
                final_task_template['start_date'] = start_date_template
                final_task_template['effort_ph'] = effort_ph_template
                final_task_template['duration_calc_days'] = duration_calc_days_template # Store the effort-based duration
                final_task_template['dependencies'] = json.dumps(dependencies_template)
                final_task_template['status'] = 'Pending'
                final_task_template['notes'] = task_data_template.get('notes', '')
                final_task_template['parent_id'] = None # Assuming top-level tasks for template
                final_task_template['assignments'] = assignments_template # Store parsed assignments
                final_task_template['phase_color'] = st.session_state.phases.get(final_task_template.get('phase', ''), "#CCCCCC") # Use 'phase'
                final_task_template['name'] = f"{final_task_template.get('phase','No Phase')} - {final_task_template.get('subtask','No Subtask')}"


                tasks.append(final_task_template)
                task_end_dates_map[task_id_template] = end_date_template
                processed_ids.add(task_id_template)
                processed_in_iteration_template = True

        if not calculation_ok_template: break # Exit if critical error
        iterations_template += 1
        if not processed_in_iteration_template and len(processed_ids) < len(ids_to_process_template):
            logging.error("Template Load: Could not resolve dependencies for all tasks. Possible circular dependency or data issue.")
            calculation_ok_template = False; break # Stop if stuck

    if not calculation_ok_template:
        st.error("Error calculating template dates. Data was not loaded. Check logs for details.")
        return {}, [], 1 # Return empty data

    next_id_template = max(task_dict_template.keys()) + 1 if task_dict_template else 1

    # Add phases from template to global phases if they don't exist
    for task_template_item in tasks_structure:
         phase_name_template = task_template_item.get('phase')
         if phase_name_template and phase_name_template not in st.session_state.phases:
             st.session_state.phases[phase_name_template] = "#ADD8E6" # Default color for new phases

    return roles_cfg, tasks, next_id_template

# --- Resource Leveling Functions ---

def calculate_dependent_start_date_for_scheduling(dependencies_str: str, task_end_dates_map: dict, default_start_date: datetime.date, working_hours_config: dict, exclude_weekends: bool) -> datetime.date | None:
    """
    Calculates the earliest possible start date for a task based on its dependencies' end dates.

    Args:
        dependencies_str: JSON string of dependency task IDs.
        task_end_dates_map: A map of {task_id: end_date} for already scheduled tasks.
        default_start_date: The project's default start date if no dependencies.
        working_hours_config: Working hours configuration.
        exclude_weekends: Boolean for weekend exclusion.

    Returns:
        The calculated earliest start date, or None if a critical dependency is missing.
    """
    dep_ids = parse_dependencies(dependencies_str)
    latest_dependency_finish_date = None

    if dep_ids:
        try:
            # Ensure all dependencies have valid end dates in the map
            valid_end_dates = []
            for dep_id in dep_ids:
                if dep_id not in task_end_dates_map:
                    logging.error(f"Dependency task ID {dep_id} not found in task_end_dates_map. Cannot calculate start date.")
                    return None # Critical: cannot proceed
                if not isinstance(task_end_dates_map[dep_id], datetime.date):
                    logging.error(f"End date for dependency task ID {dep_id} is not a valid date: {task_end_dates_map[dep_id]}.")
                    return None # Critical: invalid date
                valid_end_dates.append(task_end_dates_map[dep_id])

            if valid_end_dates:
                latest_dependency_finish_date = max(valid_end_dates)
        except KeyError as e:
            logging.error(f"Critical error: End date for dependency {e} not found in task_end_dates_map during scheduling: {task_end_dates_map}")
            return None # Critical error

    earliest_start = default_start_date
    if latest_dependency_finish_date:
        # Task starts the working day *after* the latest dependency finishes
        earliest_start = get_next_working_day(latest_dependency_finish_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)
    else:
        # No dependencies, task can start on the project start date (or next working day)
        earliest_start = get_next_working_day(default_start_date, working_hours_config, exclude_weekends)

    return earliest_start


def check_and_get_daily_effort_capacity(
    current_date: datetime.date,
    task_assignments: list,
    current_resource_schedule_hours: dict, # {date: {role: hours_scheduled}}
    roles_config: dict,
    working_hours_config: dict,
    exclude_weekends: bool
) -> tuple[bool, dict]:
    """
    Checks if a task can be worked on a given date and calculates the available effort (in PH)
    each assigned role can contribute to THIS task on THIS date, considering their overall availability
    and hours already scheduled for them on other tasks.

    Args:
        current_date: The date to check.
        task_assignments: Assignments for the specific task being scheduled.
        current_resource_schedule_hours: Hours already scheduled for roles on this date across all tasks.
        roles_config: Configuration for roles (availability).
        working_hours_config: General working hours configuration.
        exclude_weekends: Boolean for weekend exclusion.

    Returns:
        A tuple: (can_schedule_today, available_effort_today_by_role_for_this_task)
        - can_schedule_today (bool): True if any effort can be made on this task today.
        - available_effort_today_by_role_for_this_task (dict): {role_name: ph_available_for_this_task}
    """
    daily_system_hours_for_this_day = get_working_hours_for_date(current_date, working_hours_config)

    if not (daily_system_hours_for_this_day > 0):
        return False, {} # Not a working day according to system calendar

    available_effort_today_by_role_for_this_task = {}
    for assign in task_assignments: # Iterate through roles assigned TO THIS TASK
        role_name = assign['role']
        # Allocation percentage of this role's *available time* to *this specific task*
        allocation_to_this_task_pct = assign.get('allocation', 0) / 100.0

        if allocation_to_this_task_pct <= 0:
            available_effort_today_by_role_for_this_task[role_name] = 0.0
            continue

        role_detail = roles_config.get(role_name, {})
        # General availability of the role (e.g., 50% if they work part-time on projects)
        role_general_availability_pct = role_detail.get('availability_percent', 100.0) / 100.0

        # Max hours this role could *generally* work today based on system hours and their general availability
        role_max_possible_hours_today_general = daily_system_hours_for_this_day * role_general_availability_pct

        # Hours already scheduled for this role on *other tasks* today
        hours_already_scheduled_for_role_on_other_tasks = current_resource_schedule_hours.get(current_date, {}).get(role_name, 0.0)

        # Remaining general capacity for this role today after considering other tasks
        role_remaining_general_capacity_today = max(0, role_max_possible_hours_today_general - hours_already_scheduled_for_role_on_other_tasks)

        # Potential hours this role *could* dedicate to *this task* today, if they had full capacity
        # This is based on the task's specific allocation request for this role
        potential_hours_for_this_task_from_role = (daily_system_hours_for_this_day * role_general_availability_pct) * allocation_to_this_task_pct


        # Actual hours this role can contribute to *this task* today is limited by their remaining general capacity
        # AND the hours requested by this task's allocation for this role.
        actual_available_for_this_task = max(0, min(role_remaining_general_capacity_today, potential_hours_for_this_task_from_role))

        available_effort_today_by_role_for_this_task[role_name] = actual_available_for_this_task

    # Can schedule today if the sum of available effort for this task from all its assigned roles is > 0
    # Or, if it's a milestone (no assignments with allocation > 0), it can be "scheduled" (start/end same day)
    can_schedule_today = sum(available_effort_today_by_role_for_this_task.values()) > 1e-6 or \
                         not any(a.get('allocation',0) > 0 for a in task_assignments)
    return can_schedule_today, available_effort_today_by_role_for_this_task

def update_hourly_schedule_with_effort(
    current_date: datetime.date,
    effort_done_by_role_today: dict, # {role_name: hours_contributed_to_current_task}
    resource_schedule_hours: dict  # Master schedule: {date: {role: total_hours_scheduled_across_tasks}}
):
    """
    Updates the master resource schedule with the effort contributed by roles to a task on a given day.

    Args:
        current_date: The date the effort was performed.
        effort_done_by_role_today: Effort (in PH) done by each role on the *current task* today.
        resource_schedule_hours: The master schedule to update.
    """
    if current_date not in resource_schedule_hours:
        resource_schedule_hours[current_date] = defaultdict(float)

    for role_name, hours_contributed_to_current_task in effort_done_by_role_today.items():
        if hours_contributed_to_current_task > 0:
            resource_schedule_hours[current_date][role_name] += hours_contributed_to_current_task

def replan_with_resource_leveling(tasks_to_plan: list, roles_config: dict, project_config: dict):
    """
    Re-schedules tasks considering dependencies and daily resource capacity (effort in PH).
    This is the core resource leveling logic.

    Args:
        tasks_to_plan: A list of task dictionaries to be replanned.
        roles_config: Configuration of roles.
        project_config: General project configuration (start date, working hours, etc.).
    """
    working_hours_config = project_config['working_hours']
    exclude_weekends = project_config['exclude_weekends']
    project_start_date = project_config['project_start_date']

    # Sort tasks by ID initially; other sorting (e.g., priority) could be added.
    # For simplicity, using ID ensures a consistent processing order if other factors are equal.
    tasks_to_plan.sort(key=lambda t: t.get('id', float('inf')))

    task_end_dates = {} # Stores {task_id: actual_end_date} as tasks get scheduled
    # Master schedule tracking total hours scheduled per role per day across ALL tasks
    resource_schedule_hours = defaultdict(lambda: defaultdict(float))

    unscheduled_task_ids = [t['id'] for t in tasks_to_plan]
    task_map = {t['id']: t for t in tasks_to_plan} # For quick lookup

    logging.info(f"Starting resource leveling. Project Start Default: {project_start_date}")
    MAX_ITERATIONS_SCHEDULING = len(tasks_to_plan) * 3 + 10 # Safety break for outer loop
    current_scheduling_iteration = 0

    st.session_state.leveled_resource_schedule = {} # Clear previous leveled data

    while unscheduled_task_ids and current_scheduling_iteration < MAX_ITERATIONS_SCHEDULING:
        current_scheduling_iteration += 1
        scheduled_one_in_this_outer_iteration = False

        # Attempt to schedule tasks whose dependencies are met
        for task_id_to_attempt in list(unscheduled_task_ids): # Iterate on a copy
            if task_id_to_attempt not in task_map: continue # Task might have been removed or is invalid

            task = task_map[task_id_to_attempt]
            dependencies = parse_dependencies(task.get('dependencies', '[]'))

            # Check if all dependencies for this task are already scheduled (i.e., have an end date)
            if not all(dep_id in task_end_dates for dep_id in dependencies):
                continue # Cannot schedule this task yet, try next one

            effort_ph_total = float(task.get('effort_ph', 0.0))
            assignments = parse_assignments(task.get('assignments', []))

            # Handle Milestones (zero effort or no effective assignments)
            if effort_ph_total <= 1e-6 or not any(a.get('allocation',0) > 0 for a in assignments):
                earliest_start_for_milestone = calculate_dependent_start_date_for_scheduling(
                    json.dumps(dependencies), task_end_dates, project_start_date, working_hours_config, exclude_weekends
                )
                if earliest_start_for_milestone is None:
                    logging.error(f"Iter {current_scheduling_iteration}: Milestone T{task_id_to_attempt} dependency start calculation error.")
                    task['status'] = "Pending (Dependency Error)"
                    continue

                task['start_date'] = earliest_start_for_milestone
                task['end_date'] = earliest_start_for_milestone # Milestones start and end on the same day
                task_end_dates[task_id_to_attempt] = earliest_start_for_milestone
                if task_id_to_attempt in unscheduled_task_ids:
                    unscheduled_task_ids.remove(task_id_to_attempt)
                scheduled_one_in_this_outer_iteration = True
                task['status'] = "Pending (Leveled)"
                logging.info(f"Iter {current_scheduling_iteration}: SCHEDULED Milestone T{task_id_to_attempt} ('{task.get('name', 'N/A')}') | Start/End: {earliest_start_for_milestone}")
                continue # Move to next task in unscheduled_task_ids

            # For tasks with effort:
            earliest_start_based_on_deps = calculate_dependent_start_date_for_scheduling(
                json.dumps(dependencies), task_end_dates, project_start_date, working_hours_config, exclude_weekends
            )
            if earliest_start_based_on_deps is None:
                logging.error(f"Iter {current_scheduling_iteration}: Cannot determine dependency start for T{task_id_to_attempt}. Critical error.")
                task['status'] = "Pending (Dependency Error)"
                continue

            current_date_for_task_search = earliest_start_based_on_deps
            remaining_effort_for_task = effort_ph_total
            actual_task_start_date = None
            actual_task_end_date = None

            MAX_DAYS_TO_SCHEDULE_ONE_TASK = 365 * 3 # Max search window for a single task
            days_searched_for_this_task_scheduling = 0

            # This temporary log tracks effort for *this task only* before committing to master schedule
            temp_task_daily_effort_log = []

            logging.debug(f"Iter {current_scheduling_iteration}: Attempting T{task_id_to_attempt} ('{task.get('name', 'N/A')}'). Effort: {effort_ph_total:.1f} PH. DepStart: {earliest_start_based_on_deps}")

            # Inner loop: find days to complete this specific task
            while remaining_effort_for_task > 1e-6 and days_searched_for_this_task_scheduling < MAX_DAYS_TO_SCHEDULE_ONE_TASK:
                days_searched_for_this_task_scheduling +=1

                # Check capacity for *this task's assignments* on *this specific day*
                can_work_on_this_date, effort_capacity_by_role_today_for_this_task = check_and_get_daily_effort_capacity(
                    current_date_for_task_search, assignments, resource_schedule_hours, roles_config, working_hours_config, exclude_weekends
                )

                if can_work_on_this_date:
                    total_effort_producible_today_for_this_task = sum(effort_capacity_by_role_today_for_this_task.values())

                    if total_effort_producible_today_for_this_task > 1e-6:
                        if actual_task_start_date is None:
                            actual_task_start_date = current_date_for_task_search

                        effort_to_log_this_day_for_task = min(remaining_effort_for_task, total_effort_producible_today_for_this_task)

                        # Distribute the `effort_to_log_this_day_for_task` among contributing roles proportionally
                        effort_done_by_role_on_this_date_map = defaultdict(float)
                        if total_effort_producible_today_for_this_task > 1e-6: # Avoid division by zero
                            for role_name, role_can_do_today_for_task in effort_capacity_by_role_today_for_this_task.items():
                                if role_can_do_today_for_task > 1e-6:
                                    proportion = role_can_do_today_for_task / total_effort_producible_today_for_this_task
                                    effort_this_role_does_for_task = effort_to_log_this_day_for_task * proportion
                                    effort_done_by_role_on_this_date_map[role_name] = effort_this_role_does_for_task

                        temp_task_daily_effort_log.append({
                            'date': current_date_for_task_search,
                            'effort_by_role': dict(effort_done_by_role_on_this_date_map) # Effort for THIS task
                        })
                        remaining_effort_for_task -= effort_to_log_this_day_for_task
                        actual_task_end_date = current_date_for_task_search # Update end date as work is done

                if remaining_effort_for_task <= 1e-6:
                    break # Task completed

                current_date_for_task_search = get_next_working_day(current_date_for_task_search + datetime.timedelta(days=1), working_hours_config, exclude_weekends)

            # After inner loop: if task is fully scheduled
            if remaining_effort_for_task <= 1e-6 and actual_task_start_date and actual_task_end_date:
                task['start_date'] = actual_task_start_date
                task['end_date'] = actual_task_end_date
                task_end_dates[task_id_to_attempt] = actual_task_end_date

                # Commit this task's daily effort to the master resource schedule
                for daily_log in temp_task_daily_effort_log:
                    update_hourly_schedule_with_effort(daily_log['date'], daily_log['effort_by_role'], resource_schedule_hours)

                if task_id_to_attempt in unscheduled_task_ids:
                    unscheduled_task_ids.remove(task_id_to_attempt)
                scheduled_one_in_this_outer_iteration = True
                task['status'] = "Pending (Leveled)"
                logging.info(f"Iter {current_scheduling_iteration}: SCHEDULED T{task_id_to_attempt} ('{task.get('name', 'N/A')}') | Effort: {effort_ph_total:.1f} PH | Start: {actual_task_start_date} | End: {actual_task_end_date}")
            else:
                task['status'] = "Pending (Leveling Error)"
                logging.warning(f"Iter {current_scheduling_iteration}: Could NOT fully schedule T{task_id_to_attempt} ('{task.get('name', 'N/A')}') within search limit ({MAX_DAYS_TO_SCHEDULE_ONE_TASK} days). Remaining effort: {remaining_effort_for_task:.2f} PH. Searched until {current_date_for_task_search}")
                # Task remains in unscheduled_task_ids

        if not scheduled_one_in_this_outer_iteration and unscheduled_task_ids:
            logging.error(f"Resource Leveling: Iteration {current_scheduling_iteration} completed but no new tasks were scheduled. Unscheduled: {unscheduled_task_ids}. This might indicate a circular dependency or resource bottleneck that prevents further progress.")
            # Potentially break here or implement more sophisticated handling for deadlocks
            pass # Allow loop to continue for MAX_ITERATIONS_SCHEDULING for now


    if unscheduled_task_ids:
        logging.warning(f"Resource leveling finished with {len(unscheduled_task_ids)} tasks unscheduled: {unscheduled_task_ids}")
        st.warning(f"Replanning finished, but {len(unscheduled_task_ids)} tasks could not be fully scheduled. IDs: {unscheduled_task_ids}. Check logs for details (e.g., resource conflicts, dependency issues).")
        st.session_state.leveled_resource_schedule = {} # No valid complete schedule
        for failed_id in unscheduled_task_ids:
            if failed_id in task_map: task_map[failed_id]['status'] = "Pending (Leveling Error)"
    else:
        logging.info("Resource leveling replan completed successfully for all tasks.")
        st.success("Project dates recalculated successfully using resource leveling.")
        # Store the detailed leveled schedule for workload visualization
        st.session_state.leveled_resource_schedule = dict(resource_schedule_hours)

    # Update the main session state tasks with the replanned tasks
    final_replan_tasks = []
    all_original_task_ids_in_session = [t_orig['id'] for t_orig in st.session_state.tasks]

    for task_id_orig in all_original_task_ids_in_session:
        if task_id_orig in task_map: # If the task was part of the replan (i.e., in tasks_to_plan)
            final_replan_tasks.append(task_map[task_id_orig])
        else: # Task was not in tasks_to_plan (e.g., if replan was selective, though current impl. is all)
            original_task_obj = next((t for t in st.session_state.tasks if t['id'] == task_id_orig), None)
            if original_task_obj:
                final_replan_tasks.append(original_task_obj) # Keep original if not replanned

    st.session_state.tasks = final_replan_tasks


# --- EXCEL EXPORT FUNCTION ---
def export_cost_model_to_excel(tasks_df: pd.DataFrame, roles_data: dict, config_data: dict, phases_data: dict) -> bytes:
    """
    Generates an Excel file with project cost breakdown.
    Sheet 1: Cost per phase
    Sheet 2: Cost per task with role dedications
    Sheet 3: Project parameters (roles, rates, profit margin)

    Args:
        tasks_df: DataFrame of tasks with calculated costs.
        roles_data: Dictionary of roles and their rates/availability.
        config_data: Project configuration including profit margin.
        phases_data: Dictionary of phases (formerly macrotasks).

    Returns:
        BytesIO object containing the Excel file.
    """
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='openpyxl')

    profit_margin_percent = config_data.get('profit_margin_percent', 0.0)
    currency_format = '€#,##0.00' # Standard Euro format for Excel

    # --- Sheet 1: Cost by Phases ---
    if not tasks_df.empty and 'cost' in tasks_df.columns and 'phase' in tasks_df.columns:
        cost_per_phase_df = tasks_df.groupby('phase')['cost'].sum().reset_index()
        cost_per_phase_df.rename(columns={'phase': 'Phase', 'cost': 'Total Cost (€)'}, inplace=True)
        cost_per_phase_df['Selling Price (€)'] = cost_per_phase_df['Total Cost (€)'] * (1 + profit_margin_percent / 100.0)

        # Add Total row for Sheet 1
        total_cost_phases = cost_per_phase_df['Total Cost (€)'].sum()
        total_selling_phases = cost_per_phase_df['Selling Price (€)'].sum()
        total_row_phases = pd.DataFrame([{'Phase': 'TOTAL', 'Total Cost (€)': total_cost_phases, 'Selling Price (€)': total_selling_phases}])
        cost_per_phase_df = pd.concat([cost_per_phase_df, total_row_phases], ignore_index=True)

        cost_per_phase_df.to_excel(writer, sheet_name='Cost by Phases', index=False)

        worksheet_s1 = writer.sheets['Cost by Phases']
        cost_col_idx_s1 = cost_per_phase_df.columns.get_loc('Total Cost (€)') + 1
        sale_col_idx_s1 = cost_per_phase_df.columns.get_loc('Selling Price (€)') + 1
        for row in range(2, worksheet_s1.max_row + 1): # Apply to all data rows including TOTAL
            worksheet_s1.cell(row=row, column=cost_col_idx_s1).number_format = currency_format
            worksheet_s1.cell(row=row, column=sale_col_idx_s1).number_format = currency_format
        worksheet_s1.column_dimensions['A'].width = 45 # Phase Name
        worksheet_s1.column_dimensions['B'].width = 20 # Total Cost
        worksheet_s1.column_dimensions['C'].width = 20 # Selling Price
    else:
        pd.DataFrame(columns=['Phase', 'Total Cost (€)', 'Selling Price (€)']).to_excel(writer, sheet_name='Cost by Phases', index=False)


    # --- Sheet 2: Cost by Tasks ---
    if not tasks_df.empty:
        tasks_export_df = tasks_df[['id', 'phase', 'subtask', 'cost']].copy()
        tasks_export_df.rename(columns={
            'id': 'Task ID',
            'phase': 'Phase',
            'subtask': 'Subtask',
            'cost': 'Task Cost (€)'
        }, inplace=True)
        tasks_export_df['Task Selling Price (€)'] = tasks_export_df['Task Cost (€)'] * (1 + profit_margin_percent / 100.0)

        all_role_names = sorted(list(roles_data.keys()))
        for role_name in all_role_names:
            tasks_export_df[f'Dedication {role_name} (%)'] = 0.0 # Initialize dedication columns

        # Populate role dedications
        for index_tasks_df, task_row_df in tasks_df.iterrows():
            original_task_id = task_row_df['id']
            # Find the corresponding row in tasks_export_df
            export_df_index = tasks_export_df[tasks_export_df['Task ID'] == original_task_id].index

            if not export_df_index.empty:
                idx = export_df_index[0] # Should be unique
                assignments = parse_assignments(task_row_df.get('assignments', [])) # Get parsed assignments
                for assign in assignments:
                    role_col_name = f'Dedication {assign["role"]} (%)'
                    if role_col_name in tasks_export_df.columns:
                        tasks_export_df.loc[idx, role_col_name] = assign['allocation']

        # Add Total row for Sheet 2
        total_cost_tasks = tasks_export_df['Task Cost (€)'].sum()
        total_selling_tasks = tasks_export_df['Task Selling Price (€)'].sum()

        total_row_data_tasks = {'Task ID': 'TOTAL', 'Phase': '', 'Subtask': '', 'Task Cost (€)': total_cost_tasks, 'Task Selling Price (€)': total_selling_tasks}
        for role_name in all_role_names: # Keep dedication columns blank for total row
            total_row_data_tasks[f'Dedication {role_name} (%)'] = None # Or np.nan, or ""
        total_row_tasks_df = pd.DataFrame([total_row_data_tasks]) # Corrected variable name
        tasks_export_df = pd.concat([tasks_export_df, total_row_tasks_df], ignore_index=True)


        cols_order_sheet2 = ['Task ID', 'Phase', 'Subtask', 'Task Cost (€)', 'Task Selling Price (€)'] + \
                            [f'Dedication {rn} (%)' for rn in all_role_names]
        tasks_export_df = tasks_export_df[cols_order_sheet2] # Ensure column order
        tasks_export_df.to_excel(writer, sheet_name='Cost by Tasks', index=False)

        worksheet_s2 = writer.sheets['Cost by Tasks']
        df_columns_s2 = tasks_export_df.columns.tolist()
        cost_col_idx_s2 = df_columns_s2.index('Task Cost (€)') + 1
        sale_col_idx_s2 = df_columns_s2.index('Task Selling Price (€)') + 1
        for row in range(2, worksheet_s2.max_row + 1): # Apply to all data rows including TOTAL
            worksheet_s2.cell(row=row, column=cost_col_idx_s2).number_format = currency_format
            worksheet_s2.cell(row=row, column=sale_col_idx_s2).number_format = currency_format

        worksheet_s2.column_dimensions['A'].width = 10 # Task ID
        worksheet_s2.column_dimensions['B'].width = 40 # Phase
        worksheet_s2.column_dimensions['C'].width = 45 # Subtask
        worksheet_s2.column_dimensions['D'].width = 20 # Task Cost
        worksheet_s2.column_dimensions['E'].width = 25 # Task Selling Price
        start_col_letter_ord_s2 = ord('F') # Start of dedication columns
        for i in range(len(all_role_names)):
            col_letter = chr(start_col_letter_ord_s2 + i)
            worksheet_s2.column_dimensions[col_letter].width = 25 # Dedication columns
    else:
        # Create empty sheet if no tasks
        cols_s2_empty = ['Task ID', 'Phase', 'Subtask', 'Task Cost (€)', 'Task Selling Price (€)']
        if roles_data:
            all_role_names_s2_empty = sorted(list(roles_data.keys()))
            for role_name_s2_empty in all_role_names_s2_empty:
                cols_s2_empty.append(f'Dedication {role_name_s2_empty} (%)')
        pd.DataFrame(columns=cols_s2_empty).to_excel(writer, sheet_name='Cost by Tasks', index=False)

    # --- Sheet 3: Project Parameters ---
    params_list_for_df = []
    params_list_for_df.append(["Profit Margin (%)", profit_margin_percent / 100.0, ""]) # Store as decimal for % format
    params_list_for_df.append(["", "", ""]) # Spacer row
    params_list_for_df.append(["Project Roles:", "Availability (%)", "Rate (€/hour)"])
    for role_name, details in roles_data.items():
        params_list_for_df.append([role_name, details.get('availability_percent', 0), details.get('rate_eur_hr', 0)])

    params_df = pd.DataFrame(params_list_for_df) # No header for this sheet from df
    params_df.to_excel(writer, sheet_name='Project Parameters', index=False, header=False) # Write data without df header

    worksheet_s3 = writer.sheets['Project Parameters']
    worksheet_s3.column_dimensions['A'].width = 30 # Parameter Name / Role Name
    worksheet_s3.column_dimensions['B'].width = 20 # Value / Availability
    worksheet_s3.column_dimensions['C'].width = 20 # Rate

    # Apply formatting
    cell_margin = worksheet_s3.cell(row=1, column=2) # Profit Margin value
    cell_margin.number_format = '0.00%' # Percentage format

    # Start from row 4 for role rates (1-based index for rows in openpyxl)
    # Header "Project Roles:" is on row 3. Data starts on row 4.
    for row_idx in range(4, worksheet_s3.max_row + 1):
        cell_rate = worksheet_s3.cell(row=row_idx, column=3) # Rate column
        if isinstance(cell_rate.value, (int, float)): # Check if it's a number
            cell_rate.number_format = currency_format

    writer.close() # Correctly close writer to save data to BytesIO
    processed_data = output.getvalue()
    return processed_data

# --- MAIN INTERFACE WITH TABS ---
st.title("🚀 Advanced Project Planner")
tab_tasks, tab_gantt, tab_deps, tab_resources, tab_costs, tab_config = st.tabs([
    "📝 Tasks", "📊 Gantt", "🔗 Dependencies", "👥 Resources", "💰 Costs", "⚙️ Settings/Data"
])

# --- Settings and Data Tab (tab_config) ---
with tab_config:
    st.header("⚙️ General Settings and Data Management")

    st.subheader("🚀 Project Actions")
    col_new, col_load_template = st.columns(2)
    with col_new:
        if st.button("✨ Create New Empty Project", help="Deletes all current tasks, roles, and phases."):
            if 'confirm_new_project' not in st.session_state or not st.session_state.confirm_new_project: # Unique key
                st.session_state.confirm_new_project = True
                st.warning("Are you sure? All current project data (tasks, roles, phases) will be deleted. Press the button again to confirm.")
            else:
                st.session_state.tasks = []
                st.session_state.roles = {}
                st.session_state.phases = {} # Reset phases
                st.session_state.last_phase = None # Reset last phase
                st.session_state.next_task_id = 1
                st.session_state.leveled_resource_schedule = {}
                # Reset config to defaults
                st.session_state.config = {
                    'project_start_date': datetime.date.today(),
                    'exclude_weekends': True,
                    'working_hours': {
                        'default': {"Monday": 9.0, "Tuesday": 9.0, "Wednesday": 9.0, "Thursday": 9.0, "Friday": 7.0, "Saturday": 0.0, "Sunday": 0.0},
                        'monthly_overrides': {}
                    },
                    'profit_margin_percent': 0.0
                }
                st.success("New empty project created successfully.")
                del st.session_state.confirm_new_project # Clear confirmation flag
                st.rerun()
    with col_load_template:
        if st.button("📋 Load AI Project Template", help="Loads a sample AI project template, replacing current data."):
            if 'confirm_load_template' not in st.session_state or not st.session_state.confirm_load_template: # Unique key
                st.session_state.confirm_load_template = True
                st.warning("Are you sure? Current project data will be replaced with the template. Press the button again to confirm.")
            else:
                template_roles, template_tasks, template_next_id = get_ai_project_template_data()
                if template_tasks: # Check if template data was successfully generated
                    st.session_state.roles = template_roles
                    st.session_state.tasks = template_tasks
                    st.session_state.next_task_id = template_next_id
                    # Phases are added within get_ai_project_template_data if not existing
                    st.session_state.leveled_resource_schedule = {} # Reset schedule
                    st.success("AI Project Template loaded successfully.")
                else:
                    st.error("Failed to load AI project template. Please check logs.")
                del st.session_state.confirm_load_template # Clear confirmation flag
                st.rerun()
    st.divider()

    st.subheader("🔧 General Project Settings")
    config_changed_flag_settings = False
    current_start_date_cfg = st.session_state.config.get('project_start_date', datetime.date.today())
    new_start_date_cfg = st.date_input("Default Project Start Date", value=current_start_date_cfg, key="project_start_date_config_main")
    if new_start_date_cfg != current_start_date_cfg:
        st.session_state.config['project_start_date'] = new_start_date_cfg
        config_changed_flag_settings = True

    exclude_weekends_current_cfg = st.session_state.config.get('exclude_weekends', True)
    exclude_weekends_new_cfg = st.checkbox("Exclude Saturdays and Sundays from standard working days", value=exclude_weekends_current_cfg, key="exclude_weekends_toggle_main", help="If checked, Saturdays and Sundays are non-working unless overridden in monthly schedules.")
    if exclude_weekends_new_cfg != exclude_weekends_current_cfg:
        st.session_state.config['exclude_weekends'] = exclude_weekends_new_cfg
        config_changed_flag_settings = True

    if config_changed_flag_settings:
        st.success("General project settings updated. Consider replanning if dates are affected.")
        st.rerun() # Rerun to reflect changes immediately
    st.divider()

    st.subheader("👥 Role Management")
    roles_col1, roles_col2 = st.columns([0.4, 0.6])
    with roles_col1:
        with st.form("role_form_config"): # Renamed key for clarity
            st.write("**Add or Update Role**")
            role_name_input = st.text_input("Role Name")
            role_rate_input = st.number_input("Hourly Rate (€/hour)", min_value=0.0, format="%.2f", step=0.50)
            role_availability_input = st.number_input("General Availability (%)", 0.0, 100.0, 100.0, 1.0, help="Maximum % of daily system working hours this role can generally be allocated to project work.")
            if st.form_submit_button("💾 Add/Update Role"):
                if role_name_input.strip():
                    st.session_state.roles[role_name_input.strip()] = {"availability_percent": role_availability_input, "rate_eur_hr": role_rate_input}
                    st.success(f"Role '{role_name_input.strip()}' added/updated successfully.")
                    st.rerun()
                else:
                    st.error("Role name cannot be empty.")

        st.write("**Delete Role**")
        role_to_delete_select = st.selectbox("Select Role to Delete", [""] + sorted(list(st.session_state.roles.keys())), key="delete_role_select_config")
        if st.button("❌ Delete Selected Role", key="delete_role_btn_config") and role_to_delete_select:
            # Check if role is used in any task assignments
            is_role_assigned = any(
                any(assign.get('role') == role_to_delete_select for assign in parse_assignments(task.get('assignments', [])))
                for task in st.session_state.tasks
            )
            if is_role_assigned:
                st.warning(f"Role '{role_to_delete_select}' is currently assigned to one or more tasks and cannot be deleted. Please remove assignments first.")
            else:
                del st.session_state.roles[role_to_delete_select]
                st.success(f"Role '{role_to_delete_select}' deleted successfully.")
                st.rerun()
    with roles_col2:
        st.write("**Current Roles**")
        if st.session_state.roles:
            roles_df_display = pd.DataFrame([
                {"Role": name, "Rate (€/h)": data.get("rate_eur_hr",0), "Availability (%)": data.get("availability_percent",100)}
                for name, data in st.session_state.roles.items()
            ])
            edited_roles_df = st.data_editor(
                roles_df_display,
                key="roles_editor_config",
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Role": st.column_config.TextColumn(disabled=True), # Role name editing via form is safer
                    "Rate (€/h)": st.column_config.NumberColumn(format="%.2f €", min_value=0.0, step=0.50),
                    "Availability (%)": st.column_config.NumberColumn(format="%.1f %%", min_value=0.0, max_value=100.0, step=1.0)
                }
            )
            if not edited_roles_df.equals(roles_df_display): # Check if changes were made
                for _, row in edited_roles_df.iterrows():
                    st.session_state.roles[row["Role"]]["rate_eur_hr"] = row["Rate (€/h)"]
                    st.session_state.roles[row["Role"]]["availability_percent"] = row["Availability (%)"]
                st.success("Roles updated from table.")
                st.rerun()
        else:
            st.info("No roles defined yet. Add roles using the form on the left.")
    st.divider()

    with st.expander("➕ Manage Phases (Project Stages)", expanded=False): # Renamed from Macro Tasks
        st.subheader("Define and Edit Project Phases")
        phase_form_col, phase_table_col = st.columns(2)
        with phase_form_col:
            with st.form("phases_form", clear_on_submit=True): # Renamed key
                st.write("**Add New Phase**")
                new_phase_name = st.text_input("New Phase Name")
                new_phase_color = st.color_picker("Associated Color", "#ADD8E6", key="new_phase_color_picker")
                if st.form_submit_button("✨ Add New Phase"):
                    if not new_phase_name.strip():
                        st.error("Phase name cannot be empty.")
                    elif new_phase_name.strip() in st.session_state.phases:
                        st.warning(f"Phase '{new_phase_name.strip()}' already exists.")
                    else:
                        st.session_state.phases[new_phase_name.strip()] = new_phase_color
                        st.success(f"Phase '{new_phase_name.strip()}' added.")
                        st.rerun()

            st.write("**Delete Phase**")
            phase_to_delete = st.selectbox("Select Phase to Delete", [""] + sorted(list(st.session_state.phases.keys())), key="delete_phase_select")
            if st.button("❌ Delete Selected Phase", key="delete_phase_btn") and phase_to_delete:
                is_phase_used = any(task.get('phase') == phase_to_delete for task in st.session_state.tasks)
                if is_phase_used:
                    st.warning(f"Phase '{phase_to_delete}' is currently used in tasks and cannot be deleted. Please change the phase for those tasks first.")
                else:
                    del st.session_state.phases[phase_to_delete]
                    if st.session_state.last_phase == phase_to_delete: # Clear last_phase if it was the one deleted
                        st.session_state.last_phase = None
                    st.success(f"Phase '{phase_to_delete}' deleted.")
                    st.rerun()
        with phase_table_col:
            st.write("**Current Phases**")
            if st.session_state.phases:
                phases_df_display = pd.DataFrame([{"Phase": name, "Color": color} for name, color in st.session_state.phases.items()])
                edited_phases_df = st.data_editor(
                    phases_df_display,
                    key="phases_editor",
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Phase": st.column_config.TextColumn(disabled=True), # Phase name editing via form
                        "Color": st.column_config.TextColumn(help="Hex color code (e.g., #ADD8E6)")
                    }
                )
                if not edited_phases_df.equals(phases_df_display):
                    for _, row in edited_phases_df.iterrows():
                        st.session_state.phases[row["Phase"]] = row["Color"]
                    # Update phase colors in existing tasks if a phase color changed
                    for i, task_item in enumerate(st.session_state.tasks):
                        st.session_state.tasks[i]['phase_color'] = st.session_state.phases.get(task_item['phase'], "#CCCCCC") # Default color
                    st.success("Phase colors updated from table.")
                    st.rerun()
            else:
                st.info("No phases defined yet.")
    st.divider()

    st.subheader("🕒 Working Hours Configuration")
    hours_config_changed_flag = False # Renamed for local scope
    days_of_week_for_hours = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] # Full names for keys
    st.markdown("**Default Weekly Schedule** (Hours per day)")
    cols_days_default_hours = st.columns(len(days_of_week_for_hours))
    default_hours_config = st.session_state.config['working_hours'].get('default', {})

    for i_day_default, day_name_default in enumerate(days_of_week_for_hours):
        with cols_days_default_hours[i_day_default]:
            current_val_hours_default = default_hours_config.get(day_name_default, 0.0)
            new_val_hours_default = st.number_input(
                f"{DAY_NAMES_EN[day_name_default][:3]}", # Display short day name
                0.0, 24.0, current_val_hours_default, 0.5,
                key=f"working_hours_default_{day_name_default}",
                help=f"Working hours for {DAY_NAMES_EN[day_name_default]}"
            )
            if new_val_hours_default != current_val_hours_default:
                hours_config_changed_flag = True
                st.session_state.config['working_hours']['default'][day_name_default] = new_val_hours_default

    st.markdown("**Specific Monthly Schedules (Overrides)**")
    monthly_overrides_config = st.session_state.config['working_hours'].get('monthly_overrides', {})
    if monthly_overrides_config:
        overrides_list_display = []
        # Sort by month number for display
        sorted_month_keys = sorted(monthly_overrides_config.keys(), key=lambda m_key: int(m_key) if m_key.isdigit() else 0)

        for month_key_str in sorted_month_keys:
             month_schedule = monthly_overrides_config[month_key_str]
             month_int = int(month_key_str) if month_key_str.isdigit() else 0 # Robust conversion
             schedule_str_display = ", ".join([f"{DAY_NAMES_EN[d][:3]}: {h}h" for d,h in month_schedule.items() if h>0]) or "All 0h (Non-working)"
             overrides_list_display.append({"Month": MONTH_NAMES_EN.get(month_int, f"Month Key {month_key_str}"), "Configured Schedule": schedule_str_display})
        if overrides_list_display:
            st.table(pd.DataFrame(overrides_list_display))
        else:
            st.caption("No monthly overrides currently defined.")


    col_month_select, col_month_edit_form = st.columns([0.3, 0.7])
    with col_month_select:
        selected_month_for_override = st.selectbox(
            "Select Month to Add/Edit Override:",
            options=[None] + list(range(1, 13)), # None + 1-12
            format_func=lambda x_month: MONTH_NAMES_EN[x_month] if x_month else "Choose a month...",
            key="month_override_select"
        )

    if selected_month_for_override: # If a month is chosen
        with col_month_edit_form:
            st.write(f"**Editing Override for {MONTH_NAMES_EN[selected_month_for_override]}**")
            selected_month_key_str = str(selected_month_for_override) # Key for storage is string
            current_month_schedule_override = monthly_overrides_config.get(selected_month_key_str, {}) # Get existing or empty
            new_month_schedule_data = {}
            cols_month_days_edit = st.columns(len(days_of_week_for_hours))

            for i_day_month_edit, day_name_month_edit in enumerate(days_of_week_for_hours):
                with cols_month_days_edit[i_day_month_edit]:
                    # Default to this month's override if exists, else default schedule's value for that day
                    default_value_for_day = current_month_schedule_override.get(day_name_month_edit, default_hours_config.get(day_name_month_edit, 0.0))
                    new_month_schedule_data[day_name_month_edit] = st.number_input(
                        f"{DAY_NAMES_EN[day_name_month_edit][:3]} ({MONTH_NAMES_EN[selected_month_for_override][:3]})", # Short day and month
                        0.0, 24.0, default_value_for_day, 0.5,
                        key=f"work_hours_month_{selected_month_key_str}_{day_name_month_edit}"
                    )

            save_col, delete_col, _ = st.columns([0.3, 0.4, 0.3]) # Layout for buttons
            with save_col:
                if st.button(f"💾 Save Override for {MONTH_NAMES_EN[selected_month_for_override]}", key=f"save_month_override_{selected_month_key_str}"):
                    st.session_state.config['working_hours']['monthly_overrides'][selected_month_key_str] = new_month_schedule_data
                    st.success(f"Working hours override for {MONTH_NAMES_EN[selected_month_for_override]} saved.")
                    hours_config_changed_flag = True # Signal change
                    st.rerun() # Rerun to reflect changes
            with delete_col:
                if selected_month_key_str in monthly_overrides_config: # Show delete only if override exists
                    if st.button(f"❌ Delete Override for {MONTH_NAMES_EN[selected_month_for_override]}", key=f"delete_month_override_{selected_month_key_str}"):
                        del st.session_state.config['working_hours']['monthly_overrides'][selected_month_key_str]
                        st.success(f"Working hours override for {MONTH_NAMES_EN[selected_month_for_override]} deleted.")
                        hours_config_changed_flag = True # Signal change
                        st.rerun() # Rerun

    if hours_config_changed_flag: # If any hour config changed (default or monthly)
        st.info("Working hours configuration changed. You may need to recalculate the project plan for dates to update.")
        # No automatic rerun here, user should trigger replan if needed.
    st.divider()

    st.subheader("🔄 Recalculate Plan with Resource Leveling")
    st.warning("This action re-schedules all tasks based on their ID priority, dependencies, and daily resource capacity (effort in Person-Hours). Existing task dates will be overwritten by the leveling algorithm.")
    if st.button("🔁 Replan with Resource Leveling", key="replan_leveled_effort_button_main"):
        if not st.session_state.tasks:
            st.info("No tasks in the project to replan.")
        elif not st.session_state.roles:
            st.error("No roles defined. Roles are required for resource leveling.")
        else:
            # Create a deep copy of tasks for replanning to avoid modifying session state directly during calculation
            tasks_copy_for_replan = [task.copy() for task in st.session_state.tasks]
            logging.info("--- Starting Effort-Based Resource Leveling Replan (User Triggered) ---")
            replan_with_resource_leveling(tasks_copy_for_replan, st.session_state.roles, st.session_state.config)
            # replan_with_resource_leveling modifies st.session_state.tasks internally if successful
            logging.info("--- Effort-Based Resource Leveling Replan Finished (User Triggered) ---")
            st.rerun() # Rerun to show updated dates and statuses
    st.divider()

    st.subheader("📈 Profit Margin")
    current_profit_margin = st.session_state.config.get('profit_margin_percent', 0.0)
    new_profit_margin = st.number_input(
        "Profit Margin (%)", 0.0, value=current_profit_margin, format="%.2f",
        key="profit_margin_input_main", help="Set the desired profit margin for cost calculations."
    )
    if new_profit_margin != current_profit_margin:
        st.session_state.config['profit_margin_percent'] = new_profit_margin
        st.success("Profit margin updated. Cost estimations will reflect this change.")
        st.rerun() # Rerun to update cost displays
    st.divider()

    st.subheader("💾 Project Data Management")
    col_export, col_import = st.columns(2)
    with col_export:
        st.write("**Export Project Plan**")
        export_data_payload = {
            "roles": st.session_state.roles,
            "tasks": [], # Will be populated
            "next_task_id": st.session_state.next_task_id,
            "config": {}, # Will be populated
            "phases": st.session_state.phases # Export phases
        }
        # Prepare tasks for export (convert dates to ISO format, ensure assignments/deps are JSON strings)
        for task_to_export in st.session_state.tasks:
            task_copy_export = task_to_export.copy()
            if isinstance(task_copy_export.get('start_date'), datetime.date):
                task_copy_export['start_date'] = task_copy_export['start_date'].isoformat()
            if isinstance(task_copy_export.get('end_date'), datetime.date):
                task_copy_export['end_date'] = task_copy_export['end_date'].isoformat()

            # Ensure assignments and dependencies are consistently stored/exported
            task_copy_export['assignments'] = parse_assignments(task_copy_export.get('assignments', [])) # Store as list of dicts
            task_copy_export['dependencies'] = json.dumps(parse_dependencies(task_copy_export.get('dependencies', '[]'))) # Store as JSON string of list of int

            export_data_payload["tasks"].append(task_copy_export)

        # Prepare config for export (convert date to ISO format)
        config_copy_export = json.loads(json.dumps(st.session_state.config, default=str)) # Deep copy and convert non-serializable
        if 'project_start_date' in config_copy_export and isinstance(st.session_state.config['project_start_date'], datetime.date):
            config_copy_export['project_start_date'] = st.session_state.config['project_start_date'].isoformat()
        # Ensure monthly override keys are strings for JSON export consistency
        if 'working_hours' in config_copy_export and 'monthly_overrides' in config_copy_export['working_hours']:
            config_copy_export['working_hours']['monthly_overrides'] = {
                str(k): v for k,v in config_copy_export['working_hours']['monthly_overrides'].items()
            }
        export_data_payload["config"] = config_copy_export

        try:
            json_export_string = json.dumps(export_data_payload, indent=2)
            st.download_button(
                label="📥 Download Plan (JSON)",
                data=json_export_string,
                file_name=f"project_plan_{datetime.date.today()}.json",
                mime="application/json",
                key="download_project_json_button"
            )
        except Exception as e_export:
            st.error(f"Error during JSON export preparation: {e_export}")
            logging.error(f"JSON Export Error: {e_export}", exc_info=True)

    with col_import:
        st.write("**Import Project Plan**")
        uploaded_file = st.file_uploader("Upload Project Plan JSON File", type=["json"], key="upload_project_json")
        if uploaded_file and st.button("✅ Confirm Import from JSON", key="confirm_import_json_button"):
            try:
                imported_data = json.load(uploaded_file)
                if all(k in imported_data for k in ["roles", "tasks", "next_task_id", "config"]): # Basic structure check
                    imported_tasks_processed = []
                    for task_data_imported in imported_data["tasks"]:
                        # Convert dates back from ISO format
                        if isinstance(task_data_imported.get('start_date'), str):
                            task_data_imported['start_date'] = datetime.date.fromisoformat(task_data_imported['start_date'])
                        if isinstance(task_data_imported.get('end_date'), str):
                            task_data_imported['end_date'] = datetime.date.fromisoformat(task_data_imported['end_date'])

                        task_data_imported['effort_ph'] = float(task_data_imported.get('effort_ph', 0.0))
                        # Handle potential old 'duration' key, prefer 'duration_calc_days'
                        task_data_imported['duration_calc_days'] = float(task_data_imported.get('duration_calc_days', task_data_imported.get('duration', 0.0)))
                        task_data_imported.pop('duration', None) # Remove old key if present

                        # Ensure assignments and dependencies are correctly parsed/formatted
                        task_data_imported['assignments'] = parse_assignments(task_data_imported.get('assignments', []))
                        task_data_imported['dependencies'] = json.dumps(parse_dependencies(task_data_imported.get('dependencies', '[]')))

                        task_data_imported.setdefault('phase', 'No Phase') # Use 'phase'
                        task_data_imported.setdefault('subtask', 'No Subtask')
                        task_data_imported.setdefault('name', f"{task_data_imported.get('phase','No Phase')} - {task_data_imported.get('subtask','No Subtask')}")
                        imported_tasks_processed.append(task_data_imported)

                    imported_config = imported_data["config"]
                    if isinstance(imported_config.get('project_start_date'), str):
                        imported_config['project_start_date'] = datetime.date.fromisoformat(imported_config['project_start_date'])

                    # Ensure working_hours structure and string keys for monthly_overrides
                    if 'working_hours' not in imported_config or not isinstance(imported_config['working_hours'], dict):
                        imported_config['working_hours'] = {'default': {"Monday":9.0,"Tuesday":9.0,"Wednesday":9.0,"Thursday":9.0,"Friday":7.0,"Saturday":0.0,"Sunday":0.0},'monthly_overrides':{}}
                    imported_config['working_hours'].setdefault('default', {"Monday":9.0,"Tuesday":9.0,"Wednesday":9.0,"Thursday":9.0,"Friday":7.0,"Saturday":0.0,"Sunday":0.0})
                    imported_config['working_hours'].setdefault('monthly_overrides', {})
                    imported_config['working_hours']['monthly_overrides'] = {
                        str(k): v for k, v in imported_config['working_hours']['monthly_overrides'].items()
                    }

                    # Update session state
                    st.session_state.roles = imported_data["roles"]
                    st.session_state.tasks = imported_tasks_processed
                    st.session_state.next_task_id = imported_data["next_task_id"]
                    st.session_state.config = imported_config
                    st.session_state.phases = imported_data.get("phases", {}) # Import phases, default to empty if not present
                    st.session_state.leveled_resource_schedule = {} # Reset schedule after import

                    # Ensure all necessary defaults after import (some might be redundant if import structure is good)
                    st.session_state.config.setdefault('project_start_date', datetime.date.today())
                    st.session_state.config.setdefault('exclude_weekends', True)
                    st.session_state.config.setdefault('profit_margin_percent', 0.0)

                    # Post-import processing: update phase colors and recalculate durations if needed
                    for i, task_final_import in enumerate(st.session_state.tasks):
                        st.session_state.tasks[i]['phase_color'] = st.session_state.phases.get(task_final_import.get('phase'), "#CCCCCC")
                        # If effort exists but calculated duration is missing/zero, recalculate it
                        if task_final_import.get('effort_ph',0) > 0 and task_final_import.get('duration_calc_days',0) <=0:
                            st.session_state.tasks[i]['duration_calc_days'] = calculate_estimated_duration_from_effort(
                                task_final_import['effort_ph'],
                                task_final_import['assignments'], # Should be parsed list of dicts
                                st.session_state.roles,
                                st.session_state.config['working_hours'],
                                st.session_state.config['exclude_weekends']
                            )
                    st.success("Project plan imported successfully!")
                    st.rerun()
                else:
                    st.error("Invalid JSON file structure. Required keys: roles, tasks, next_task_id, config.")
            except Exception as e_import:
                st.error(f"Error during JSON import: {e_import}")
                logging.error(f"JSON Import Error: {e_import}", exc_info=True)

# --- Common Data Preparation (Calculations for Display in other tabs) ---
# This section prepares tasks_df which is used by multiple tabs for display.
# It should use the most up-to-date configurations from st.session_state.config.

tasks_list_for_df_prep = st.session_state.tasks
current_project_config_prep = st.session_state.config
current_working_hours_prep = current_project_config_prep['working_hours']
current_exclude_weekends_prep = current_project_config_prep['exclude_weekends']
current_roles_prep = st.session_state.roles
current_phases_prep = st.session_state.phases


if tasks_list_for_df_prep:
     # Create a fresh copy for DataFrame to avoid modifying session state tasks directly here
     tasks_df_list_copy_prep = [t.copy() for t in tasks_list_for_df_prep]
     tasks_df_for_display = pd.DataFrame(tasks_df_list_copy_prep)

     tasks_df_for_display['effort_ph'] = pd.to_numeric(tasks_df_for_display['effort_ph'], errors='coerce').fillna(0.0)

     # Ensure duration_calc_days is calculated if missing or zero, using current configurations
     for index, row in tasks_df_for_display.iterrows():
        if row['effort_ph'] > 0 and (pd.isna(row['duration_calc_days']) or row['duration_calc_days'] <=0):
            tasks_df_for_display.loc[index, 'duration_calc_days'] = calculate_estimated_duration_from_effort(
                row['effort_ph'],
                parse_assignments(row['assignments']), # Ensure assignments are parsed
                current_roles_prep,
                current_working_hours_prep,
                current_exclude_weekends_prep
            )
        elif row['effort_ph'] <= 0 : # For tasks with no effort (e.g., milestones)
             tasks_df_for_display.loc[index, 'duration_calc_days'] = 0.5 # Assign a minimal duration

     tasks_df_for_display['duration_calc_days'] = pd.to_numeric(tasks_df_for_display['duration_calc_days'], errors='coerce').fillna(0.5) # Fallback if still NaN

     tasks_df_for_display['start_date'] = pd.to_datetime(tasks_df_for_display['start_date'], errors='coerce').dt.date

     # Recalculate end_date if missing or if it needs to be based on effort (e.g., after leveling)
     # The 'end_date' in session_state.tasks should be the one from leveling.
     # This section primarily ensures it's a date object for display.
     if 'end_date' not in tasks_df_for_display.columns or tasks_df_for_display['end_date'].isnull().any():
         tasks_df_for_display['end_date'] = tasks_df_for_display.apply(
             lambda row: calculate_end_date_from_effort(
                             row['start_date'], row['effort_ph'],
                             parse_assignments(row['assignments']), current_roles_prep,
                             current_working_hours_prep, current_exclude_weekends_prep)
                         if pd.notna(row['start_date']) and row['effort_ph'] > 0 and isinstance(row['start_date'], datetime.date)
                         else (row['start_date'] if pd.notna(row['start_date']) else pd.NaT), # For milestones, end = start
             axis=1
         )
     tasks_df_for_display['end_date'] = pd.to_datetime(tasks_df_for_display['end_date'], errors='coerce').dt.date

     tasks_df_for_display['assignments'] = tasks_df_for_display['assignments'].apply(parse_assignments) # Ensure it's always a list of dicts
     tasks_df_for_display['phase'] = tasks_df_for_display['phase'].fillna('No Phase').astype(str)
     tasks_df_for_display['subtask'] = tasks_df_for_display['subtask'].fillna('No Subtask').astype(str)
     tasks_df_for_display['name'] = tasks_df_for_display['phase'] + " - " + tasks_df_for_display['subtask']
     tasks_df_for_display['phase_color'] = tasks_df_for_display['phase'].apply(lambda p: current_phases_prep.get(p, "#CCCCCC"))
     tasks_df_for_display['cost'] = tasks_df_for_display.apply(
         lambda row: calculate_task_cost_by_effort(row['effort_ph'], row['assignments'], current_roles_prep)
                     if row['effort_ph'] > 0 else 0.0,
         axis=1
     )
     # Create a map of task end dates for dependency calculations if needed by other parts (e.g., new task form)
     valid_end_dates_for_map_prep = tasks_df_for_display.dropna(subset=['id', 'end_date'])
     task_end_dates_map_for_new_task_form = pd.Series(
         valid_end_dates_for_map_prep.end_date.values,
         index=valid_end_dates_for_map_prep.id
     ).to_dict()
else: # No tasks in the project
     tasks_df_for_display = pd.DataFrame(columns=[
         'id', 'phase', 'subtask', 'phase_color', 'name', 'start_date',
         'effort_ph', 'duration_calc_days', 'assignments', 'dependencies',
         'status', 'notes', 'end_date', 'cost'
     ])
     task_end_dates_map_for_new_task_form = {}


# --- Tasks Tab (Editing and Creation) ---
with tab_tasks:
    st.header("📝 Detailed Task Management")

    with st.expander("➕ Add New Task", expanded=False):
        with st.form("new_task_form_effort_based", clear_on_submit=True): # Unique key
            st.write("Define the details of the new task:")

            # Phase selection
            if st.session_state.phases:
                phase_options_new_task = [""] + sorted(list(st.session_state.phases.keys()))
                default_phase_index_new_task = phase_options_new_task.index(st.session_state.last_phase) \
                                               if st.session_state.last_phase in phase_options_new_task else 0
                selected_phase_new_task = st.selectbox(
                    "Phase (*)", options=phase_options_new_task, index=default_phase_index_new_task,
                    help="Select the project phase for this task.", key="new_task_phase_select"
                )
                phase_color_for_new_task_form = st.session_state.phases.get(selected_phase_new_task, "#CCCCCC")
            else: # No phases defined yet
                selected_phase_new_task = st.text_input(
                    "Phase (*)", help="No phases defined. Enter a name for this task's phase.",
                    key="new_task_phase_text_input"
                )
                # If user enters a new phase name here, they might expect a color too.
                # For simplicity, we'll use a default or they can define it in Settings.
                phase_color_for_new_task_form = st.color_picker(
                    "Color for this new Phase (if not yet defined)", value="#ADD8E6", key="new_task_phase_color_picker_form"
                )


            subtask_name_new_task = st.text_input("Subtask Name (*)", help="Specific name for this task.", key="new_task_subtask_name")
            task_name_preview_new_task = f"{selected_phase_new_task.strip()} - {subtask_name_new_task.strip()}" \
                                         if selected_phase_new_task and selected_phase_new_task.strip() and \
                                            subtask_name_new_task and subtask_name_new_task.strip() else ""
            if task_name_preview_new_task:
                st.caption(f"Full task name will be: {task_name_preview_new_task}")

            task_effort_ph_input_new = st.number_input(
                "Effort (Person-Hours) (*)", min_value=0.1, step=0.5, value=8.0, format="%.1f",
                key="new_task_effort_ph_input", help="Total estimated person-hours required to complete this task."
            )

            # Start Date: Manual input, but dependencies can override it.
            default_new_task_start_date = st.session_state.config.get('project_start_date', datetime.date.today())
            task_start_date_manual_input = st.date_input(
                "Desired Start Date (can be overridden by dependencies)",
                value=default_new_task_start_date, key="new_task_start_date_manual"
            )

            # Dependencies
            dep_options_for_new_task = {
                task_dep['id']: f"{task_dep.get('name', f'ID {task_dep['id']}')} (ID: {task_dep['id']})"
                for task_dep in sorted(st.session_state.tasks, key=lambda x_dep: x_dep.get('start_date', datetime.date.min))
            }
            task_dependencies_ids_selected = st.multiselect(
                "Dependencies (Prerequisite Tasks)",
                options=list(dep_options_for_new_task.keys()),
                format_func=lambda x_dep_id: dep_options_for_new_task.get(x_dep_id, f"ID {x_dep_id}?"),
                help="Select tasks that must be completed before this task can start.",
                key="new_task_dependencies_select"
            )

            task_status_new = st.selectbox("Initial Status", ["Pending", "In Progress", "Completed", "Blocked"], key="new_task_status_select")
            task_notes_new = st.text_area("Additional Notes", key="new_task_notes_area")

            st.markdown("--- \n ### Role Assignments for this Task")
            st.caption("Specify the percentage of each role's **available time** that should be allocated **specifically to this task** while it's active.")
            assignment_data_for_new_task = {}
            if st.session_state.roles:
                cols_assign_new_task = st.columns(len(st.session_state.roles))
                for i_assign_new, role_assign_new in enumerate(sorted(st.session_state.roles.keys())):
                    with cols_assign_new_task[i_assign_new]:
                        assignment_data_for_new_task[role_assign_new] = st.number_input(
                            f"{role_assign_new} (% Alloc.)", 0, 100, 0, 5, # Min, Max, Default, Step
                            key=f"new_task_allocation_{role_assign_new}",
                            help=f"Percentage of {role_assign_new}'s available project time to dedicate to THIS task."
                        )
            else:
                st.warning("No roles defined in Settings. Roles are needed for assignments and cost/effort calculations.")

            if st.form_submit_button("✅ Add Task to Plan"):
                final_selected_phase_name = selected_phase_new_task.strip() if selected_phase_new_task else ""
                final_subtask_name = subtask_name_new_task.strip() if subtask_name_new_task else ""

                if not final_selected_phase_name or not final_subtask_name or task_effort_ph_input_new <= 0:
                    st.error("Please complete all required fields (*): Phase, Subtask Name, and Effort (must be > 0).")
                else:
                    # If a new phase was entered in text input and not yet in session_state.phases, add it.
                    if final_selected_phase_name not in st.session_state.phases and not st.session_state.phases: # Only if no phases exist at all
                         st.session_state.phases[final_selected_phase_name] = phase_color_for_new_task_form
                         st.info(f"New phase '{final_selected_phase_name}' was added with the chosen color. You can manage phases in Settings.")


                    # Calculate start date considering dependencies
                    calculated_start_date_for_new_task = task_start_date_manual_input
                    if task_dependencies_ids_selected:
                        # task_end_dates_map_for_new_task_form is prepared in the common data prep section
                        computed_start_from_deps = calculate_dependent_start_date_for_scheduling(
                            json.dumps(task_dependencies_ids_selected),
                            task_end_dates_map_for_new_task_form, # Use the map from common prep
                            default_new_task_start_date, # Project default start
                            st.session_state.config['working_hours'],
                            st.session_state.config['exclude_weekends']
                        )
                        if computed_start_from_deps:
                            calculated_start_date_for_new_task = computed_start_from_deps
                        else: # Should not happen if map is correct, but as a fallback:
                            calculated_start_date_for_new_task = get_next_working_day(task_start_date_manual_input, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])
                    else: # No dependencies, ensure it's a working day
                        calculated_start_date_for_new_task = get_next_working_day(task_start_date_manual_input, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])

                    new_task_id_val = st.session_state.next_task_id
                    st.session_state.next_task_id += 1
                    st.session_state.last_phase = final_selected_phase_name # Remember last used phase

                    new_assignments_parsed = [{'role': r_name, 'allocation': alloc_val}
                                           for r_name, alloc_val in assignment_data_for_new_task.items() if alloc_val > 0]

                    # Estimate duration based on effort for initial display
                    duration_calc_days_for_new_task = calculate_estimated_duration_from_effort(
                        task_effort_ph_input_new, new_assignments_parsed,
                        st.session_state.roles, st.session_state.config['working_hours'],
                        st.session_state.config['exclude_weekends']
                    )
                    # The actual end_date will be determined by resource leveling.
                    # For now, we can estimate it for non-leveled display or if leveling isn't run.
                    # However, the 'replan' button is the primary way to get accurate end dates.

                    final_phase_color_for_new_task = st.session_state.phases.get(final_selected_phase_name, phase_color_for_new_task_form)


                    new_task_entry_dict = {
                        'id': new_task_id_val,
                        'phase': final_selected_phase_name,
                        'subtask': final_subtask_name,
                        'phase_color': final_phase_color_for_new_task,
                        'name': f"{final_selected_phase_name} - {final_subtask_name}",
                        'start_date': calculated_start_date_for_new_task,
                        'effort_ph': task_effort_ph_input_new,
                        'duration_calc_days': duration_calc_days_for_new_task, # Store the effort-based duration estimate
                        'assignments': new_assignments_parsed, # Store as list of dicts
                        'dependencies': json.dumps(task_dependencies_ids_selected), # Store as JSON string of list of ints
                        'status': task_status_new,
                        'notes': task_notes_new,
                        'parent_id': None, # Assuming top-level tasks for now
                        'end_date': None # End date will be set by leveling or a simple calculation if not leveled
                    }
                    # Add a simple end_date calculation for immediate display if not leveling
                    if new_task_entry_dict['start_date'] and new_task_entry_dict['effort_ph'] > 0:
                         new_task_entry_dict['end_date'] = calculate_end_date_from_effort(
                             new_task_entry_dict['start_date'], new_task_entry_dict['effort_ph'],
                             new_task_entry_dict['assignments'], st.session_state.roles,
                             st.session_state.config['working_hours'], st.session_state.config['exclude_weekends']
                         )
                    elif new_task_entry_dict['start_date']: # Milestone like
                         new_task_entry_dict['end_date'] = new_task_entry_dict['start_date']


                    st.session_state.tasks.append(new_task_entry_dict)
                    st.success(f"Task '{new_task_entry_dict['name']}' added. Estimated duration: {duration_calc_days_for_new_task} days. Replan for accurate schedule.")
                    st.rerun()
    st.divider()

    st.subheader("📋 Task List (Editable)")
    st.caption("You can edit Phase, Subtask, Start Date, Effort (PH), Dependencies (as JSON list of IDs), Status, and Notes directly in the table. Estimated Duration and Calculated End Date/Cost are re-evaluated. For a resource-aware schedule, use the 'Replan with Resource Leveling' button in Settings.")

    if not tasks_df_for_display.empty:
        tasks_df_editor_display = tasks_df_for_display.copy() # Use the prepared DataFrame

        # Add display-friendly columns for assignments and dependencies
        tasks_df_editor_display['assignments_display'] = tasks_df_editor_display['assignments'].apply(format_assignments_display)
        tasks_df_editor_display['dependencies_display'] = tasks_df_editor_display['dependencies'].apply(lambda d_str: format_dependencies_display(d_str, st.session_state.tasks)) # Pass full task list for name lookup
        tasks_df_editor_display['cost_display'] = tasks_df_editor_display['cost'].apply(lambda c_val: f"€ {c_val:,.2f}")
        tasks_df_editor_display['end_date_display'] = tasks_df_editor_display['end_date'].apply(lambda d_val: d_val.strftime('%Y-%m-%d') if pd.notna(d_val) and isinstance(d_val, datetime.date) else 'N/A (Replan)')


        column_config_for_task_editor = {
            "id": st.column_config.NumberColumn("ID", disabled=True, help="Unique Task Identifier"),
            "phase": st.column_config.TextColumn("Phase", required=True, help="Project phase or macro-task name."),
            "subtask": st.column_config.TextColumn("Subtask", required=True, help="Specific subtask name."),
            "phase_color": None, # Not directly editable here, managed by phase definition
            "name": st.column_config.TextColumn("Full Name", disabled=True, width="large", help="Auto-generated from Phase and Subtask."),
            "start_date": st.column_config.DateColumn("Start Date", required=True, format="YYYY-MM-DD", help="Task start date. Can be affected by dependencies and replanning."),
            "effort_ph": st.column_config.NumberColumn("Effort (PH)", required=True, min_value=0.1, format="%.1f PH", help="Estimated person-hours for the task."),
            "duration_calc_days": st.column_config.NumberColumn("Est. Dur. (Days)", disabled=True, format="%.1f d", help="Estimated duration based on effort and assignments. Recalculated."),
            "dependencies": st.column_config.TextColumn("Deps (IDs JSON)", help="JSON list of prerequisite task IDs, e.g., [1, 2]."),
            "dependencies_display": st.column_config.TextColumn("Deps (Names)", disabled=True, help="Readable list of dependencies."),
            "status": st.column_config.SelectboxColumn("Status", options=["Pending", "In Progress", "Completed", "Blocked", "Pending (Leveling Error)", "Pending (Dependency Error)", "Pending (Leveled)"], help="Current status of the task."),
            "notes": st.column_config.TextColumn("Notes", width="medium", help="Additional notes for the task."),
            "end_date": None, # Not directly editable, calculated
            "end_date_display": st.column_config.TextColumn("Calc. End Date", disabled=True, help="Calculated end date. Accurate after replanning."),
            "cost": None, # Not directly editable, calculated
            "cost_display": st.column_config.TextColumn("Calc. Cost (€)", disabled=True, help="Calculated cost based on effort and role rates."),
            "assignments": None, # Not directly editable here, use dedicated section below table
            "assignments_display": st.column_config.TextColumn("Assignments", disabled=True, help="Role assignments. Edit below.")
        }

        # Define which columns from tasks_df_editor_display to show in the data_editor
        cols_to_display_in_editor = ['id', 'phase', 'subtask', 'start_date', 'effort_ph', 'duration_calc_days', 'dependencies_display', 'status', 'notes', 'end_date_display', 'cost_display', 'assignments_display', 'dependencies'] # 'dependencies' is for editing

        edited_df_from_table = st.data_editor(
            tasks_df_editor_display[cols_to_display_in_editor],
            column_config=column_config_for_task_editor,
            key="task_editor_main_effort",
            num_rows="dynamic", # Allows adding/deleting rows
            use_container_width=True,
            hide_index=True
        )

        # Process changes from the data_editor
        if edited_df_from_table is not None: # Check if editor returned data
            try:
                updated_tasks_list_from_editor = []
                processed_ids_from_editor = set()
                needs_rerun_after_edit = False

                original_tasks_map_for_edit = {task_orig['id']: task_orig for task_orig in st.session_state.tasks}

                for i_row_edit, edited_row in edited_df_from_table.iterrows():
                    task_id_edited = edited_row.get('id')
                    is_new_row_from_editor = pd.isna(task_id_edited) or task_id_edited <= 0 # Check if it's a new row added via editor UI

                    if is_new_row_from_editor:
                        task_id_edited = st.session_state.next_task_id
                        st.session_state.next_task_id += 1
                        original_task_data_for_row = {} # It's a new task
                        # For new rows, assignments and dependencies need to be initialized or handled
                        current_assignments_for_row = [] # New tasks start with no assignments via table
                        current_dependencies_str_for_row = '[]' # New tasks start with no dependencies via table
                        current_phase_color_for_row = st.session_state.phases.get(str(edited_row.get("phase","")).strip() or "No Phase", "#CCCCCC")
                        needs_rerun_after_edit = True # New row always means change
                    else:
                        task_id_edited = int(task_id_edited)
                        original_task_data_for_row = original_tasks_map_for_edit.get(task_id_edited, {})
                        # For existing tasks, retain their current assignments unless changed elsewhere
                        current_assignments_for_row = original_task_data_for_row.get('assignments', [])
                        current_dependencies_str_for_row = original_task_data_for_row.get('dependencies', '[]')
                        current_phase_color_for_row = original_task_data_for_row.get('phase_color', '#CCCCCC')


                    processed_ids_from_editor.add(task_id_edited)

                    # Handle dependencies string from editor
                    raw_deps_from_editor = edited_row.get('dependencies') # This is the editable column
                    dependencies_changed_in_editor = False
                    if pd.notna(raw_deps_from_editor) and raw_deps_from_editor != current_dependencies_str_for_row:
                        try:
                            parsed_deps_list_editor = parse_dependencies(raw_deps_from_editor)
                            final_deps_str_for_row = json.dumps(parsed_deps_list_editor)
                        except Exception: # Fallback if parsing fails
                            final_deps_str_for_row = current_dependencies_str_for_row # Keep original
                        if final_deps_str_for_row != current_dependencies_str_for_row:
                            dependencies_changed_in_editor = True
                            needs_rerun_after_edit = True
                    else:
                        final_deps_str_for_row = current_dependencies_str_for_row

                    # Get values from edited row, falling back to original if necessary
                    edited_phase_name = str(edited_row.get("phase", "")).strip() or "No Phase"
                    edited_subtask_name = str(edited_row.get("subtask", "")).strip() or "No Subtask"
                    edited_full_name = f"{edited_phase_name} - {edited_subtask_name}"
                    edited_phase_color = st.session_state.phases.get(edited_phase_name, current_phase_color_for_row)

                    edited_start_date = pd.to_datetime(edited_row.get('start_date'), errors='coerce').date() \
                                        if pd.notna(edited_row.get('start_date')) \
                                        else (original_task_data_for_row.get('start_date') or datetime.date.today())

                    edited_effort_ph = max(0.1, float(edited_row['effort_ph'])) \
                                       if pd.notna(edited_row.get('effort_ph')) \
                                       else (original_task_data_for_row.get('effort_ph') or 0.1)

                    edited_status = str(edited_row.get('status', original_task_data_for_row.get('status', 'Pending')))
                    edited_notes = str(edited_row.get('notes', original_task_data_for_row.get('notes', '')))

                    # Recalculate duration based on potentially changed effort (assignments are not changed here)
                    recalculated_duration_days = calculate_estimated_duration_from_effort(
                        edited_effort_ph, current_assignments_for_row, # Use existing assignments for this task
                        st.session_state.roles, st.session_state.config['working_hours'],
                        st.session_state.config['exclude_weekends']
                    )
                    # Recalculate end_date based on potentially changed start_date or effort
                    recalculated_end_date = calculate_end_date_from_effort(
                        edited_start_date, edited_effort_ph, current_assignments_for_row,
                        st.session_state.roles, st.session_state.config['working_hours'],
                        st.session_state.config['exclude_weekends']
                    ) if edited_start_date and edited_effort_ph > 0 else (edited_start_date if edited_start_date else None)


                    task_data_entry_from_editor = {
                        'id': task_id_edited,
                        'phase': edited_phase_name,
                        'subtask': edited_subtask_name,
                        'phase_color': edited_phase_color,
                        'name': edited_full_name,
                        'start_date': edited_start_date,
                        'effort_ph': edited_effort_ph,
                        'duration_calc_days': recalculated_duration_days,
                        'assignments': current_assignments_for_row, # Assignments are not edited in this table
                        'dependencies': final_deps_str_for_row,
                        'status': edited_status,
                        'notes': edited_notes,
                        'parent_id': original_task_data_for_row.get('parent_id'), # Preserve parent_id
                        'end_date': recalculated_end_date # Store recalculated end_date
                    }
                    updated_tasks_list_from_editor.append(task_data_entry_from_editor)

                    # Detect if any significant field changed for existing tasks
                    if not is_new_row_from_editor:
                        original_compare_fields = {
                            'phase': original_task_data_for_row.get('phase'),
                            'subtask': original_task_data_for_row.get('subtask'),
                            'start_date': original_task_data_for_row.get('start_date'),
                            'effort_ph': original_task_data_for_row.get('effort_ph'),
                            'status': original_task_data_for_row.get('status'),
                            'notes': original_task_data_for_row.get('notes'),
                        }
                        current_compare_fields = {
                            'phase': edited_phase_name,
                            'subtask': edited_subtask_name,
                            'start_date': edited_start_date,
                            'effort_ph': edited_effort_ph,
                            'status': edited_status,
                            'notes': edited_notes,
                        }
                        if original_compare_fields != current_compare_fields or dependencies_changed_in_editor:
                            needs_rerun_after_edit = True

                # Handle deleted rows
                original_ids_in_session_state = set(original_tasks_map_for_edit.keys())
                deleted_ids_by_editor = original_ids_in_session_state - processed_ids_from_editor
                final_task_list_after_editor = updated_tasks_list_from_editor # Start with updated/new

                if deleted_ids_by_editor:
                    needs_rerun_after_edit = True
                    # Filter out tasks that were deleted
                    final_task_list_after_editor = [t for t in updated_tasks_list_from_editor if t['id'] not in deleted_ids_by_editor]
                    deleted_task_names = [original_tasks_map_for_edit.get(del_id,{}).get('name',f'ID {del_id}') for del_id in deleted_ids_by_editor]
                    st.success(f"Tasks deleted via table: {', '.join(deleted_task_names)}.")

                    # Update dependencies in remaining tasks if any deleted tasks were dependencies
                    dependency_updates_log = []
                    for task_in_final_list in final_task_list_after_editor:
                        current_deps_of_task = parse_dependencies(task_in_final_list.get('dependencies','[]'))
                        deps_to_remove_from_task = set(current_deps_of_task) & deleted_ids_by_editor
                        if deps_to_remove_from_task:
                            task_in_final_list['dependencies'] = json.dumps([d for d in current_deps_of_task if d not in deleted_ids_by_editor])
                            dependency_updates_log.append(f"Dependencies updated for '{task_in_final_list['name']}': removed {deps_to_remove_from_task}.")
                    if dependency_updates_log:
                        st.info("Some dependencies were automatically updated due to task deletions:\n- " + "\n- ".join(dependency_updates_log))

                # Only update session state and rerun if there were actual changes
                if needs_rerun_after_edit:
                    # Check if the content actually changed to avoid unnecessary reruns if only formatting was touched
                    # A more robust check would be a deep comparison, but string comparison of JSON is often sufficient
                    if json.dumps(st.session_state.tasks, sort_keys=True, default=str) != json.dumps(final_task_list_after_editor, sort_keys=True, default=str):
                        st.session_state.tasks = final_task_list_after_editor
                        st.success("Task list changes saved from table.")
                        st.rerun()
            except Exception as e_editor_processing:
                st.error(f"Error processing changes from task table: {e_editor_processing}")
                logging.error(f"Task table editor error: {e_editor_processing}", exc_info=True)
        # else: st.info("No tasks currently in the editor table.") # This case means editor is empty
    else:
        st.info("No tasks in the project plan yet. Add tasks using the 'Add New Task' form above.")
    st.divider()

    st.subheader("💼 Edit Role Assignments per Task")
    if not st.session_state.tasks:
        st.info("Create tasks first before assigning roles.")
    elif not st.session_state.roles:
        st.warning("No roles defined in Settings. Roles are needed for assignments.")
    else:
        # Prepare options for task selection dropdown
        task_options_for_assignment_edit = {
            task_assign_item['id']: f"{task_assign_item.get('name', 'Unnamed Task')} (Effort: {task_assign_item.get('effort_ph',0)} PH)"
            for task_assign_item in sorted(st.session_state.tasks, key=lambda x_assign: x_assign.get('start_date', datetime.date.min))
        }
        selected_task_id_for_assignment = st.selectbox(
            "Select Task to Edit Assignments:",
            options=[None] + list(task_options_for_assignment_edit.keys()), # Allow None to be selected
            format_func=lambda x_assign_id: task_options_for_assignment_edit.get(x_assign_id, "Choose a task..."),
            key="assignment_task_selector"
        )

        if selected_task_id_for_assignment:
            task_to_edit_assignments = get_task_by_id(selected_task_id_for_assignment, st.session_state.tasks)
            if task_to_edit_assignments:
                st.write(f"**Editing Assignments for Task:** {task_to_edit_assignments['name']}")
                current_assignments_for_edit_form = parse_assignments(task_to_edit_assignments.get('assignments',[]))
                current_allocations_map_edit_form = {assign_item['role']: assign_item['allocation'] for assign_item in current_assignments_for_edit_form if isinstance(assign_item,dict)}

                new_assignment_data_from_form = {}
                # Use a form for this section to group inputs and have a single save button
                with st.form(key=f"edit_assignments_form_{selected_task_id_for_assignment}"):
                    cols_assignment_edit_form = st.columns(len(st.session_state.roles))
                    for i_role_assign_form, role_name_assign_form in enumerate(sorted(st.session_state.roles.keys())):
                        with cols_assignment_edit_form[i_role_assign_form]:
                            default_allocation_for_form = current_allocations_map_edit_form.get(role_name_assign_form, 0.0)
                            allocation_input_form = st.number_input(
                                f"{role_name_assign_form} (% Alloc.)", 0, 100, int(default_allocation_for_form), 5, # Min, Max, Default, Step
                                key=f"allocation_edit_form_{selected_task_id_for_assignment}_{role_name_assign_form}"
                            )
                            new_assignment_data_from_form[role_name_assign_form] = allocation_input_form

                    if st.form_submit_button("💾 Save Assignments for this Task"):
                        updated_assignments_list_for_task = [{'role': r_name, 'allocation': alloc_val}
                                                             for r_name, alloc_val in new_assignment_data_from_form.items() if alloc_val > 0]
                        assignments_changed_flag = False
                        for i_task_loop, task_loop_item in enumerate(st.session_state.tasks):
                            if task_loop_item['id'] == selected_task_id_for_assignment:
                                # Compare new assignments with existing ones (robustly)
                                if json.dumps(parse_assignments(task_loop_item.get('assignments',[])),sort_keys=True) != json.dumps(updated_assignments_list_for_task,sort_keys=True):
                                    st.session_state.tasks[i_task_loop]['assignments'] = updated_assignments_list_for_task
                                    # Recalculate duration and end_date for this task as assignments changed
                                    st.session_state.tasks[i_task_loop]['duration_calc_days'] = calculate_estimated_duration_from_effort(
                                        st.session_state.tasks[i_task_loop]['effort_ph'],
                                        updated_assignments_list_for_task,
                                        st.session_state.roles,
                                        st.session_state.config['working_hours'],
                                        st.session_state.config['exclude_weekends']
                                    )
                                    if st.session_state.tasks[i_task_loop]['start_date'] and st.session_state.tasks[i_task_loop]['effort_ph'] > 0:
                                        st.session_state.tasks[i_task_loop]['end_date'] = calculate_end_date_from_effort(
                                            st.session_state.tasks[i_task_loop]['start_date'],
                                            st.session_state.tasks[i_task_loop]['effort_ph'],
                                            updated_assignments_list_for_task,
                                            st.session_state.roles,
                                            st.session_state.config['working_hours'],
                                            st.session_state.config['exclude_weekends']
                                        )
                                    assignments_changed_flag = True
                                break # Found and updated the task

                        if assignments_changed_flag:
                            st.success(f"Assignments and estimated duration/end date updated for '{task_to_edit_assignments['name']}'. Replan for leveled schedule.")
                            st.rerun()
                        else:
                            st.info("No changes detected in assignments for this task.")
            else: # Should not happen if selected_task_id_for_assignment is valid
                st.error(f"Task with ID {selected_task_id_for_assignment} not found. This is unexpected.")

# --- Gantt Tab ---
with tab_gantt:
    st.header("📊 Interactive Gantt Chart (Based on Current Plan)")
    st.caption("This Gantt chart visualizes tasks based on their current start and end dates. For resource-leveled dates, ensure you've used 'Replan with Resource Leveling'.")

    if not tasks_df_for_display.empty and \
       'start_date' in tasks_df_for_display.columns and \
       'end_date' in tasks_df_for_display.columns and \
       tasks_df_for_display['start_date'].notna().all() and \
       tasks_df_for_display['end_date'].notna().all():

        gantt_df_source = tasks_df_for_display.copy() # Use the prepared DataFrame

        # Prepare display columns for hover data
        gantt_df_source['effort_ph_display'] = gantt_df_source['effort_ph'].apply(lambda x: f"{x:.1f} PH")
        gantt_df_source['duration_actual_display'] = gantt_df_source.apply(
            lambda r: f"{(r['end_date'] - r['start_date']).days + 1 if pd.notna(r['start_date']) and pd.notna(r['end_date']) and isinstance(r['start_date'], datetime.date) and isinstance(r['end_date'], datetime.date) and r['end_date'] >= r['start_date'] else r['duration_calc_days']:.1f} d",
            axis=1
        )
        gantt_df_source['assignments_display_gantt'] = gantt_df_source['assignments'].apply(format_assignments_display)
        gantt_df_source['dependencies_display_gantt'] = gantt_df_source['dependencies'].apply(lambda d_str: format_dependencies_display(d_str, st.session_state.tasks))

        # Get phase colors for the Gantt chart
        phase_colors_for_gantt = gantt_df_source.set_index('phase')['phase_color'].to_dict()

        plotly_data_for_gantt_segments = []
        gantt_working_hours_config = st.session_state.config['working_hours']
        gantt_exclude_weekends_config = st.session_state.config['exclude_weekends']

        for _, task_row_gantt in gantt_df_source.iterrows():
             task_start = task_row_gantt['start_date']
             task_end = task_row_gantt['end_date']

             if isinstance(task_start, datetime.date) and isinstance(task_end, datetime.date) and task_start <= task_end:
                 # Get working segments for this task
                 working_segments_for_task = get_working_segments_from_dates(
                     task_start, task_end, gantt_exclude_weekends_config, gantt_working_hours_config
                 )
                 for segment_start_date, segment_end_date in working_segments_for_task:
                      # Plotly timeline x_end is exclusive, so add 1 day to the segment_end_date
                      plotly_segment_end_date = segment_end_date + datetime.timedelta(days=1)
                      task_data_for_segment = task_row_gantt.to_dict() # Copy all task data
                      task_data_for_segment['plotly_segment_start'] = segment_start_date
                      task_data_for_segment['plotly_segment_end'] = plotly_segment_end_date
                      plotly_data_for_gantt_segments.append(task_data_for_segment)

        if plotly_data_for_gantt_segments:
             gantt_segments_df = pd.DataFrame(plotly_data_for_gantt_segments)
             # Ensure dates are datetime objects for Plotly
             gantt_segments_df['plotly_segment_start'] = pd.to_datetime(gantt_segments_df['plotly_segment_start'])
             gantt_segments_df['plotly_segment_end'] = pd.to_datetime(gantt_segments_df['plotly_segment_end'])

             # Sort by original task start date then by segment start for consistent Y-axis order
             gantt_segments_df = gantt_segments_df.sort_values(by=['start_date', 'plotly_segment_start'])


             fig_gantt_chart = px.timeline(
                 gantt_segments_df,
                 x_start="plotly_segment_start",
                 x_end="plotly_segment_end",
                 y="name", # Task name on Y-axis
                 color="phase", # Color by phase
                 color_discrete_map=phase_colors_for_gantt,
                 title="Project Timeline",
                 hover_name="name", # Show task name prominently on hover
                 hover_data={ # Customize hover data
                     "start_date": "|%Y-%m-%d", # Show original task start date
                     "end_date": "|%Y-%m-%d",   # Show original task end date
                     "effort_ph_display": True,
                     "duration_actual_display": True, # Show calculated duration
                     "assignments_display_gantt": True,
                     "dependencies_display_gantt": True,
                     "status": True,
                     "cost": ":.2f€", # Format cost
                     "notes": True,
                     # Hide internal plotly segment dates from hover
                     "plotly_segment_start": False,
                     "plotly_segment_end": False,
                     "phase": False, # Already shown by color legend
                     "phase_color": False,
                     "assignments": False, # Show formatted display version
                     "dependencies": False # Show formatted display version
                 },
                 custom_data=["id"] # Can be used for callbacks if needed
             )
             fig_gantt_chart.update_layout(
                 xaxis_title="Date",
                 yaxis_title="Tasks",
                 legend_title_text="Phase",
                 yaxis=dict(autorange="reversed", tickfont=dict(size=10)), # Show tasks top-to-bottom
                 xaxis=dict(type='date', tickformat="%d-%b\n%Y"), # Date format on X-axis
                 title_x=0.5 # Center title
             )
             st.plotly_chart(fig_gantt_chart, use_container_width=True)
        else:
            st.info("No valid task segments found for Gantt chart. Ensure tasks are scheduled with valid start/end dates and that these dates span at least one working day according to the calendar settings.")
    elif not tasks_df_for_display.empty:
        st.warning("Gantt chart cannot be displayed. Some tasks may be missing valid start or end dates. Please check task data or use 'Replan with Resource Leveling'.")
    else:
        st.info("No tasks in the project plan. Add tasks to visualize them on the Gantt chart.")


# --- Dependencies Tab ---
with tab_deps:
    st.header("🔗 Dependency Visualization (Graph)")
    if not tasks_df_for_display.empty:
        try:
            dep_graph = graphviz.Digraph(comment='Project Dependency Diagram')
            dep_graph.attr(rankdir='LR') # Left-to-Right layout

            tasks_for_graph_list = st.session_state.tasks # Use direct session state for most current data
            status_colors_for_graph = {
                "Pending": "lightblue", "In Progress": "orange", "Completed": "lightgreen",
                "Blocked": "lightcoral", "Pending (Leveling Error)": "pink",
                "Pending (Dependency Error)": "lightgrey", "Pending (Leveled)": "lightyellow"
            }
            valid_task_ids_for_graph = {task_graph_node['id'] for task_graph_node in tasks_for_graph_list}

            for task_node_item in tasks_for_graph_list:
                assign_display_for_graph = format_assignments_display(task_node_item.get('assignments', []))

                # Determine duration display for graph node
                duration_display_for_graph = f"{task_node_item.get('duration_calc_days', '?'):.1f}d (est)" # Default to estimated
                if 'end_date' in task_node_item and isinstance(task_node_item.get('start_date'), datetime.date) and isinstance(task_node_item.get('end_date'), datetime.date) and task_node_item['end_date'] >= task_node_item['start_date']:
                    actual_duration_val = (task_node_item['end_date'] - task_node_item['start_date']).days + 1
                    duration_display_for_graph = f"{actual_duration_val}d (sched)" # Leveled/Scheduled duration

                node_label_html = f'''<{task_node_item.get('name', 'Unknown Task Name')}<BR/>
                                    <FONT POINT-SIZE="10">
                                    ID: {task_node_item.get('id', '?')}<BR/>
                                    Effort: {task_node_item.get('effort_ph', '?')} PH | Dur: {duration_display_for_graph}<BR/>
                                    Status: {task_node_item.get('status', 'N/A')}<BR/>
                                    Assignments: {assign_display_for_graph}
                                    </FONT>>'''
                node_fill_color = status_colors_for_graph.get(task_node_item.get('status', 'Pending'), 'lightgrey') # Default color
                dep_graph.node(
                    str(task_node_item['id']),
                    label=node_label_html,
                    shape='box',
                    style='filled',
                    fillcolor=node_fill_color
                )

            # Add edges for dependencies
            for task_edge_item in tasks_for_graph_list:
                dependencies_for_edges = parse_dependencies(task_edge_item.get('dependencies', '[]'))
                for dep_id_edge in dependencies_for_edges:
                    if dep_id_edge in valid_task_ids_for_graph: # Ensure dependency exists as a node
                        dep_graph.edge(str(dep_id_edge), str(task_edge_item['id']))

            st.graphviz_chart(dep_graph, use_container_width=True)
        except ImportError:
            st.error("The 'graphviz' library is not installed or not configured correctly on your system. Please install it to use this feature.")
            st.code("pip install graphviz")
            st.info("You also need to install Graphviz system-wide. Download from: https://graphviz.org/download/")
        except Exception as e_dep_graph:
            st.error(f"An error occurred while generating the dependency graph: {e_dep_graph}")
            logging.error(f"Dependency graph generation error: {e_dep_graph}", exc_info=True)
    else:
        st.info("No tasks in the project plan. Add tasks and define their dependencies to visualize the graph.")

# --- Resources Tab ---
with tab_resources:
    st.header("👥 Resource Workload (Based on Leveled Plan)")
    st.caption("This tab shows daily workload per role based on the last 'Replan with Resource Leveling' calculation. If no leveled data is available, it attempts an approximation.")

    # Use the prepared tasks_df_for_display for date ranges, but leveled_resource_schedule for actual load data
    if (not tasks_df_for_display.empty and
        'start_date' in tasks_df_for_display.columns and 'end_date' in tasks_df_for_display.columns and
        tasks_df_for_display['start_date'].notna().all() and tasks_df_for_display['end_date'].notna().all() and
        st.session_state.roles):

        # Get overall project date range from the tasks_df_for_display (which reflects current plan)
        project_min_date_overall = tasks_df_for_display['start_date'].min()
        project_max_date_overall = tasks_df_for_display['end_date'].max()

        if isinstance(project_min_date_overall, datetime.date) and \
           isinstance(project_max_date_overall, datetime.date) and \
           project_min_date_overall <= project_max_date_overall:

            leveled_schedule_data_res = st.session_state.get('leveled_resource_schedule', {})
            workload_data_for_chart = []

            if leveled_schedule_data_res:
                st.info("Displaying workload from the last resource leveling calculation (found in session state).")
                for date_val_dt_obj, roles_load_on_date in leveled_schedule_data_res.items():
                    # Ensure date_val_dt_obj is a datetime.date object
                    current_date_for_load_chart = None
                    if isinstance(date_val_dt_obj, str):
                        try: current_date_for_load_chart = datetime.date.fromisoformat(date_val_dt_obj)
                        except ValueError: pass
                    elif isinstance(date_val_dt_obj, datetime.datetime):
                         current_date_for_load_chart = date_val_dt_obj.date()
                    elif isinstance(date_val_dt_obj, datetime.date):
                         current_date_for_load_chart = date_val_dt_obj
                    else:
                        logging.warning(f"Skipping unknown date format in leveled_resource_schedule: {date_val_dt_obj}")
                        continue

                    if current_date_for_load_chart:
                        for role_name_load, hours_loaded in roles_load_on_date.items():
                            workload_data_for_chart.append({'Date': pd.to_datetime(current_date_for_load_chart), 'Role': role_name_load, 'Load (h)': hours_loaded})
            else: # Fallback if no leveled schedule data
                st.warning("No detailed leveled schedule data found from 'Replan with Resource Leveling'. Displaying an approximation of daily load. For accurate data, please run the replanning process from the Settings tab.")
                # Fallback approximation logic (less accurate)
                for _, task_res_approx_row in tasks_df_for_display.iterrows():
                    task_start_approx_date = task_res_approx_row['start_date']
                    task_end_approx_date = task_res_approx_row['end_date']
                    assignments_approx_list = parse_assignments(task_res_approx_row.get('assignments',[]))
                    task_effort_approx_val = task_res_approx_row.get('effort_ph', 0.0)

                    if isinstance(task_start_approx_date, datetime.date) and isinstance(task_end_approx_date, datetime.date) and \
                       task_start_approx_date <= task_end_approx_date and assignments_approx_list and task_effort_approx_val > 0:

                        task_actual_working_days_count = 0
                        temp_date_for_day_count = task_start_approx_date
                        while temp_date_for_day_count <= task_end_approx_date:
                            if get_working_hours_for_date(temp_date_for_day_count, current_working_hours_prep) > 0:
                               task_actual_working_days_count +=1
                            temp_date_for_day_count += datetime.timedelta(days=1)

                        if task_actual_working_days_count == 0: continue # Avoid division by zero

                        avg_effort_per_actual_working_day_for_task = task_effort_approx_val / task_actual_working_days_count

                        current_d_approx_loop_date = task_start_approx_date
                        while current_d_approx_loop_date <= task_end_approx_date:
                            if get_working_hours_for_date(current_d_approx_loop_date, current_working_hours_prep) > 0: # Only consider working days
                                for assign_approx_loop_item in assignments_approx_list:
                                    role_approx_loop_name = assign_approx_loop_item['role']
                                    alloc_approx_loop_pct_val = assign_approx_loop_item.get('allocation', 0) / 100.0

                                    # Distribute the task's average daily effort based on role's allocation to *this task*
                                    # This is a rough approximation.
                                    role_share_effort_approx_today = avg_effort_per_actual_working_day_for_task * alloc_approx_loop_pct_val

                                    workload_data_for_chart.append({'Date': pd.to_datetime(current_d_approx_loop_date), 'Role': role_approx_loop_name, 'Load (h)': role_share_effort_approx_today})
                            current_d_approx_loop_date += datetime.timedelta(days=1)

            if workload_data_for_chart:
                load_df_for_charting = pd.DataFrame(workload_data_for_chart)
                # Group by Date and Role, summing up hours if a role works on multiple tasks on the same day
                load_summary_for_charting = load_df_for_charting.groupby(['Date', 'Role'])['Load (h)'].sum().reset_index()
                load_summary_for_charting = load_summary_for_charting.sort_values(by=['Date', 'Role'])

                st.subheader("📈 Daily Workload vs Capacity per Role")
                all_roles_for_chart_select = sorted(list(st.session_state.roles.keys()))
                selected_role_for_chart = st.selectbox(
                    "Select Role to Analyze:", all_roles_for_chart_select,
                    index=0 if all_roles_for_chart_select else -1, # Default to first role if any
                    key="resource_workload_role_selector"
                )

                if selected_role_for_chart:
                    role_specific_load_df = load_summary_for_charting[load_summary_for_charting['Role'] == selected_role_for_chart]

                    # Calculate capacity line for the selected role over the project duration
                    dates_range_for_capacity_chart = pd.date_range(project_min_date_overall, project_max_date_overall, freq='D')
                    role_capacity_data_for_chart = []
                    selected_role_info = st.session_state.roles.get(selected_role_for_chart, {})
                    role_availability_percent = selected_role_info.get('availability_percent', 100.0)

                    for date_in_range_cap in dates_range_for_capacity_chart:
                        date_obj_for_cap_calc = date_in_range_cap.date() # Convert pandas Timestamp to datetime.date
                        daily_system_hours_for_cap = get_working_hours_for_date(date_obj_for_cap_calc, current_working_hours_prep)

                        # Role's capacity for the day = system working hours * role's general availability %
                        role_capacity_on_day = daily_system_hours_for_cap * (role_availability_percent / 100.0)
                        role_capacity_data_for_chart.append({"Date": date_in_range_cap, "Capacity (h)": role_capacity_on_day})

                    role_capacity_df_for_plot = pd.DataFrame(role_capacity_data_for_chart)

                    fig_role_workload = go.Figure()
                    # Plot actual load as bars
                    fig_role_workload.add_trace(go.Bar(
                        x=role_specific_load_df['Date'], y=role_specific_load_df['Load (h)'],
                        name=f'{selected_role_for_chart} Actual Load', marker_color='rgba(55, 83, 109, 0.7)'
                    ))
                    # Plot capacity as a line
                    if not role_capacity_df_for_plot.empty:
                        fig_role_workload.add_trace(go.Scatter(
                            x=role_capacity_df_for_plot['Date'], y=role_capacity_df_for_plot['Capacity (h)'],
                            mode='lines', name=f'{selected_role_for_chart} Capacity',
                            line=dict(dash='solid', color='red', width=2)
                        ))

                    fig_role_workload.update_layout(
                        title=f'Workload vs Capacity for: {selected_role_for_chart}',
                        xaxis_title="Date", yaxis_title="Working Hours",
                        legend_title_text="Metric", barmode='overlay', title_x=0.5,
                        xaxis=dict(type='date', tickformat="%d-%b\n%Y")
                    )
                    st.plotly_chart(fig_role_workload, use_container_width=True)

                    # Overload detection and display
                    if not role_specific_load_df.empty and not role_capacity_df_for_plot.empty:
                        # Merge load and capacity on Date
                        merged_role_data_df = pd.merge(role_specific_load_df, role_capacity_df_for_plot, on="Date", how="left")
                        merged_role_data_df['Capacity (h)'] = merged_role_data_df['Capacity (h)'].fillna(0) # Fill NaNs if any date mismatch
                        merged_role_data_df['Overload (h)'] = merged_role_data_df['Load (h)'] - merged_role_data_df['Capacity (h)']
                        overloaded_days_df = merged_role_data_df[merged_role_data_df['Overload (h)'] > 0.01] # Small tolerance for float precision

                        if not overloaded_days_df.empty:
                            st.warning(f"**{selected_role_for_chart} is overloaded on the following days:**")
                            overloaded_days_display = overloaded_days_df[['Date', 'Load (h)', 'Capacity (h)', 'Overload (h)']].copy()
                            overloaded_days_display['Date'] = overloaded_days_display['Date'].dt.strftime('%Y-%m-%d (%a)') # Format date
                            st.dataframe(
                                overloaded_days_display.style.format({'Load (h)':'{:.1f}h','Capacity (h)':'{:.1f}h','Overload (h)':'{:.1f}h (Over)'}),
                                hide_index=True, use_container_width=True
                            )
                        else:
                            st.success(f"{selected_role_for_chart} has no overloaded days based on the current plan.")
                else: # No role selected or no roles exist
                    st.info("Select a role to see their specific workload and capacity.")

                st.divider()
                st.subheader("📊 Total Load Summary (Aggregated Person-Hours per Role)")
                total_hours_summary_per_role = load_summary_for_charting.groupby('Role')['Load (h)'].sum().reset_index()
                total_hours_summary_per_role.rename(columns={'Load (h)': 'Total Hours', 'Role': 'Role Name'}, inplace=True)
                st.dataframe(
                    total_hours_summary_per_role.sort_values(by='Total Hours', ascending=False).style.format({'Total Hours': '{:,.1f} h'}),
                    hide_index=True, use_container_width=True
                )
            else: # No workload_data_for_chart
                st.info("No workload data to display. This could be due to no tasks, no assignments, or an issue with date calculations. Ensure tasks are scheduled and resources are leveled.")
        else: # Invalid project date range
            st.warning("Cannot determine overall project date range for workload analysis. Ensure tasks have valid start and end dates after planning.")
    elif not tasks_df_for_display.empty: # Tasks exist but other conditions not met
        st.warning("Missing necessary data for workload analysis (e.g., some tasks might lack dates, or roles are not defined). Please check your project setup or re-run planning.")
    else: # No tasks at all
        st.info("Add tasks with assignments to visualize resource workload.")


# --- Costs Tab ---
with tab_costs:
    st.header("💰 Estimated Costs Summary (Based on Total Effort PH)")
    # Use the centrally prepared tasks_df_for_display
    if not tasks_df_for_display.empty and 'cost' in tasks_df_for_display.columns and tasks_df_for_display['cost'].notna().any():
        total_gross_cost_calculated = tasks_df_for_display['cost'].sum()
        profit_margin_percentage_config = st.session_state.config.get('profit_margin_percent', 0.0)
        profit_amount_calculated = total_gross_cost_calculated * (profit_margin_percentage_config / 100.0)
        total_selling_price_calculated = total_gross_cost_calculated + profit_amount_calculated

        st.subheader("Overall Financial Summary")
        cost_summary_cols = st.columns(4)
        with cost_summary_cols[0]:
            st.metric(label="Total Estimated Gross Cost", value=f"€ {total_gross_cost_calculated:,.2f}")
        with cost_summary_cols[1]:
            st.metric(label="Configured Profit Margin", value=f"{profit_margin_percentage_config:.2f} %")
        with cost_summary_cols[2]:
            st.metric(label="Estimated Profit Amount", value=f"€ {profit_amount_calculated:,.2f}")
        with cost_summary_cols[3]:
            st.metric(label="Estimated Total Selling Price", value=f"€ {total_selling_price_calculated:,.2f}")

        st.divider()
        st.subheader("📊 Export Cost Model (Excel)")
        if st.button("📤 Export Cost Breakdown to Excel", key="export_excel_cost_model_button"):
            if not tasks_df_for_display.empty and st.session_state.roles and st.session_state.config and st.session_state.phases:
                excel_file_data = export_cost_model_to_excel(
                    tasks_df_for_display, st.session_state.roles, st.session_state.config, st.session_state.phases
                )
                st.download_button(
                    label="📥 Download Cost Model (Excel File)",
                    data=excel_file_data,
                    file_name=f"project_cost_model_{datetime.date.today()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_excel_cost_file_button"
                )
                st.success("Cost model Excel file generated. Click the download button above.")
            else:
                st.error("Insufficient data to generate the cost model. Requires tasks, roles, phases, and project configuration.")

        st.divider()
        st.subheader("Cost Breakdown by Role (Based on Total Effort PH Distribution)")
        cost_by_role_data_list = []
        for _, task_cost_row_item in tasks_df_for_display.iterrows():
            effort_ph_for_cost_calc = task_cost_row_item.get('effort_ph', 0.0)
            assignments_for_cost_calc = parse_assignments(task_cost_row_item.get('assignments', [])) # Ensure parsed
            if effort_ph_for_cost_calc > 0 and assignments_for_cost_calc:
                # total_task_cost_from_df = task_cost_row_item.get('cost', 0.0) # Already calculated

                # To distribute cost by role, we need to recalculate based on effort proportion for each role in this task
                total_task_specific_allocation_sum_for_cost_dist = sum(assign_cost_dist.get('allocation', 0) for assign_cost_dist in assignments_for_cost_calc)

                if total_task_specific_allocation_sum_for_cost_dist > 0:
                    for assign_cost_item_dist in assignments_for_cost_calc:
                        role_name_for_cost_dist = assign_cost_item_dist.get('role')
                        role_allocation_percent_for_dist = assign_cost_item_dist.get('allocation', 0)

                        proportion_of_effort_for_role_in_task = role_allocation_percent_for_dist / total_task_specific_allocation_sum_for_cost_dist
                        effort_for_this_role_in_this_task = effort_ph_for_cost_calc * proportion_of_effort_for_role_in_task
                        role_hourly_rate_for_calc = get_role_rate(role_name_for_cost_dist)
                        cost_for_this_role_in_this_task = effort_for_this_role_in_this_task * role_hourly_rate_for_calc
                        cost_by_role_data_list.append({'Role': role_name_for_cost_dist, 'Cost (€)': cost_for_this_role_in_this_task})

        if cost_by_role_data_list:
            cost_by_role_df_aggregated = pd.DataFrame(cost_by_role_data_list)
            cost_by_role_summary_df = cost_by_role_df_aggregated.groupby('Role')['Cost (€)'].sum().reset_index()
            cost_by_role_summary_df = cost_by_role_summary_df.sort_values(by='Cost (€)', ascending=False)

            col_cost_table_by_role, col_cost_chart_by_role = st.columns([0.6, 0.4])
            with col_cost_table_by_role:
                st.write("**Total Cost per Role**")
                st.dataframe(
                    cost_by_role_summary_df.style.format({'Cost (€)': '€ {:,.2f}'}),
                    use_container_width=True, hide_index=True
                )
            with col_cost_chart_by_role:
                if not cost_by_role_summary_df.empty and cost_by_role_summary_df['Cost (€)'].sum() > 0:
                    fig_pie_chart_cost_by_role = px.pie(
                        cost_by_role_summary_df, values='Cost (€)', names='Role',
                        title='Cost Distribution by Role', hole=0.3
                    )
                    fig_pie_chart_cost_by_role.update_traces(textposition='inside', textinfo='percent+label')
                    fig_pie_chart_cost_by_role.update_layout(showlegend=False, title_x=0.5, margin=dict(l=0, r=0, t=30, b=0))
                    st.plotly_chart(fig_pie_chart_cost_by_role, use_container_width=True)
                else:
                    st.info("No positive costs to display in the role distribution chart.")
        else:
            st.info("Could not calculate cost breakdown by role. Ensure tasks have effort, assignments, and roles have defined hourly rates.")

        st.divider()
        st.subheader("Cost Breakdown by Task (Based on Total Effort PH)")
        cost_by_task_display_df = tasks_df_for_display[['id', 'phase', 'subtask', 'cost']].copy()
        cost_by_task_display_df.rename(columns={'cost': 'Estimated Cost (€)', 'phase': 'Phase', 'subtask':'Subtask'}, inplace=True)

        filter_col_phase_cost, filter_col_subtask_cost = st.columns(2)
        with filter_col_phase_cost:
            unique_phases_for_filter = sorted(cost_by_task_display_df['Phase'].unique())
            selected_phases_filter = st.multiselect(
                "Filter by Phase:", options=unique_phases_for_filter, default=[], key="filter_phase_cost_tab"
            )
        with filter_col_subtask_cost:
            # Filter subtask options based on selected phases if any, else show all
            subtasks_options_for_filter = cost_by_task_display_df['Subtask'].unique()
            if selected_phases_filter:
                subtasks_options_for_filter = sorted(cost_by_task_display_df[cost_by_task_display_df['Phase'].isin(selected_phases_filter)]['Subtask'].unique())
            else:
                subtasks_options_for_filter = sorted(subtasks_options_for_filter)

            selected_subtasks_filter = st.multiselect(
                "Filter by Subtask:", options=subtasks_options_for_filter, default=[], key="filter_subtask_cost_tab"
            )

        filtered_cost_by_task_df = cost_by_task_display_df.copy()
        if selected_phases_filter:
            filtered_cost_by_task_df = filtered_cost_by_task_df[filtered_cost_by_task_df['Phase'].isin(selected_phases_filter)]
        if selected_subtasks_filter: # This will further filter the already phase-filtered df
            filtered_cost_by_task_df = filtered_cost_by_task_df[filtered_cost_by_task_df['Subtask'].isin(selected_subtasks_filter)]

        filtered_cost_by_task_df = filtered_cost_by_task_df.sort_values(by='Estimated Cost (€)', ascending=False)
        st.dataframe(
            filtered_cost_by_task_df[['Phase', 'Subtask', 'Estimated Cost (€)']].style.format({'Estimated Cost (€)': '€ {:,.2f}'}),
            use_container_width=True, hide_index=True
        )
        total_filtered_task_cost = filtered_cost_by_task_df['Estimated Cost (€)'].sum()
        st.info(f"**Total Cost of Filtered Tasks:** € {total_filtered_task_cost:,.2f}")

    elif not tasks_df_for_display.empty: # Tasks exist, but cost column might be missing or all NaN
        st.warning("Could not calculate or display costs. Ensure tasks have effort and assignments, and roles have defined rates. Also, check if the 'cost' column is properly calculated.")
    else: # No tasks
        st.info("Add tasks with effort and assignments, and define role rates in Settings to see cost estimations.")

