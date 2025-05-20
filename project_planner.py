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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(funcName)s - %(levelname)s - %(message)s')


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
                     pass
         task['assignments'] = valid_assignments
    task.setdefault('macro', 'No Phase')
    task.setdefault('subtask', 'No Subtask')
    task.setdefault('phase_color', st.session_state.macrotasks.get(task['macro'], "#CCCCCC"))
    task['name'] = f"{str(task.get('macro','No Phase')).strip()} - {str(task.get('subtask','No Subtask')).strip()}"


# --- HELPER FUNCTIONS ---

def get_working_hours_for_date(target_date: datetime.date, working_hours_config: dict) -> float:
    if not isinstance(target_date, datetime.date) or not isinstance(working_hours_config, dict):
        return 0.0
    month = target_date.month
    day_name = target_date.strftime("%A") 
    
    monthly_schedule = working_hours_config.get('monthly_overrides', {}).get(str(month)) 
    if monthly_schedule and isinstance(monthly_schedule, dict):
        return monthly_schedule.get(day_name, 0.0)
    else:
        default_schedule = working_hours_config.get('default', {})
        return default_schedule.get(day_name, 0.0)

def get_next_working_day(input_date: datetime.date, working_hours_config: dict, exclude_weekends: bool) -> datetime.date:
    next_day = input_date
    while True:
        day_hours = get_working_hours_for_date(next_day, working_hours_config)
        is_weekend_day = next_day.weekday() >= 5 
        
        if day_hours > 0:
            if not (exclude_weekends and is_weekend_day):
                return next_day
        
        next_day += datetime.timedelta(days=1)
        if (next_day - input_date).days > 365: 
             logging.warning(f"Could not find next working day within 365 days of {input_date}. Returning original + 1.")
             return input_date + datetime.timedelta(days=1)

def calculate_estimated_duration_from_effort(effort_ph: float, assignments: list, roles_config: dict, working_hours_config: dict, exclude_weekends: bool) -> float:
    if effort_ph <= 0 or not assignments:
        return 0.5 

    default_daily_hours_sum = sum(h for day, h in working_hours_config.get('default', {}).items() if h > 0 and not (exclude_weekends and (day == "Saturday" or day == "Sunday")))
    avg_working_days_per_week = sum(1 for day, h in working_hours_config.get('default', {}).items() if h > 0 and not (exclude_weekends and (day == "Saturday" or day == "Sunday")))
    if avg_working_days_per_week == 0: return 999 
    avg_daily_hours_capacity = default_daily_hours_sum / avg_working_days_per_week if avg_working_days_per_week > 0 else 0
    if avg_daily_hours_capacity <=0: return 999

    total_weighted_role_contribution_per_day = 0
    for assign in assignments:
        role_name = assign['role']
        allocation_pct = assign['allocation'] / 100.0
        role_info = roles_config.get(role_name, {})
        role_availability_pct = role_info.get('availability_percent', 100.0) / 100.0
        role_max_hours_on_task_per_day = (avg_daily_hours_capacity * role_availability_pct) * allocation_pct
        total_weighted_role_contribution_per_day += role_max_hours_on_task_per_day
        
    if total_weighted_role_contribution_per_day <= 0:
        return 999 

    estimated_days = effort_ph / total_weighted_role_contribution_per_day
    return max(0.5, math.ceil(estimated_days * 2) / 2) 

def calculate_end_date_from_effort(start_date: datetime.date, effort_ph: float, assignments: list, roles_config: dict, working_hours_config: dict, exclude_weekends: bool):
    if not isinstance(start_date, datetime.date): # Ensure start_date is a date object
        logging.error(f"Invalid start_date type for calculate_end_date_from_effort: {start_date}")
        return None # Or a sensible default like datetime.date.today() + timedelta(days=1)
        
    if effort_ph <= 0:
        return get_next_working_day(start_date, working_hours_config, exclude_weekends)

    remaining_effort = float(effort_ph)
    current_date = get_next_working_day(start_date, working_hours_config, exclude_weekends) 
    
    days_simulated = 0
    MAX_SIM_DAYS = 365 * 3 

    while remaining_effort > 1e-6 and days_simulated < MAX_SIM_DAYS : 
        days_simulated +=1
        daily_total_working_hours_system = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend_day = current_date.weekday() >= 5
        
        if daily_total_working_hours_system > 0 and not (exclude_weekends and is_weekend_day):
            effort_done_today = 0
            for assign in assignments:
                role_name = assign['role']
                allocation_to_task_pct = assign['allocation'] / 100.0 
                role_detail = roles_config.get(role_name, {})
                role_general_availability_pct = role_detail.get('availability_percent', 100.0) / 100.0 
                role_max_hours_today = daily_total_working_hours_system * role_general_availability_pct
                role_hours_on_this_task_today = role_max_hours_today * allocation_to_task_pct
                effort_done_today += role_hours_on_this_task_today
            
            remaining_effort -= effort_done_today
            if remaining_effort <= 1e-6:
                return current_date 
        
        current_date = get_next_working_day(current_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)

    if days_simulated >= MAX_SIM_DAYS:
        logging.error(f"calculate_end_date_from_effort exceeded MAX_SIM_DAYS for task starting {start_date} with effort {effort_ph}.")
        return current_date 
    
    return current_date 

def calculate_end_date(start_date, duration_days, exclude_weekends=True, working_hours_config=None):
    if working_hours_config is None:
        working_hours_config = st.session_state.config['working_hours']

    if not isinstance(start_date, datetime.date) or not isinstance(duration_days, (int, float)) or duration_days <= 0:
        return None

    current_date = get_next_working_day(start_date, working_hours_config, exclude_weekends)
    days_counted = 0.0

    if duration_days < 1.0: 
        return current_date

    days_counted = 1.0 
    
    while days_counted < duration_days:
        current_date = get_next_working_day(current_date + datetime.timedelta(days=1), working_hours_config, exclude_weekends)
        days_counted += 1.0
        if (current_date - start_date).days > (duration_days * 7 + 60): 
            logging.error(f"calculate_end_date loop exceeded safety for start={start_date}, duration={duration_days}")
            return current_date 
    return current_date


def get_task_by_id(task_id, task_list):
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
    role = st.session_state.roles.get(role_name, {})
    return role.get("rate_eur_hr", 0)

def parse_assignments(assign_input):
    if isinstance(assign_input, list):
        valid_assignments = []
        for assign in assign_input:
            if isinstance(assign, dict) and 'role' in assign and 'allocation' in assign:
                try:
                    allocation_val = float(assign['allocation'])
                    if 0 <= allocation_val <= 100:
                        valid_assignments.append({'role': assign['role'], 'allocation': allocation_val})
                except (ValueError, TypeError): pass # Skip invalid
        return valid_assignments
    elif isinstance(assign_input, str) and assign_input.strip():
        try:
            assignments = json.loads(assign_input)
            return parse_assignments(assignments) 
        except (json.JSONDecodeError, TypeError): pass
    return []


def calculate_task_cost_by_effort(task_effort_ph: float, assignments_list: list, roles_config: dict):
    if task_effort_ph <= 0: return 0.0
    total_cost = 0.0
    valid_assignments = parse_assignments(assignments_list)
    total_allocation_pct_for_task = sum(assign.get('allocation', 0) for assign in valid_assignments)
    if total_allocation_pct_for_task <= 0: return 0.0

    for assign in valid_assignments:
        role_name = assign.get('role')
        proportion_of_effort_by_role = assign.get('allocation', 0) / total_allocation_pct_for_task
        effort_by_this_role = task_effort_ph * proportion_of_effort_by_role
        hourly_rate = get_role_rate(role_name)
        total_cost += effort_by_this_role * hourly_rate
    return total_cost


def parse_dependencies(dep_input):
    if isinstance(dep_input, list):
        valid_deps = []
        for d in dep_input:
            try: valid_deps.append(int(d))
            except (ValueError, TypeError): pass
        return valid_deps
    elif isinstance(dep_input, str) and dep_input.strip():
        try:
            deps = json.loads(dep_input)
            if isinstance(deps, list): return parse_dependencies(deps)
        except (json.JSONDecodeError, TypeError): pass
    return []

def get_task_name(task_id, task_list):
    task = get_task_by_id(task_id, task_list)
    return task.get('name', f"ID {task_id}?") if task else f"ID {task_id}?"

def format_dependencies_display(dep_str, task_list):
    dep_list = parse_dependencies(dep_str)
    return ", ".join([get_task_name(dep_id, task_list) for dep_id in dep_list]) if dep_list else "None"

def format_assignments_display(assignments_list):
    valid_assignments = parse_assignments(assignments_list)
    if not valid_assignments: return "None"
    return ", ".join([f"{a.get('role','?')} ({a.get('allocation',0):.0f}%)" for a in valid_assignments])


def get_working_segments_from_dates(task_start_date: datetime.date, task_end_date: datetime.date, exclude_weekends: bool, working_hours_config: dict) -> list:
    segments = []
    if not isinstance(task_start_date, datetime.date) or not isinstance(task_end_date, datetime.date) or task_start_date > task_end_date:
        return segments
    current_date = task_start_date
    current_segment_start = None
    while current_date <= task_end_date:
        day_hours = get_working_hours_for_date(current_date, working_hours_config)
        is_weekend_day = current_date.weekday() >= 5
        is_working_day_for_gantt = day_hours > 0 and not (exclude_weekends and is_weekend_day)
        if is_working_day_for_gantt:
            if current_segment_start is None:
                current_segment_start = current_date
        else: 
            if current_segment_start is not None:
                segments.append((current_segment_start, current_date - datetime.timedelta(days=1)))
                current_segment_start = None
        if current_date == task_end_date and current_segment_start is not None:
             segments.append((current_segment_start, current_date)) 
        current_date += datetime.timedelta(days=1)
    return segments


def get_ai_template_data():
    project_start_date = st.session_state.config.get('project_start_date', datetime.date.today())
    roles_cfg = {
        'Tech Lead': {"availability_percent": 50.0, "rate_eur_hr": 46.0},
        'AI Engineer': {"availability_percent": 50.0, "rate_eur_hr": 27.0},
        'AI Engineer Senior': {"availability_percent": 50.0, "rate_eur_hr": 37.0}
    }
    tasks_structure = [
        {"id": 1, "macro": "Fase 0", "subtask": "Kick-off & Planning", "effort_ph": 20, "assignments": [{"role": "Tech Lead", "allocation": 50}, {"role": "AI Engineer Senior", "allocation": 25}], "dependencies": [], "notes": "Align team, refine plan."},
        {"id": 2, "macro": "Fase 1", "subtask": "Benchmark Research", "effort_ph": 40, "assignments": [{"role": "AI Engineer", "allocation": 50}, {"role": "AI Engineer Senior", "allocation": 50}], "dependencies": [1], "notes": "Investigate SOTA models."},
        {"id": 3, "macro": "Fase 1", "subtask": "Define Metrics & Setup", "effort_ph": 16, "assignments": [{"role": "Tech Lead", "allocation": 25}, {"role": "AI Engineer Senior", "allocation": 25}], "dependencies": [2], "notes": "Key evaluation metrics and environment setup."},
        {"id": 4, "macro": "Fase 2", "subtask": "Fine-tune VLM", "effort_ph": 80, "assignments": [{"role": "AI Engineer", "allocation": 50}, {"role": "AI Engineer Senior", "allocation": 50}], "dependencies": [3], "notes": "Adapt selected VLM."},
        {"id": 5, "macro": "Fase 3", "subtask": "Develop RAG Prototype", "effort_ph": 60, "assignments": [{"role": "Tech Lead", "allocation": 25},{"role": "AI Engineer", "allocation": 50}], "dependencies": [4], "notes": "Build local RAG."}
    ]
    tasks = []
    task_end_dates_map = {}
    processed_ids = set()
    exclude_weekends_cfg = st.session_state.config.get('exclude_weekends', True)
    working_hours_cfg = st.session_state.config['working_hours']
    task_dict_template = {task['id']: task for task in tasks_structure}
    ids_to_process_template = sorted(list(task_dict_template.keys()))
    max_iterations_template = len(ids_to_process_template) * 2
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
                if start_date_template is None: calculation_ok_template = False; break
                effort_ph_template = task_data_template.get('effort_ph', 1)
                assignments_template = parse_assignments(task_data_template.get('assignments', []))
                duration_calc_days_template = calculate_estimated_duration_from_effort(effort_ph_template, assignments_template, roles_cfg, working_hours_cfg, exclude_weekends_cfg)
                end_date_template = calculate_end_date_from_effort(start_date_template, effort_ph_template, assignments_template, roles_cfg, working_hours_cfg, exclude_weekends_cfg)
                if end_date_template is None: end_date_template = start_date_template 
                final_task_template = task_data_template.copy()
                final_task_template['start_date'] = start_date_template
                final_task_template['effort_ph'] = effort_ph_template
                final_task_template['duration_calc_days'] = duration_calc_days_template 
                final_task_template['dependencies'] = json.dumps(dependencies_template)
                final_task_template['status'] = 'Pending'
                final_task_template['notes'] = task_data_template.get('notes', '')
                final_task_template['parent_id'] = None
                final_task_template['assignments'] = assignments_template 
                final_task_template['phase_color'] = st.session_state.macrotasks.get(final_task_template.get('macro', ''), "#CCCCCC")
                final_task_template['name'] = f"{final_task_template.get('macro','No Phase')} - {final_task_template.get('subtask','No Subtask')}"
                tasks.append(final_task_template)
                task_end_dates_map[task_id_template] = end_date_template
                processed_ids.add(task_id_template)
                processed_in_iteration_template = True
        if not calculation_ok_template: break
        iterations_template += 1
        if not processed_in_iteration_template and len(processed_ids) < len(ids_to_process_template):
            logging.error("Template Load: Could not resolve dependencies for all tasks."); calculation_ok_template = False; break
    if not calculation_ok_template:
        st.error("Error calculating template dates. Data was not loaded."); return {}, [], 1
    next_id_template = max(task_dict_template.keys()) + 1 if task_dict_template else 1
    for task_template_item in tasks_structure: 
         macro_name_template = task_template_item.get('macro')
         if macro_name_template and macro_name_template not in st.session_state.macrotasks:
             st.session_state.macrotasks[macro_name_template] = "#ADD8E6" 
    return roles_cfg, tasks, next_id_template

# --- Resource Leveling Functions ---

def calculate_dependent_start_date_for_scheduling(dependencies_str, task_end_dates_map, default_start_date, working_hours_config, exclude_weekends):
    dep_ids = parse_dependencies(dependencies_str)
    latest_dependency_finish = None
    if dep_ids:
        try:
            valid_end_dates = [task_end_dates_map[dep_id] for dep_id in dep_ids if dep_id in task_end_dates_map and isinstance(task_end_dates_map[dep_id], datetime.date)]
            if valid_end_dates:
                latest_dependency_finish = max(valid_end_dates)
        except KeyError as e:
            logging.error(f"Critical error: End date for dependency {e} not found in task_end_dates_map: {task_end_dates_map}")
            return None 
    earliest_start = default_start_date
    if latest_dependency_finish:
        earliest_start = latest_dependency_finish + datetime.timedelta(days=1)
    return get_next_working_day(earliest_start, working_hours_config, exclude_weekends)

def check_and_get_daily_effort_capacity(
    current_date: datetime.date,
    task_assignments: list,
    current_schedule_hours: dict, 
    roles_config: dict,
    working_hours_config: dict,
    exclude_weekends: bool
) -> tuple[bool, dict]:
    daily_system_hours = get_working_hours_for_date(current_date, working_hours_config)
    is_weekend_day = current_date.weekday() >= 5
    if not (daily_system_hours > 0 and not (exclude_weekends and is_weekend_day)):
        return False, {} 
    available_effort_today_by_role = {}
    for assign in task_assignments:
        role_name = assign['role']
        allocation_to_task_pct = assign['allocation'] / 100.0 
        if allocation_to_task_pct <= 0:
            available_effort_today_by_role[role_name] = 0.0 
            continue
        role_detail = roles_config.get(role_name, {})
        role_general_availability_pct = role_detail.get('availability_percent', 100.0) / 100.0 
        role_max_possible_hours_today = daily_system_hours * role_general_availability_pct
        hours_already_scheduled_for_role = current_schedule_hours.get(current_date, {}).get(role_name, 0.0)
        role_remaining_general_capacity_today = role_max_possible_hours_today - hours_already_scheduled_for_role
        potential_hours_for_this_task = role_max_possible_hours_today * allocation_to_task_pct # Max role could do for *this task* if fully free
        actual_available_for_this_task = max(0, min(role_remaining_general_capacity_today, potential_hours_for_this_task))
        available_effort_today_by_role[role_name] = actual_available_for_this_task
    
    can_schedule_today = sum(available_effort_today_by_role.values()) > 1e-6 or not any(a['allocation'] > 0 for a in task_assignments)
    return can_schedule_today, available_effort_today_by_role

def update_hourly_schedule_with_effort(
    current_date: datetime.date,
    effort_done_by_role_today: dict, 
    schedule_hours: dict 
):
    if current_date not in schedule_hours:
        schedule_hours[current_date] = defaultdict(float)
    for role_name, hours_contributed in effort_done_by_role_today.items():
        if hours_contributed > 0:
            schedule_hours[current_date][role_name] += hours_contributed

def replan_with_resource_leveling(tasks_to_plan: list, roles_config: dict, config: dict):
    working_hours_config = config['working_hours']
    exclude_weekends = config['exclude_weekends']
    project_start_date = config['project_start_date']
    tasks_to_plan.sort(key=lambda t: t['id']) 
    task_end_dates = {} 
    resource_schedule_hours = {} 
    unscheduled_task_ids = [t['id'] for t in tasks_to_plan]
    task_map = {t['id']: t for t in tasks_to_plan}
    logging.info(f"Starting resource leveling. Project Start Default: {project_start_date}")
    MAX_ITERATIONS_SCHEDULING = len(tasks_to_plan) * 3 # Increased iterations
    current_scheduling_iteration = 0
    st.session_state.leveled_resource_schedule = {} # Clear previous leveled schedule

    while unscheduled_task_ids and current_scheduling_iteration < MAX_ITERATIONS_SCHEDULING:
        current_scheduling_iteration += 1
        scheduled_one_in_outer_loop = False
        for task_id_to_attempt in list(unscheduled_task_ids): 
            if task_id_to_attempt not in task_map: continue
            task = task_map[task_id_to_attempt]
            dependencies = parse_dependencies(task.get('dependencies', '[]'))
            if not all(dep_id in task_end_dates for dep_id in dependencies):
                continue 
            effort_ph_total = task.get('effort_ph', 1.0)
            assignments = parse_assignments(task.get('assignments', []))
            if effort_ph_total <= 0 or not assignments : 
                task['start_date'] = project_start_date
                task['end_date'] = project_start_date 
                task_end_dates[task_id_to_attempt] = project_start_date
                if task_id_to_attempt in unscheduled_task_ids: unscheduled_task_ids.remove(task_id_to_attempt)
                scheduled_one_in_outer_loop = True
                continue
            earliest_start_based_on_deps = calculate_dependent_start_date_for_scheduling(
                json.dumps(dependencies), task_end_dates, project_start_date, working_hours_config, exclude_weekends
            )
            if earliest_start_based_on_deps is None: 
                logging.error(f"Iter {current_scheduling_iteration}: Cannot determine dependency start for T{task_id_to_attempt}. Critical error.")
                continue 
            current_task_start_date_search = earliest_start_based_on_deps
            remaining_effort_for_task = float(effort_ph_total)
            actual_task_start_date = None
            actual_task_end_date = None
            MAX_DAYS_TO_SCHEDULE_ONE_TASK = 365 * 2 
            days_searched_for_this_task = 0
            temp_task_schedule_details = [] 
            logging.debug(f"Iter {current_scheduling_iteration}: Attempting T{task_id_to_attempt} ('{task['name']}'). Effort: {effort_ph_total} PH. DepStart: {earliest_start_based_on_deps}")
            while remaining_effort_for_task > 1e-6 and days_searched_for_this_task < MAX_DAYS_TO_SCHEDULE_ONE_TASK:
                days_searched_for_this_task +=1
                can_work_on_this_date, effort_capacity_by_role_today = check_and_get_daily_effort_capacity(
                    current_task_start_date_search, assignments, resource_schedule_hours, roles_config, working_hours_config, exclude_weekends
                )
                if can_work_on_this_date:
                    effort_to_do_today_total_potential = sum(effort_capacity_by_role_today.values())
                    actual_effort_done_this_date_total = min(remaining_effort_for_task, effort_to_do_today_total_potential)
                    if actual_effort_done_this_date_total > 1e-6 :
                        if actual_task_start_date is None:
                            actual_task_start_date = current_task_start_date_search
                        effort_done_by_role_on_this_date_map = defaultdict(float)
                        if effort_to_do_today_total_potential > 1e-6: 
                            for role_name, role_potential_contrib_today in effort_capacity_by_role_today.items():
                                if role_potential_contrib_today > 1e-6:
                                    share_of_effort = role_potential_contrib_today / effort_to_do_today_total_potential
                                    effort_this_role_does = actual_effort_done_this_date_total * share_of_effort
                                    effort_done_by_role_on_this_date_map[role_name] = effort_this_role_does
                        temp_task_schedule_details.append({'date': current_task_start_date_search, 'effort_by_role': dict(effort_done_by_role_on_this_date_map), 'total_effort_day': actual_effort_done_this_date_total})
                        remaining_effort_for_task -= actual_effort_done_this_date_total
                        actual_task_end_date = current_task_start_date_search 
                if remaining_effort_for_task <= 1e-6:
                    break 
                current_task_start_date_search = get_next_working_day(current_task_start_date_search + datetime.timedelta(days=1), working_hours_config, exclude_weekends)
            if remaining_effort_for_task <= 1e-6 and actual_task_start_date and actual_task_end_date:
                task['start_date'] = actual_task_start_date
                task['end_date'] = actual_task_end_date 
                task_end_dates[task_id_to_attempt] = actual_task_end_date
                for daily_detail in temp_task_schedule_details:
                    update_hourly_schedule_with_effort(daily_detail['date'], daily_detail['effort_by_role'], resource_schedule_hours)
                if task_id_to_attempt in unscheduled_task_ids: unscheduled_task_ids.remove(task_id_to_attempt)
                scheduled_one_in_outer_loop = True
                logging.info(f"Iter {current_scheduling_iteration}: SCHEDULED T{task_id_to_attempt} ('{task['name']}') | Effort: {effort_ph_total:.1f} PH | Start: {actual_task_start_date} | End: {actual_task_end_date}")
            else:
                logging.warning(f"Iter {current_scheduling_iteration}: Could NOT fully schedule T{task_id_to_attempt} ('{task['name']}') within search limit. Remaining effort: {remaining_effort_for_task:.2f} PH.")
        if not scheduled_one_in_outer_loop and unscheduled_task_ids:
            logging.error(f"Resource Leveling: Iteration {current_scheduling_iteration} completed but no new tasks were scheduled. Unscheduled: {unscheduled_task_ids}.")
    if unscheduled_task_ids:
        logging.warning(f"Resource leveling finished with {len(unscheduled_task_ids)} tasks unscheduled: {unscheduled_task_ids}")
        st.warning(f"Replanning finished, but {len(unscheduled_task_ids)} tasks could not be fully scheduled: IDs {unscheduled_task_ids}")
        st.session_state.leveled_resource_schedule = {} # Clear if not fully successful
        for failed_id in unscheduled_task_ids: 
            if failed_id in task_map: task_map[failed_id]['status'] = "Pending (Leveling Error)"
    else:
        logging.info("Resource leveling replan completed successfully.")
        st.success("Project dates recalculated successfully using resource leveling.")
        st.session_state.leveled_resource_schedule = resource_schedule_hours # Store the successful schedule
    
    final_replan_tasks = []
    for original_task_in_session in st.session_state.tasks: # Ensure all tasks from session are processed
        task_id_orig = original_task_in_session['id']
        if task_id_orig in task_map: 
            final_replan_tasks.append(task_map[task_id_orig]) 
        else: 
            final_replan_tasks.append(original_task_in_session) 
    st.session_state.tasks = final_replan_tasks


# --- MAIN INTERFACE WITH TABS ---
st.title("🚀 Advanced Project Planner (Effort-Based)")
tab_tasks, tab_gantt, tab_deps, tab_resources, tab_costs, tab_config = st.tabs([
    "📝 Tasks", "📊 Gantt", "🔗 Dependencies", "👥 Resources", "💰 Costs", "⚙️ Settings/Data"
])

# --- Settings and Data Tab ---
with tab_config:
    st.header("⚙️ General Settings and Data Management")
    st.subheader("🚀 Project Actions")
    col_new, col_load_template = st.columns(2)
    with col_new:
        if st.button("✨ Create New Empty Project", help="Deletes all current tasks and roles."):
            if 'confirm_new' not in st.session_state or not st.session_state.confirm_new:
                st.session_state.confirm_new = True; st.warning("Are you sure? All data will be deleted. Press again to confirm.")
            else:
                st.session_state.tasks = []; st.session_state.roles = {}; st.session_state.macrotasks = {}
                st.session_state.last_macro = None; st.session_state.next_task_id = 1
                st.session_state.leveled_resource_schedule = {} # Clear leveled schedule
                st.session_state.config = { 
                    'project_start_date': datetime.date.today(), 'exclude_weekends': True,
                    'working_hours': {'default': {"Monday": 9.0, "Tuesday": 9.0, "Wednesday": 9.0,"Thursday": 9.0, "Friday": 7.0, "Saturday": 0.0, "Sunday": 0.0}, 'monthly_overrides': {}},
                    'profit_margin_percent': 0.0}
                st.success("Empty project created."); del st.session_state.confirm_new; st.rerun()
    with col_load_template:
        if st.button("📋 Load AI Template (Effort-Based)", help="Loads a sample template, replacing current data."):
            if 'confirm_load' not in st.session_state or not st.session_state.confirm_load:
                st.session_state.confirm_load = True; st.warning("Are you sure? Current data will be replaced. Press again to confirm.")
            else:
                template_roles, template_tasks, template_next_id = get_ai_template_data()
                if template_tasks: 
                    st.session_state.roles = template_roles; st.session_state.tasks = template_tasks 
                    st.session_state.next_task_id = template_next_id
                    st.session_state.leveled_resource_schedule = {} # Clear any old leveled schedule
                    st.success("AI Effort-Based template loaded.")
                else: st.error("Failed to load AI template.")
                del st.session_state.confirm_load; st.rerun()
    st.divider()
    st.subheader("🔧 General Project Settings")
    config_changed_flag_settings = False 
    current_start_date_cfg = st.session_state.config.get('project_start_date', datetime.date.today())
    new_start_date_cfg = st.date_input("Default Project Start Date", value=current_start_date_cfg, key="project_start_date_config")
    if new_start_date_cfg != current_start_date_cfg:
        st.session_state.config['project_start_date'] = new_start_date_cfg; config_changed_flag_settings = True
    exclude_weekends_current_cfg = st.session_state.config.get('exclude_weekends', True)
    exclude_weekends_new_cfg = st.checkbox("Exclude Saturdays and Sundays", value=exclude_weekends_current_cfg, key="exclude_weekends_toggle")
    if exclude_weekends_new_cfg != exclude_weekends_current_cfg:
        st.session_state.config['exclude_weekends'] = exclude_weekends_new_cfg; config_changed_flag_settings = True
    if config_changed_flag_settings:
        st.success("General project settings updated."); st.rerun()
    st.divider()
    st.subheader("👥 Role Management")
    roles_col1, roles_col2 = st.columns([0.4, 0.6])
    with roles_col1:
        with st.form("role_form_config_v2"):
            role_name = st.text_input("Role Name")
            role_rate = st.number_input("Hourly Rate (€/hour)", min_value=0.0, format="%.2f")
            role_availability = st.number_input("Availability (%)", 0.0, 100.0, 100.0, 1.0, help="Max % of daily working hours this role can be allocated.")
            if st.form_submit_button("Add/Update Role"):
                if role_name.strip():
                    st.session_state.roles[role_name.strip()] = {"availability_percent": role_availability, "rate_eur_hr": role_rate}
                    st.success(f"Role '{role_name.strip()}' added/updated."); st.rerun()
                else: st.error("Role name empty.")
        role_to_delete = st.selectbox("Delete Role", [""] + sorted(list(st.session_state.roles.keys())), key="delete_role_select_config_v2")
        if st.button("Delete Selected Role", key="delete_role_btn_config_v2") and role_to_delete:
            if any(a.get('role') == role_to_delete for t in st.session_state.tasks for a in parse_assignments(t.get('assignments', []))):
                st.warning(f"Role '{role_to_delete}' is assigned and cannot be deleted.")
            else: del st.session_state.roles[role_to_delete]; st.success(f"Role '{role_to_delete}' deleted."); st.rerun()
    with roles_col2:
        if st.session_state.roles:
            roles_df = pd.DataFrame([{"Role": n, "Rate (€/h)": d.get("rate_eur_hr",0), "Availability (%)": d.get("availability_percent",100)} for n,d in st.session_state.roles.items()])
            edited_roles_df = st.data_editor(roles_df, key="roles_editor_v2", hide_index=True, use_container_width=True,
                column_config={"Role":st.column_config.TextColumn(disabled=True), "Rate (€/h)":st.column_config.NumberColumn(format="%.2f €"), "Availability (%)":st.column_config.NumberColumn(format="%.1f %%",min_value=0.0,max_value=100.0)})
            if not edited_roles_df.equals(roles_df):
                for _, row in edited_roles_df.iterrows():
                    st.session_state.roles[row["Role"]]["rate_eur_hr"] = row["Rate (€/h)"]
                    st.session_state.roles[row["Role"]]["availability_percent"] = row["Availability (%)"]
                st.success("Roles updated."); st.rerun()
        else: st.info("No roles defined.")
    st.divider()
    with st.expander("➕ Manage Macro Tasks (Phases)", expanded=False):
        st.subheader("Define and Edit Macro Tasks / Phases")
        macro_form_col, macro_table_col = st.columns(2)
        with macro_form_col:
            with st.form("macro_tasks_form_v2", clear_on_submit=True):
                macro_name_new = st.text_input("New Macro Task / Phase Name")
                macro_color_new = st.color_picker("Associated Color", "#ADD8E6", key="macro_color_picker_new_v2")
                if st.form_submit_button("Add New Macro/Phase"):
                    if not macro_name_new.strip(): st.error("Name required.")
                    elif macro_name_new.strip() in st.session_state.macrotasks: st.warning("Already exists.")
                    else: st.session_state.macrotasks[macro_name_new.strip()] = macro_color_new; st.success("Added."); st.rerun()
            macro_to_delete = st.selectbox("Delete Macro Task / Phase", [""] + sorted(list(st.session_state.macrotasks.keys())), key="delete_macro_select_v2")
            if st.button("Delete Selected Macro/Phase", key="delete_macro_btn_v2") and macro_to_delete:
                if any(t.get('macro') == macro_to_delete for t in st.session_state.tasks): st.warning("In use.")
                else: del st.session_state.macrotasks[macro_to_delete]; st.success("Deleted."); st.rerun()
        with macro_table_col:
            if st.session_state.macrotasks:
                macros_df = pd.DataFrame([{"Macro/Phase": n, "Color": c} for n,c in st.session_state.macrotasks.items()])
                edited_macros_df = st.data_editor(macros_df, key="macros_editor_v2", hide_index=True, use_container_width=True, column_config={"Macro/Phase":st.column_config.TextColumn(disabled=True), "Color":st.column_config.TextColumn(help="Hex color")})
                if not edited_macros_df.equals(macros_df):
                    for _, row in edited_macros_df.iterrows():
                        st.session_state.macrotasks[row["Macro/Phase"]] = row["Color"]
                    for i, task in enumerate(st.session_state.tasks): # Update task colors
                        st.session_state.tasks[i]['phase_color'] = st.session_state.macrotasks.get(task['macro'], "#CCCCCC")
                    st.success("Macro colors updated."); st.rerun()
            else: st.info("No macros defined.")
    st.divider()
    st.subheader("🕒 Working Hours Configuration")
    hours_config_changed_flag_v2 = False 
    days_of_week_hrs_v2 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    st.markdown("**Default Schedule**")
    cols_days_default_hrs_v2 = st.columns(len(days_of_week_hrs_v2))
    default_hours_cfg_v2 = st.session_state.config['working_hours'].get('default', {})
    for i_v2_day, day_v2 in enumerate(days_of_week_hrs_v2):
        with cols_days_default_hrs_v2[i_v2_day]:
            current_val_hrs_v2 = default_hours_cfg_v2.get(day_v2, 0.0)
            new_val_hrs_v2 = st.number_input(f"{day_names_en[day_v2][:3]}", 0.0, 24.0, current_val_hrs_v2, 0.5, key=f"working_default_{day_v2}_v2", help=f"Hours for {day_names_en[day_v2]}")
            if new_val_hrs_v2 != current_val_hrs_v2: hours_config_changed_flag_v2 = True; st.session_state.config['working_hours']['default'][day_v2] = new_val_hrs_v2
    st.markdown("**Specific Monthly Schedules**")
    monthly_overrides_cfg_v2 = st.session_state.config['working_hours'].get('monthly_overrides', {})
    if monthly_overrides_cfg_v2:
        overrides_list_cfg_v2 = []
        for m_key, sched in sorted(monthly_overrides_cfg_v2.items()):
             m_int = int(m_key)
             sched_str = ", ".join([f"{day_names_en[d][:3]}: {h}h" for d,h in sched.items() if h>0]) or "All 0h"
             overrides_list_cfg_v2.append({"Month": month_names_en.get(m_int, f"M {m_int}"), "Schedule": sched_str})
        if overrides_list_cfg_v2: st.table(pd.DataFrame(overrides_list_cfg_v2))
    col_m_sel, col_m_edit = st.columns([0.3,0.7])
    with col_m_sel: sel_m_cfg = st.selectbox("Month to Add/Edit:", [None]+list(range(1,13)), format_func=lambda x: month_names_en[x] if x else "Choose...", key="month_override_select_v2")
    if sel_m_cfg:
        with col_m_edit:
            st.write(f"**Editing {month_names_en[sel_m_cfg]}**")
            cur_m_sched = monthly_overrides_cfg_v2.get(str(sel_m_cfg), {})
            new_m_sched = {}; cols_m_days = st.columns(len(days_of_week_hrs_v2))
            for i_d_m, d_m in enumerate(days_of_week_hrs_v2):
                 with cols_m_days[i_d_m]:
                     def_val = cur_m_sched.get(d_m, default_hours_cfg_v2.get(d_m,0.0))
                     new_m_sched[d_m] = st.number_input(f"{day_names_en[d_m][:3]} ({month_names_en[sel_m_cfg]})", 0.0, 24.0, def_val, 0.5, key=f"work_m_{sel_m_cfg}_{d_m}_v2")
            s_col, d_col, _ = st.columns([0.3,0.4,0.3])
            with s_col:
                if st.button(f"💾 Save {month_names_en[sel_m_cfg]}", key=f"save_m_{sel_m_cfg}_v2"):
                    st.session_state.config['working_hours']['monthly_overrides'][str(sel_m_cfg)] = new_m_sched
                    st.success("Saved."); hours_config_changed_flag_v2=True; st.rerun()
            with d_col:
                if str(sel_m_cfg) in monthly_overrides_cfg_v2 and st.button(f"❌ Delete {month_names_en[sel_m_cfg]}", key=f"del_m_{sel_m_cfg}_v2"):
                    del st.session_state.config['working_hours']['monthly_overrides'][str(sel_m_cfg)]
                    st.success("Deleted."); hours_config_changed_flag_v2=True; st.rerun()
    if hours_config_changed_flag_v2: st.info("Hours config changed. Replan if needed."); st.rerun() # Rerun to reflect changes immediately
    st.divider()
    st.subheader("🔄 Recalculate Plan with Resource Leveling (Effort-Based)")
    st.warning("Reschedules tasks by ID priority, considering dependencies and daily resource capacity (effort PH).")
    if st.button("Replan with Resource Leveling (Effort-Based)", key="replan_leveled_effort_btn"):
        if not st.session_state.tasks: st.info("No tasks.")
        elif not st.session_state.roles: st.error("No roles defined.")
        else:
            tasks_copy_replan = [t.copy() for t in st.session_state.tasks] # Operate on a copy
            logging.info("--- Starting Effort-Based Resource Leveling Replan ---")
            replan_with_resource_leveling(tasks_copy_replan, st.session_state.roles, st.session_state.config)
            # The function now updates st.session_state.tasks directly if successful or partially successful
            logging.info("--- Effort-Based Resource Leveling Replan Finished ---")
            st.rerun() 
    st.divider()
    st.subheader("📈 Profit Margin")
    cur_margin = st.session_state.config.get('profit_margin_percent', 0.0)
    new_margin = st.number_input("Profit Margin (%)", 0.0, value=cur_margin, format="%.2f", key="profit_margin_input_v2")
    if new_margin != cur_margin: st.session_state.config['profit_margin_percent'] = new_margin; st.success("Margin updated."); st.rerun()
    st.divider()
    st.subheader("💾 Project Data Management")
    col_exp, col_imp = st.columns(2)
    with col_exp:
        st.write("**Export Plan**")
        exp_data = {"roles": st.session_state.roles, "tasks": [], "next_task_id": st.session_state.next_task_id, "config": st.session_state.config, "macrotasks": st.session_state.macrotasks }
        for t_exp in st.session_state.tasks:
            tc_exp = t_exp.copy()
            if isinstance(tc_exp.get('start_date'), datetime.date): tc_exp['start_date'] = tc_exp['start_date'].isoformat()
            if isinstance(tc_exp.get('end_date'), datetime.date): tc_exp['end_date'] = tc_exp['end_date'].isoformat()
            tc_exp['assignments'] = parse_assignments(tc_exp.get('assignments', [])) 
            tc_exp['dependencies'] = json.dumps(parse_dependencies(tc_exp.get('dependencies', '[]')))
            exp_data["tasks"].append(tc_exp)
        cfg_exp = json.loads(json.dumps(exp_data["config"], default=str)) # Ensure dates are str
        if 'working_hours' in cfg_exp and 'monthly_overrides' in cfg_exp['working_hours']:
            cfg_exp['working_hours']['monthly_overrides'] = {str(k): v for k,v in cfg_exp['working_hours']['monthly_overrides'].items()}
        exp_data["config"] = cfg_exp
        try:
            json_s = json.dumps(exp_data, indent=2); st.download_button("Download Plan (JSON)", json_s, f"plan_{datetime.date.today()}.json", "application/json", key="download_json_v2")
        except Exception as e: st.error(f"Export error: {e}")
    with col_imp:
        st.write("**Import Plan**")
        up_file = st.file_uploader("Upload JSON", type=["json"], key="upload_json_v2")
        if up_file and st.button("Confirm Import", key="confirm_import_btn_v2"):
            try:
                imp_data = json.load(up_file)
                if all(k in imp_data for k in ["roles", "tasks", "next_task_id", "config"]):
                    imp_tasks = []
                    for td_imp in imp_data["tasks"]:
                        if isinstance(td_imp.get('start_date'), str): td_imp['start_date'] = datetime.date.fromisoformat(td_imp['start_date'])
                        if isinstance(td_imp.get('end_date'), str): td_imp['end_date'] = datetime.date.fromisoformat(td_imp['end_date'])
                        td_imp['effort_ph'] = float(td_imp.get('effort_ph', 0.0))
                        td_imp['duration_calc_days'] = float(td_imp.get('duration_calc_days', td_imp.get('duration',0.0)))
                        td_imp.pop('duration', None) # Remove old field if present
                        td_imp['assignments'] = parse_assignments(td_imp.get('assignments', []))
                        td_imp['dependencies'] = json.dumps(parse_dependencies(td_imp.get('dependencies', '[]')))
                        td_imp.setdefault('name', f"{td_imp.get('macro','No Phase')} - {td_imp.get('subtask','No Subtask')}")
                        imp_tasks.append(td_imp)
                    imp_cfg = imp_data["config"]
                    if isinstance(imp_cfg.get('project_start_date'), str): imp_cfg['project_start_date'] = datetime.date.fromisoformat(imp_cfg['project_start_date'])
                    if 'working_hours' in imp_cfg and 'monthly_overrides' in imp_cfg['working_hours']:
                        imp_cfg['working_hours']['monthly_overrides'] = {int(k):v for k,v in imp_cfg['working_hours']['monthly_overrides'].items() if k.isdigit()} # Convert keys to int
                    
                    st.session_state.roles = imp_data["roles"]; st.session_state.tasks = imp_tasks
                    st.session_state.next_task_id = imp_data["next_task_id"]; st.session_state.config = imp_cfg
                    st.session_state.macrotasks = imp_data.get("macrotasks", {})
                    st.session_state.leveled_resource_schedule = {} # Clear leveled schedule on import
                    # Ensure all necessary defaults after import
                    st.session_state.config.setdefault('project_start_date', datetime.date.today())
                    st.session_state.config.setdefault('exclude_weekends', True)
                    st.session_state.config.setdefault('working_hours', {'default': {"Monday":9,"Tuesday":9,"Wednesday":9,"Thursday":9,"Friday":7,"Saturday":0,"Sunday":0},'monthly_overrides':{}})
                    st.session_state.config['working_hours'].setdefault('default', {"Monday":9,"Tuesday":9,"Wednesday":9,"Thursday":9,"Friday":7,"Saturday":0,"Sunday":0})
                    st.session_state.config['working_hours'].setdefault('monthly_overrides', {})
                    st.session_state.config.setdefault('profit_margin_percent', 0.0)
                    for i, t_final_imp in enumerate(st.session_state.tasks): # Recalc duration_calc_days and phase_color
                        st.session_state.tasks[i]['phase_color'] = st.session_state.macrotasks.get(t_final_imp['macro'], "#CCCCCC")
                        if t_final_imp.get('effort_ph',0) > 0 and t_final_imp.get('duration_calc_days',0) <=0:
                            st.session_state.tasks[i]['duration_calc_days'] = calculate_estimated_duration_from_effort(t_final_imp['effort_ph'],t_final_imp['assignments'],st.session_state.roles,st.session_state.config['working_hours'],st.session_state.config['exclude_weekends'])
                    st.success("Imported."); st.rerun()
                else: st.error("Invalid JSON structure.")
            except Exception as e: st.error(f"Import error: {e}")

# --- Common Data Preparation (Calculations for Display) ---
# (This section remains the same as it prepares data for all tabs)
tasks_list_for_df_v2 = st.session_state.tasks
current_config_v2 = st.session_state.config
current_working_hours_v2 = current_config_v2['working_hours']
current_exclude_weekends_v2 = current_config_v2['exclude_weekends']

if tasks_list_for_df_v2:
     tasks_df_list_copy_v2 = [t.copy() for t in tasks_list_for_df_v2]
     tasks_df_v2 = pd.DataFrame(tasks_df_list_copy_v2)
     tasks_df_v2['effort_ph'] = pd.to_numeric(tasks_df_v2['effort_ph'], errors='coerce').fillna(0.0)
     tasks_df_v2['duration_calc_days'] = pd.to_numeric(tasks_df_v2['duration_calc_days'], errors='coerce').fillna(0.0)
     tasks_df_v2['start_date'] = pd.to_datetime(tasks_df_v2['start_date'], errors='coerce').dt.date
     if 'end_date' not in tasks_df_v2.columns or tasks_df_v2['end_date'].isnull().all():
         tasks_df_v2['end_date'] = tasks_df_v2.apply(
             lambda row: calculate_end_date_from_effort(row['start_date'], row['effort_ph'], row['assignments'], st.session_state.roles, current_working_hours_v2, current_exclude_weekends_v2)
                         if pd.notna(row['start_date']) and row['effort_ph'] > 0 and isinstance(row['start_date'], datetime.date) else pd.NaT, axis=1
         ) # Added check for start_date type
     tasks_df_v2['end_date'] = pd.to_datetime(tasks_df_v2['end_date'], errors='coerce').dt.date
     tasks_df_v2['assignments'] = tasks_df_v2['assignments'].apply(parse_assignments)
     tasks_df_v2['macro'] = tasks_df_v2['macro'].fillna('No Phase').astype(str)
     tasks_df_v2['subtask'] = tasks_df_v2['subtask'].fillna('No Subtask').astype(str)
     tasks_df_v2['name'] = tasks_df_v2['macro'] + " - " + tasks_df_v2['subtask']
     tasks_df_v2['phase_color'] = tasks_df_v2['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))
     tasks_df_v2['cost'] = tasks_df_v2.apply(
         lambda row: calculate_task_cost_by_effort(row['effort_ph'], row['assignments'], st.session_state.roles)
                     if row['effort_ph'] > 0 else 0.0, axis=1
     )
     valid_end_dates_v2 = tasks_df_v2.dropna(subset=['id', 'end_date'])
     task_end_dates_map_v2 = pd.Series(valid_end_dates_v2.end_date.values, index=valid_end_dates_v2.id).to_dict()
else:
     tasks_df_v2 = pd.DataFrame(columns=['id', 'macro', 'subtask', 'phase_color', 'name', 'start_date', 'effort_ph', 'duration_calc_days', 'assignments', 'dependencies', 'status', 'notes', 'end_date', 'cost'])
     task_end_dates_map_v2 = {}


# --- Tasks Tab (Editing and Creation) ---
with tab_tasks:
    # (UI remains the same as previous version)
    st.header("📝 Detailed Task Management (Effort-Based)")
    with st.expander("➕ Add New Task", expanded=False):
        with st.form("new_task_form_v3_effort", clear_on_submit=True):
            st.write("Define the details of the new task:")
            if st.session_state.macrotasks:
                macro_options_v2 = [""] + sorted(list(st.session_state.macrotasks.keys()))
                default_macro_index_v2 = macro_options_v2.index(st.session_state.last_macro) if st.session_state.last_macro in macro_options_v2 else 0
                selected_macro_v2 = st.selectbox("Macro Task / Phase (*)", options=macro_options_v2, index=default_macro_index_v2, help="Select the phase or macro task.", key="new_task_macro_v2")
                phase_color_v2_form = st.session_state.macrotasks.get(selected_macro_v2, "#CCCCCC")
            else:
                selected_macro_v2 = st.text_input("Macro Task / Phase (*)", help="No macro tasks defined. Enter a name.", key="new_task_macro_text_v2")
                phase_color_v2_form = st.color_picker("Color for this Phase", value="#ADD8E6", key="newtask_phase_color_v2")
            subtask_name_v2 = st.text_input("Subtask Name (*)", help="Specific name for this task.", key="new_task_subtask_v2")
            task_name_preview_v2 = f"{selected_macro_v2.strip()} - {subtask_name_v2.strip()}" if selected_macro_v2 and selected_macro_v2.strip() and subtask_name_v2 and subtask_name_v2.strip() else ""
            if task_name_preview_v2: st.caption(f"Full name will be: {task_name_preview_v2}")
            task_effort_ph_input = st.number_input("Effort (Person-Hours) (*)", min_value=0.1, step=0.5, value=8.0, format="%.1f", key="new_task_effort_ph", help="Total estimated person-hours for this task.")
            default_new_task_start_date_v2 = st.session_state.config.get('project_start_date', datetime.date.today())
            task_start_date_manual_v2 = st.date_input("Desired Start Date (Manual or Calculated)", value=default_new_task_start_date_v2, key="new_task_start_date_v2")
            dep_options_v2 = {task_dep['id']: f"{task_dep['name']} (ID: {task_dep['id']})" for task_dep in sorted(st.session_state.tasks, key=lambda x: x.get('start_date', datetime.date.min))}
            task_dependencies_ids_v2 = st.multiselect("Dependencies", options=list(dep_options_v2.keys()), format_func=lambda x: dep_options_v2.get(x, f"ID {x}?"), help="Select prerequisite tasks.", key="new_task_deps_v2")
            task_status_v2 = st.selectbox("Initial Status", ["Pending", "In Progress", "Completed", "Blocked"], key="new_task_status_v2")
            task_notes_v2 = st.text_area("Additional Notes", key="new_task_notes_v2")
            st.markdown("--- \n ### Assignments (% allocation of role's available time *to this task*)")
            assignment_data_v2 = {}
            if st.session_state.roles:
                cols_assign_v2 = st.columns(len(st.session_state.roles))
                for i_assign, role_assign in enumerate(sorted(st.session_state.roles.keys())):
                    with cols_assign_v2[i_assign]: assignment_data_v2[role_assign] = st.number_input(f"{role_assign} (% Alloc.)", 0, 100, 0, 5, key=f"newtask_alloc_{role_assign}_v2", help=f"% of {role_assign}'s available time for THIS task.")
            else: st.warning("No roles defined.")
            if st.form_submit_button("✅ Add Task to Plan"):
                final_selected_macro_v2 = selected_macro_v2.strip() if selected_macro_v2 else ""; final_subtask_name_v2 = subtask_name_v2.strip() if subtask_name_v2 else ""
                if not final_selected_macro_v2 or not final_subtask_name_v2 or task_effort_ph_input <= 0: 
                    st.error("Complete required fields (*): Macro, Subtask, Effort (>0).")
                else:
                    task_start_date_v2_calc = task_start_date_manual_v2
                    if task_dependencies_ids_v2:
                        computed_start_date_v2 = calculate_dependent_start_date_for_scheduling(json.dumps(task_dependencies_ids_v2), task_end_dates_map_v2, default_new_task_start_date_v2, st.session_state.config['working_hours'],st.session_state.config['exclude_weekends'])
                        task_start_date_v2_calc = computed_start_date_v2 if computed_start_date_v2 else get_next_working_day(task_start_date_manual_v2, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])
                    else: task_start_date_v2_calc = get_next_working_day(task_start_date_manual_v2, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])
                    new_task_id_v2 = st.session_state.next_task_id; st.session_state.next_task_id += 1
                    st.session_state.last_macro = final_selected_macro_v2
                    new_assignments_v2 = [{'role': r, 'allocation': a} for r,a in assignment_data_v2.items() if a > 0]
                    duration_calc_days_new = calculate_estimated_duration_from_effort(task_effort_ph_input, new_assignments_v2, st.session_state.roles, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])
                    final_phase_color_v2 = st.session_state.macrotasks.get(final_selected_macro_v2, phase_color_v2_form)
                    new_task_entry = {'id': new_task_id_v2, 'macro': final_selected_macro_v2, 'subtask': final_subtask_name_v2, 'phase_color': final_phase_color_v2, 'name': f"{final_selected_macro_v2} - {final_subtask_name_v2}", 'start_date': task_start_date_v2_calc, 'effort_ph': task_effort_ph_input, 'duration_calc_days': duration_calc_days_new, 'assignments': new_assignments_v2, 'dependencies': json.dumps(task_dependencies_ids_v2), 'status': task_status_v2, 'notes': task_notes_v2, 'parent_id': None}
                    st.session_state.tasks.append(new_task_entry)
                    st.success(f"Task '{new_task_entry['name']}' added. Est. duration: {duration_calc_days_new} days."); st.rerun()
    st.divider()
    st.subheader("📋 Task List (Editable)")
    st.caption("Edit Macro, Subtask, Start Date, Effort (PH), Dependencies (JSON IDs), Status, Notes. Duration is estimated. Replan for final dates.")
    if not tasks_df_v2.empty:
        tasks_df_display_v2 = tasks_df_v2.copy()
        if not tasks_df_display_v2.empty:
            tasks_df_display_v2['assignments_display'] = tasks_df_display_v2['assignments'].apply(format_assignments_display)
            tasks_df_display_v2['dependencies_display'] = tasks_df_display_v2['dependencies'].apply(lambda d: format_dependencies_display(d, st.session_state.tasks))
            tasks_df_display_v2['cost_display'] = tasks_df_display_v2['cost'].apply(lambda x: f"€ {x:,.2f}")
            tasks_df_display_v2['end_date_display'] = tasks_df_display_v2['end_date'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) and isinstance(x, datetime.date) else 'N/A (Replan)')
            column_config_tasks_v2 = {
                "id": st.column_config.NumberColumn("ID", disabled=True), "macro": st.column_config.TextColumn("Macro/Phase", required=True),"subtask": st.column_config.TextColumn("Subtask", required=True), 
                "phase_color": None, "name": st.column_config.TextColumn("Full Name", disabled=True, width="large"), 
                "start_date": st.column_config.DateColumn("Start Date", required=True, format="YYYY-MM-DD"), "effort_ph": st.column_config.NumberColumn("Effort (PH)", required=True, min_value=0.1, format="%.1f PH"),
                "duration_calc_days": st.column_config.NumberColumn("Est. Dur. (Days)", disabled=True, format="%.1f d"), "dependencies": st.column_config.TextColumn("Deps (IDs JSON)"),
                "dependencies_display": st.column_config.TextColumn("Deps (Names)", disabled=True), "status": st.column_config.SelectboxColumn("Status", options=["Pending", "In Progress", "Completed", "Blocked", "Pending (Leveling Error)", "Pending (Dep Error?)"]),
                "notes": st.column_config.TextColumn("Notes", width="medium"), "end_date": None, "end_date_display": st.column_config.TextColumn("End Date (Calc.)", disabled=True),
                "cost": None, "cost_display": st.column_config.TextColumn("Cost (€ Calc.)", disabled=True), "assignments": None, "assignments_display": st.column_config.TextColumn("Assignments", disabled=True)}
            cols_to_display_editor_v2 = ['id', 'macro', 'subtask', 'start_date', 'effort_ph', 'duration_calc_days', 'dependencies_display', 'status', 'notes', 'end_date_display', 'cost_display', 'assignments_display', 'dependencies']
            edited_df_tasks_v2 = st.data_editor(tasks_df_display_v2[cols_to_display_editor_v2], column_config=column_config_tasks_v2, key="task_editor_effort_v1", num_rows="dynamic", use_container_width=True, hide_index=True)
            if edited_df_tasks_v2 is not None:
                try:
                    updated_tasks_from_editor_v2 = []; processed_ids_editor_v2 = set(); needs_rerun_editor_v2 = False
                    original_task_map_v2 = {task_edit['id']: task_edit for task_edit in st.session_state.tasks}
                    for i_edit, row_edit in edited_df_tasks_v2.iterrows():
                        task_id_edit = row_edit.get('id'); is_new_row_edit = pd.isna(task_id_edit) or task_id_edit <= 0
                        if is_new_row_edit:
                            task_id_edit = st.session_state.next_task_id; st.session_state.next_task_id += 1
                            original_task_data_edit = {}; current_assignments_edit = []; current_color_edit = st.session_state.macrotasks.get(str(row_edit.get("macro","")).strip() or "No Phase", "#CCCCCC"); current_deps_str_edit = '[]'; needs_rerun_editor_v2 = True
                        else:
                            task_id_edit = int(task_id_edit); original_task_data_edit = original_task_map_v2.get(task_id_edit, {})
                            current_assignments_edit = original_task_data_edit.get('assignments', []); current_color_edit = original_task_data_edit.get('phase_color', '#CCCCCC'); current_deps_str_edit = original_task_data_edit.get('dependencies', '[]')
                        processed_ids_editor_v2.add(task_id_edit)
                        raw_deps_edit = row_edit.get('dependencies'); deps_changed_edit = False
                        if pd.notna(raw_deps_edit) and raw_deps_edit != current_deps_str_edit:
                            try: deps_list_edit = parse_dependencies(raw_deps_edit); deps_str_edit = json.dumps(deps_list_edit)
                            except Exception: deps_str_edit = current_deps_str_edit 
                            if deps_str_edit != current_deps_str_edit: deps_changed_edit = True; needs_rerun_editor_v2 = True
                        else: deps_str_edit = current_deps_str_edit
                        macro_val_edit = str(row_edit.get("macro", "")).strip() or "No Phase"; subtask_val_edit = str(row_edit.get("subtask", "")).strip() or "No Subtask"
                        name_val_edit = f"{macro_val_edit} - {subtask_val_edit}"; phase_color_val_edit = st.session_state.macrotasks.get(macro_val_edit, current_color_edit)
                        start_date_val_edit = pd.to_datetime(row_edit.get('start_date'), errors='coerce').date() if pd.notna(row_edit.get('start_date')) else (original_task_data_edit.get('start_date') or datetime.date.today())
                        effort_ph_val_edit = max(0.1, float(row_edit['effort_ph'])) if pd.notna(row_edit.get('effort_ph')) else (original_task_data_edit.get('effort_ph') or 0.1)
                        status_val_edit = str(row_edit.get('status', original_task_data_edit.get('status', 'Pending'))); notes_val_edit = str(row_edit.get('notes', original_task_data_edit.get('notes', '')))
                        duration_calc_days_val_edit = calculate_estimated_duration_from_effort(effort_ph_val_edit, current_assignments_edit, st.session_state.roles,st.session_state.config['working_hours'], st.session_state.config['exclude_weekends'])
                        task_data_edit_entry = {'id': task_id_edit, 'macro': macro_val_edit, 'subtask': subtask_val_edit,'phase_color': phase_color_val_edit, 'name': name_val_edit,'start_date': start_date_val_edit, 'effort_ph': effort_ph_val_edit,'duration_calc_days': duration_calc_days_val_edit,'assignments': current_assignments_edit, 'dependencies': deps_str_edit,'status': status_val_edit, 'notes': notes_val_edit,'parent_id': original_task_data_edit.get('parent_id')}
                        updated_tasks_from_editor_v2.append(task_data_edit_entry)
                        if not is_new_row_edit and (task_data_edit_entry['macro'] != original_task_data_edit.get('macro') or task_data_edit_entry['subtask'] != original_task_data_edit.get('subtask') or task_data_edit_entry['start_date'] != original_task_data_edit.get('start_date') or task_data_edit_entry['effort_ph'] != original_task_data_edit.get('effort_ph') or task_data_edit_entry['status'] != original_task_data_edit.get('status') or task_data_edit_entry['notes'] != original_task_data_edit.get('notes') or deps_changed_edit):
                            needs_rerun_editor_v2 = True
                    original_ids_editor_v2 = set(original_task_map_v2.keys()); deleted_ids_editor_v2 = original_ids_editor_v2 - processed_ids_editor_v2
                    final_task_list_editor_v2 = updated_tasks_from_editor_v2 
                    if deleted_ids_editor_v2:
                        needs_rerun_editor_v2 = True; final_task_list_editor_v2 = [t for t in updated_tasks_from_editor_v2 if t['id'] not in deleted_ids_editor_v2]
                        deleted_names = [original_task_map_v2.get(d_id,{}).get('name',f'ID {d_id}') for d_id in deleted_ids_editor_v2]; dep_updates = []
                        for t_final in final_task_list_editor_v2:
                            cur_deps_f = parse_dependencies(t_final.get('dependencies','[]')); deps_to_rem_f = set(cur_deps_f) & deleted_ids_editor_v2
                            if deps_to_rem_f: t_final['dependencies'] = json.dumps([d for d in cur_deps_f if d not in deleted_ids_editor_v2]); dep_updates.append(f"'{t_final['name']}': removed {deps_to_rem_f}.")
                        st.success(f"Tasks deleted: {', '.join(deleted_names)}."); 
                        if dep_updates: st.info("Deps updated:\n- " + "\n- ".join(dep_updates))
                    if needs_rerun_editor_v2:
                        if json.dumps(st.session_state.tasks,sort_keys=True,default=str) != json.dumps(final_task_list_editor_v2,sort_keys=True,default=str):
                            st.session_state.tasks = final_task_list_editor_v2; st.success("Changes saved."); st.rerun()
                except Exception as e_editor: st.error(f"Error processing table: {e_editor}")
        else: st.info("No tasks in editor.")
    else: st.info("No tasks in plan.")
    st.divider()
    st.subheader("💼 Edit Role Assignments per Task")
    if not st.session_state.tasks: st.info("Create tasks first.")
    elif not st.session_state.roles: st.warning("No roles defined.")
    else:
        task_opts_assign = {t['id']: f"{t['name']} (Effort: {t.get('effort_ph',0)} PH)" for t in sorted(st.session_state.tasks, key=lambda x:x.get('start_date',datetime.date.min))}
        sel_task_id_assign = st.selectbox("Task to Edit Assignments:", [None]+list(task_opts_assign.keys()), format_func=lambda x:task_opts_assign.get(x,"Choose..."), key="assign_task_selector_v2")
        if sel_task_id_assign:
            task_edit_assign = get_task_by_id(sel_task_id_assign, st.session_state.tasks)
            if task_edit_assign:
                st.write(f"**Editing Assignments for:** {task_edit_assign['name']}")
                cur_assign_edit = parse_assignments(task_edit_assign.get('assignments',[])); cur_alloc_edit = {a['role']:a['allocation'] for a in cur_assign_edit if isinstance(a,dict)}
                new_assign_data_edit = {}; cols_assign_edit = st.columns(len(st.session_state.roles))
                for i_as_form, r_as_form in enumerate(sorted(st.session_state.roles.keys())):
                    with cols_assign_edit[i_as_form]: 
                        def_alloc_f = cur_alloc_edit.get(r_as_form,0)
                        alloc_f = st.number_input(f"{r_as_form} (%)",0,100,int(def_alloc_f),5,key=f"alloc_edit_{sel_task_id_assign}_{r_as_form}")
                        new_assign_data_edit[r_as_form] = alloc_f
                if st.button("💾 Save Assignments", key=f"save_assign_edit_{sel_task_id_assign}"):
                    upd_assign_f = [{'role':r,'allocation':a} for r,a in new_assign_data_edit.items() if a > 0]
                    assign_changed_f = False
                    for i_t_f, t_f_l in enumerate(st.session_state.tasks):
                        if t_f_l['id'] == sel_task_id_assign:
                            if json.dumps(parse_assignments(t_f_l.get('assignments',[])),sort_keys=True) != json.dumps(upd_assign_f,sort_keys=True):
                                st.session_state.tasks[i_t_f]['assignments'] = upd_assign_f
                                st.session_state.tasks[i_t_f]['duration_calc_days'] = calculate_estimated_duration_from_effort(st.session_state.tasks[i_t_f]['effort_ph'],upd_assign_f,st.session_state.roles,st.session_state.config['working_hours'],st.session_state.config['exclude_weekends'])
                                assign_changed_f = True; break
                    if assign_changed_f: st.success(f"Assignments saved for '{task_edit_assign['name']}'. Est. duration updated."); st.rerun()
                    else: st.info("No changes in assignments.")
            else: st.error(f"Task ID {sel_task_id_assign} not found.")

# --- Gantt Tab ---
with tab_gantt:
    # (UI remains the same as previous version)
    st.header("📊 Interactive Gantt Chart (Based on Leveled Plan)")
    if not tasks_df_v2.empty and 'end_date' in tasks_df_v2.columns and tasks_df_v2['start_date'].notna().all() and tasks_df_v2['end_date'].notna().all():
        gantt_df_source_v2 = tasks_df_v2.copy()
        gantt_df_source_v2['effort_ph_display'] = gantt_df_source_v2['effort_ph'].apply(lambda x: f"{x:.1f} PH")
        gantt_df_source_v2['duration_display'] = gantt_df_source_v2.apply(lambda r: f"{(r['end_date'] - r['start_date']).days + 1 if pd.notna(r['start_date']) and pd.notna(r['end_date']) else r['duration_calc_days']:.1f} d", axis=1)
        gantt_df_source_v2['assignments_display'] = gantt_df_source_v2['assignments'].apply(format_assignments_display)
        gantt_df_source_v2['dependencies_display'] = gantt_df_source_v2['dependencies'].apply(lambda d: format_dependencies_display(d, st.session_state.tasks))
        macro_colors_gantt = gantt_df_source_v2.set_index('macro')['phase_color'].to_dict()
        plotly_data_gantt = []
        gantt_wh_config = st.session_state.config['working_hours']; gantt_exclude_w_config = st.session_state.config['exclude_weekends']
        for _, row_gantt in gantt_df_source_v2.iterrows():
             if isinstance(row_gantt['start_date'], datetime.date) and isinstance(row_gantt['end_date'], datetime.date) and row_gantt['start_date'] <= row_gantt['end_date']:
                 segments_gantt = get_working_segments_from_dates(row_gantt['start_date'], row_gantt['end_date'], gantt_exclude_w_config, gantt_wh_config)
                 for seg_start_gantt, seg_end_gantt in segments_gantt:
                      plotly_end_date_gantt = seg_end_gantt + datetime.timedelta(days=1) 
                      new_row_gantt = row_gantt.to_dict()
                      new_row_gantt['plotly_start'] = seg_start_gantt; new_row_gantt['plotly_end'] = plotly_end_date_gantt
                      plotly_data_gantt.append(new_row_gantt)
        if plotly_data_gantt:
             segments_df_gantt = pd.DataFrame(plotly_data_gantt)
             segments_df_gantt['plotly_start'] = pd.to_datetime(segments_df_gantt['plotly_start']); segments_df_gantt['plotly_end'] = pd.to_datetime(segments_df_gantt['plotly_end'])
             segments_df_gantt = segments_df_gantt.sort_values(by='plotly_start')
             fig_gantt = px.timeline(segments_df_gantt, x_start="plotly_start", x_end="plotly_end", y="name", color="macro", color_discrete_map=macro_colors_gantt, title="Project Timeline (Leveled)", hover_name="name",
                 hover_data={"start_date": "|%Y-%m-%d", "end_date": "|%Y-%m-%d", "effort_ph_display": True, "duration_display": True, "assignments_display": True, "dependencies_display": True, "status": True, "cost": ":.2f€", "notes": True, "plotly_start": False, "plotly_end": False, "macro": False, "phase_color": False, "assignments": False, "dependencies": False, "subtask": False}, custom_data=["id"])
             fig_gantt.update_layout(xaxis_title="Date", yaxis_title="Tasks", legend_title_text="Macro/Phase", yaxis=dict(autorange="reversed",tickfont=dict(size=10)), xaxis=dict(type='date',tickformat="%d-%b\n%Y"), title_x=0.5)
             st.plotly_chart(fig_gantt, use_container_width=True)
        else: st.info("No task segments for Gantt. Ensure tasks are scheduled/leveled.")
    elif not tasks_df_v2.empty: st.warning("Missing date data for Gantt. Try replanning.")
    else: st.info("Add tasks for Gantt chart.")

# --- Dependencies Tab ---
with tab_deps:
    # (UI remains the same)
    st.header("🔗 Dependency Visualization (Graph)")
    if not tasks_df_v2.empty:
        try:
            dot_v2 = graphviz.Digraph(comment='Project Dependency Diagram'); dot_v2.attr(rankdir='LR')
            task_list_for_graph_v2 = st.session_state.tasks
            status_colors_graph_v2 = {"Pending": "lightblue", "In Progress": "orange", "Completed": "lightgreen", "Blocked": "lightcoral", "Pending (Leveling Error)": "pink", "Pending (Dep Error?)": "lightgrey"}
            valid_ids_for_graph_v2 = {task_graph['id'] for task_graph in task_list_for_graph_v2}
            for task_graph_item in task_list_for_graph_v2:
                assign_display_graph = format_assignments_display(task_graph_item.get('assignments', []))
                duration_display_graph = f"{task_graph_item.get('duration_calc_days', '?'):.1f}d (est)"
                if 'end_date' in task_graph_item and isinstance(task_graph_item.get('start_date'), datetime.date) and isinstance(task_graph_item.get('end_date'), datetime.date): # Check type before operation
                    actual_duration_graph = (task_graph_item['end_date'] - task_graph_item['start_date']).days + 1
                    duration_display_graph = f"{actual_duration_graph}d (lvl)"
                node_label_graph = f'''<{task_graph_item.get('name', 'Unknown Name')}<BR/><FONT POINT-SIZE="10">ID: {task_graph_item.get('id', '?')}<BR/>Effort: {task_graph_item.get('effort_ph', '?')} PH | Dur: {duration_display_graph}<BR/>Status: {task_graph_item.get('status', 'N/A')}<BR/>Assign: {assign_display_graph}</FONT>>'''
                node_color_graph = status_colors_graph_v2.get(task_graph_item.get('status', 'Pending'), 'lightgrey')
                dot_v2.node(str(task_graph_item['id']), label=node_label_graph, shape='box', style='filled', fillcolor=node_color_graph)
            for task_graph_item_dep in task_list_for_graph_v2:
                dependencies_graph = parse_dependencies(task_graph_item_dep.get('dependencies', '[]'))
                for dep_id_graph in dependencies_graph:
                    if dep_id_graph in valid_ids_for_graph_v2: dot_v2.edge(str(dep_id_graph), str(task_graph_item_dep['id']))
            st.graphviz_chart(dot_v2, use_container_width=True)
        except ImportError: st.error("'graphviz' library not installed/configured."); st.code("pip install graphviz"); st.info("Install Graphviz system-wide: https://graphviz.org/download/")
        except Exception as e_graph: st.error(f"Error generating dependency graph: {e_graph}"); logging.error(f"Dependency graph error: {e_graph}", exc_info=True)
    else: st.info("Add tasks and dependencies to visualize graph.")

# --- Resources Tab ---
with tab_resources:
    st.header("👥 Resource Workload (Based on Leveled Plan)")
    if (not tasks_df_v2.empty and 'end_date' in tasks_df_v2.columns and 
        tasks_df_v2['start_date'].notna().all() and tasks_df_v2['end_date'].notna().all() and
        st.session_state.roles):
        
        min_date_res_tab = tasks_df_v2['start_date'].min()
        max_date_res_tab = tasks_df_v2['end_date'].max()

        if isinstance(min_date_res_tab, datetime.date) and isinstance(max_date_res_tab, datetime.date) and min_date_res_tab <= max_date_res_tab:
            
            # Use the actual leveled schedule if available
            leveled_schedule_data = st.session_state.get('leveled_resource_schedule', {})
            load_data_for_chart = []

            if leveled_schedule_data:
                st.info("Displaying workload from the last resource leveling calculation.")
                for date_val, roles_load_val in leveled_schedule_data.items():
                    for role_val, hours_val in roles_load_val.items():
                        load_data_for_chart.append({'Fecha': pd.to_datetime(date_val), 'Rol': role_val, 'Carga (h)': hours_val})
            else:
                st.warning("No detailed leveled schedule found. Displaying an approximation of daily load. Run 'Replan with Resource Leveling' for accurate data.")
                # Fallback to approximate calculation if no leveled data (same as before, but ensure it's clear this is an estimate)
                for _, task_res_approx in tasks_df_v2.iterrows():
                    task_start_approx = task_res_approx['start_date']
                    task_end_approx = task_res_approx['end_date']
                    assignments_approx = parse_assignments(task_res_approx.get('assignments',[]))
                    task_effort_approx = task_res_approx.get('effort_ph', 0.0)
                    if isinstance(task_start_approx, datetime.date) and isinstance(task_end_approx, datetime.date) and \
                       task_start_approx <= task_end_approx and assignments_approx and task_effort_approx > 0:
                        task_actual_working_days_approx = 0
                        temp_date_approx = task_start_approx
                        while temp_date_approx <= task_end_approx:
                            d_h_approx = get_working_hours_for_date(temp_date_approx, current_working_hours_v2)
                            is_w_approx = temp_date_approx.weekday() < 5
                            if d_h_approx > 0 and not (current_exclude_weekends_v2 and not is_w_approx):
                               task_actual_working_days_approx +=1
                            temp_date_approx += datetime.timedelta(days=1)
                        if task_actual_working_days_approx == 0: continue 
                        approx_effort_per_day_task = task_effort_approx / task_actual_working_days_approx
                        current_d_approx_loop = task_start_approx
                        while current_d_approx_loop <= task_end_approx:
                            daily_sys_h_approx = get_working_hours_for_date(current_d_approx_loop, current_working_hours_v2)
                            is_wknd_approx = current_d_approx_loop.weekday() >= 5
                            if daily_sys_h_approx > 0 and not (current_exclude_weekends_v2 and is_wknd_approx):
                                for assign_approx_loop in assignments_approx:
                                    role_approx_loop = assign_approx_loop['role']
                                    alloc_approx_loop = assign_approx_loop['allocation'] / 100.0
                                    total_task_alloc_sum_approx = sum(a['allocation'] for a in assignments_approx) / 100.0
                                    if total_task_alloc_sum_approx > 1e-6:
                                        role_share_effort_approx = (alloc_approx_loop / total_task_alloc_sum_approx) * approx_effort_per_day_task
                                        load_data_for_chart.append({'Fecha': pd.to_datetime(current_d_approx_loop), 'Rol': role_approx_loop, 'Carga (h)': role_share_effort_approx})
                            current_d_approx_loop += datetime.timedelta(days=1)
            
            if load_data_for_chart:
                load_df_chart = pd.DataFrame(load_data_for_chart)
                load_summary_chart = load_df_chart.groupby(['Fecha', 'Rol'])['Carga (h)'].sum().reset_index()
                load_summary_chart = load_summary_chart.sort_values(by=['Fecha', 'Rol'])

                st.subheader("📈 Daily Workload vs Capacity")
                all_roles_chart = sorted(list(st.session_state.roles.keys()))
                selected_role_chart = st.selectbox("Select Role:", all_roles_chart, index=0 if all_roles_chart else -1, key="res_role_sel")

                if selected_role_chart:
                    role_load_df_chart = load_summary_chart[load_summary_chart['Rol'] == selected_role_chart]
                    dates_range_cap_chart = pd.date_range(min_date_res_tab, max_date_res_tab, freq='D')
                    role_cap_data_chart = []
                    role_info_sel_chart = st.session_state.roles.get(selected_role_chart, {})
                    avail_pct_sel_chart = role_info_sel_chart.get('availability_percent', 100.0)

                    for d_dt_cap_chart in dates_range_cap_chart:
                        d_cap_chart = d_dt_cap_chart.date()
                        daily_total_h_cap = get_working_hours_for_date(d_cap_chart, current_working_hours_v2)
                        is_wknd_cap = d_cap_chart.weekday() >= 5
                        if daily_total_h_cap > 0 and not (current_exclude_weekends_v2 and is_wknd_cap):
                            role_cap_today = daily_total_h_cap * (avail_pct_sel_chart / 100.0)
                            role_cap_data_chart.append({"Fecha": d_dt_cap_chart, "Capacity (h)": role_cap_today})
                    role_cap_df_chart = pd.DataFrame(role_cap_data_chart)

                    fig_role_res = go.Figure()
                    fig_role_res.add_trace(go.Bar(x=role_load_df_chart['Fecha'], y=role_load_df_chart['Carga (h)'], name=f'{selected_role_chart} Load', marker_color='rgba(55, 83, 109, 0.7)'))
                    if not role_cap_df_chart.empty:
                        fig_role_res.add_trace(go.Scatter(x=role_cap_df_chart['Fecha'], y=role_cap_df_chart['Capacity (h)'], mode='lines', name=f'{selected_role_chart} Capacity', line=dict(dash='solid', color='red', width=2)))
                    
                    fig_role_res.update_layout(title=f'Workload vs Capacity for: {selected_role_chart}', xaxis_title="Date", yaxis_title="Working Hours", legend_title_text="Metric", barmode='overlay', title_x=0.5, xaxis=dict(type='date', tickformat="%d-%b\n%Y"))
                    st.plotly_chart(fig_role_res, use_container_width=True)

                    if not role_load_df_chart.empty and not role_cap_df_chart.empty:
                        merged_df_role_chart = pd.merge(role_load_df_chart, role_cap_df_chart, on="Fecha", how="left") # Use left merge to keep all load points
                        merged_df_role_chart['Capacity (h)'] = merged_df_role_chart['Capacity (h)'].fillna(0) # Fill NaN capacity for non-working days if any made it through
                        merged_df_role_chart['Overload (h)'] = merged_df_role_chart['Carga (h)'] - merged_df_role_chart['Capacity (h)']
                        overloaded_days_chart = merged_df_role_chart[merged_df_role_chart['Overload (h)'] > 0.01] # Small tolerance
                        if not overloaded_days_chart.empty:
                            st.warning(f"**{selected_role_chart} is overloaded on:**")
                            overloaded_days_display_chart = overloaded_days_chart[['Fecha', 'Carga (h)', 'Capacity (h)', 'Overload (h)']].copy()
                            overloaded_days_display_chart['Fecha'] = overloaded_days_display_chart['Fecha'].dt.strftime('%Y-%m-%d')
                            st.dataframe(overloaded_days_display_chart.style.format({'Carga (h)':'{:.1f}','Capacity (h)':'{:.1f}','Overload (h)':'{:.1f}'}), hide_index=True)
                else: st.info("Select a role.")
                st.divider()
                st.subheader("📊 Total Load Summary (Aggregated Person-Hours)")
                total_h_summary_chart = load_summary_chart.groupby('Rol')['Carga (h)'].sum().reset_index()
                total_h_summary_chart.rename(columns={'Carga (h)': 'Total Hours', 'Rol': 'Role'}, inplace=True)
                st.dataframe(total_h_summary_chart.sort_values(by='Total Hours', ascending=False).style.format({'Total Hours': '{:,.1f} h'}), hide_index=True, use_container_width=True)
            else: st.info("No workload data. Ensure tasks are scheduled with assignments.")
        else: st.warning("Cannot determine project dates for workload. Replan tasks.")
    elif not tasks_df_v2.empty: st.warning("Missing date data for workload. Replan tasks.")
    else: st.info("Add tasks with assignments to visualize workload.")

# --- Costs Tab ---
with tab_costs:
    # (UI remains the same as previous version - already uses effort_ph for cost)
    st.header("💰 Estimated Costs Summary (Based on Total Effort PH)")
    if not tasks_df_v2.empty and 'cost' in tasks_df_v2.columns and tasks_df_v2['cost'].notna().any():
        total_gross_cost_v2 = tasks_df_v2['cost'].sum() 
        profit_margin_percent_v2 = st.session_state.config.get('profit_margin_percent', 0.0)
        profit_amount_v2 = total_gross_cost_v2 * (profit_margin_percent_v2 / 100.0)
        total_selling_price_v2 = total_gross_cost_v2 + profit_amount_v2
        st.subheader("Overall Financial Summary")
        cost_cols_v2 = st.columns(4)
        with cost_cols_v2[0]: st.metric(label="Total Estimated Gross Cost", value=f"€ {total_gross_cost_v2:,.2f}")
        with cost_cols_v2[1]: st.metric(label="Profit Margin", value=f"{profit_margin_percent_v2:.2f} %")
        with cost_cols_v2[2]: st.metric(label="Estimated Profit", value=f"€ {profit_amount_v2:,.2f}")
        with cost_cols_v2[3]: st.metric(label="Estimated Selling Price", value=f"€ {total_selling_price_v2:,.2f}")
        st.divider()
        st.subheader("Cost Breakdown by Role (Based on Total Effort PH Distribution)")
        cost_by_role_data_v2 = []
        for _, task_cost_item in tasks_df_v2.iterrows():
            effort_ph_cost = task_cost_item.get('effort_ph', 0.0)
            assignments_cost = parse_assignments(task_cost_item.get('assignments', []))
            if effort_ph_cost > 0 and assignments_cost:
                total_allocation_pct_for_task_cost = sum(assign_cost.get('allocation', 0) for assign_cost in assignments_cost)
                if total_allocation_pct_for_task_cost > 0: 
                    for assign_cost_item in assignments_cost:
                        role_cost_item = assign_cost_item.get('role')
                        proportion_of_effort_by_role_cost = assign_cost_item.get('allocation', 0) / total_allocation_pct_for_task_cost
                        effort_by_this_role_cost = effort_ph_cost * proportion_of_effort_by_role_cost
                        hourly_rate_cost = get_role_rate(role_cost_item)
                        role_actual_cost = effort_by_this_role_cost * hourly_rate_cost
                        cost_by_role_data_v2.append({'Role': role_cost_item, 'Cost (€)': role_actual_cost})
        if cost_by_role_data_v2:
            cost_by_role_df_v2 = pd.DataFrame(cost_by_role_data_v2)
            cost_by_role_summary_v2 = cost_by_role_df_v2.groupby('Role')['Cost (€)'].sum().reset_index()
            cost_by_role_summary_v2 = cost_by_role_summary_v2.sort_values(by='Cost (€)', ascending=False)
            col_cost_table_v2, col_cost_chart_v2 = st.columns([0.6, 0.4])
            with col_cost_table_v2: 
                st.write("**Total Cost per Role**")
                st.dataframe(cost_by_role_summary_v2.style.format({'Cost (€)': '€ {:,.2f}'}), use_container_width=True, hide_index=True)
            with col_cost_chart_v2:
                if not cost_by_role_summary_v2.empty and cost_by_role_summary_v2['Cost (€)'].sum() > 0:
                    fig_pie_v2 = px.pie(cost_by_role_summary_v2, values='Cost (€)', names='Role', title='Cost Distribution by Role', hole=0.3)
                    fig_pie_v2.update_traces(textposition='inside', textinfo='percent+label')
                    fig_pie_v2.update_layout(showlegend=False, title_x=0.5, margin=dict(l=0, r=0, t=30, b=0))
                    st.plotly_chart(fig_pie_v2, use_container_width=True)
                else: st.info("No positive costs for chart.")
        else: st.info("Could not calculate cost breakdown by role.")
        st.divider()
        st.subheader("Cost Breakdown by Task (Based on Total Effort PH)")
        cost_by_task_df_v2 = tasks_df_v2[['id', 'macro', 'subtask', 'cost']].copy() 
        cost_by_task_df_v2.rename(columns={'cost': 'Estimated Cost (€)', 'macro': 'Macro/Phase', 'subtask':'Subtask'}, inplace=True)
        filter_col1_v2, filter_col2_v2 = st.columns(2)
        with filter_col1_v2: 
            unique_macros_v2 = sorted(cost_by_task_df_v2['Macro/Phase'].unique())
            selected_macros_v2 = st.multiselect("Filter by Macro/Phase:", options=unique_macros_v2, default=[], key="filter_macro_cost_v2")
        with filter_col2_v2: 
            unique_subtasks_v2 = sorted(cost_by_task_df_v2['Subtask'].unique())
            selected_subtasks_v2 = st.multiselect("Filter by Subtask:", options=unique_subtasks_v2, default=[], key="filter_subtask_cost_v2")
        filtered_cost_df_v2 = cost_by_task_df_v2.copy()
        if selected_macros_v2: filtered_cost_df_v2 = filtered_cost_df_v2[filtered_cost_df_v2['Macro/Phase'].isin(selected_macros_v2)]
        if selected_subtasks_v2: filtered_cost_df_v2 = filtered_cost_df_v2[filtered_cost_df_v2['Subtask'].isin(selected_subtasks_v2)]
        filtered_cost_df_v2 = filtered_cost_df_v2.sort_values(by='Estimated Cost (€)', ascending=False)
        st.dataframe(filtered_cost_df_v2[['Macro/Phase', 'Subtask', 'Estimated Cost (€)']].style.format({'Estimated Cost (€)': '€ {:,.2f}'}), use_container_width=True, hide_index=True)
        total_filtered_cost_v2 = filtered_cost_df_v2['Estimated Cost (€)'].sum()
        st.info(f"**Total Cost of Filtered Tasks:** € {total_filtered_cost_v2:,.2f}")
    elif not tasks_df_v2.empty: st.warning("Could not calculate costs. Ensure tasks have effort and assignments.")
    else: st.info("Add tasks with effort and assignments, and define role rates to see costs.")

