import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import datetime
import json
import graphviz  # Para el gráfico de dependencias
from collections import defaultdict
import numpy as np  # Para cálculo de días laborables
import logging  # Para depuración
import math

# Configurar logging básico
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuración Inicial y Estado de Sesión ---
st.set_page_config(layout="wide", page_title="Planificador Avanzado de Proyectos")

# Inicialización en session_state.
# Se utiliza 'working_hours' en vez de un valor único de horas
if 'config' not in st.session_state:
    st.session_state.config = {
        'exclude_weekends': True,
        'working_hours': {
            "Monday": 8.0,
            "Tuesday": 8.0,
            "Wednesday": 8.0,
            "Thursday": 8.0,
            "Friday": 8.0,
            "Saturday": 0.0,
            "Sunday": 0.0
        }
    }
st.session_state.config.setdefault('exclude_weekends', True)
if 'working_hours' not in st.session_state.config:
    st.session_state.config['working_hours'] = {
        "Monday": 8.0,
        "Tuesday": 8.0,
        "Wednesday": 8.0,
        "Thursday": 8.0,
        "Friday": 8.0,
        "Saturday": 0.0,
        "Sunday": 0.0
    }

# Los roles ahora se guardarán como diccionarios con dos claves:
# "availability_percent" (por defecto se usará 100) y "rate_eur_hr".
if 'roles' not in st.session_state:
    st.session_state.roles = {}
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'next_task_id' not in st.session_state:
    st.session_state.next_task_id = 1
if 'macrotasks' not in st.session_state:
    st.session_state.macrotasks = {}
if 'last_macro' not in st.session_state:
    st.session_state.last_macro = None

# Aseguramos consistencia en la estructura de cada tarea
for task in st.session_state.tasks:
    if isinstance(task.get('dependencies'), list):
        task['dependencies'] = json.dumps(task['dependencies'])
    elif not isinstance(task.get('dependencies'), str):
        task['dependencies'] = '[]'
    if 'assignments' not in task:
        task['assignments'] = []
    elif isinstance(task.get('assignments'), str):
        task['assignments'] = []
    elif not isinstance(task.get('assignments'), list):
        task['assignments'] = []

# --- FUNCIONES AUXILIARES ---
def calculate_end_date(start_date, duration_days, exclude_weekends=True):
    if not isinstance(start_date, datetime.date) or not isinstance(duration_days, (int, float)) or duration_days <= 0:
        return None
    duration_int = int(duration_days)
    if duration_int <= 0:
        return start_date
    try:
        if exclude_weekends:
            return np.busday_offset(np.datetime64(start_date), duration_int - 1, roll='forward').astype(datetime.date)
        else:
            return start_date + datetime.timedelta(days=duration_int - 1)
    except Exception as e:
        logging.error(f"Numpy error: {e}. Falling back.")
        return start_date + datetime.timedelta(days=duration_int - 1)

def get_task_by_id(task_id, task_list):
    for task in task_list:
        if task['id'] == task_id:
            return task
    return None

# --- MODIFICACIÓN CRUCIAL ---
def get_role_rate(role_name):
    # En la nueva estructura, se espera que st.session_state.roles[role_name] sea un diccionario.
    role = st.session_state.roles.get(role_name, {})
    # Se extrae la tarifa horaria.
    return role.get("rate_eur_hr", 0)

def parse_assignments(assign_input):
    if isinstance(assign_input, list):
        valid_assignments = []
        for assign in assign_input:
            if isinstance(assign, dict) and 'role' in assign and 'allocation' in assign:
                try:
                    assign['allocation'] = float(assign['allocation'])
                    if 0 <= assign['allocation'] <= 100:
                        valid_assignments.append(assign)
                except (ValueError, TypeError):
                    continue
        return valid_assignments
    elif isinstance(assign_input, str) and assign_input.strip():
        try:
            assignments = json.loads(assign_input)
            return parse_assignments(assignments)
        except (json.JSONDecodeError, TypeError):
            logging.warning(f"Could not parse assignments string: {assign_input}")
            return []
    return []

# Nueva función para calcular total de horas de trabajo según configuración diaria.
def compute_task_working_hours(start_date: datetime.date, end_date: datetime.date, working_hours_config: dict) -> float:
    total_hours = 0.0
    current_date = start_date
    while current_date <= end_date:
        day_name = current_date.strftime("%A")
        total_hours += working_hours_config.get(day_name, 8.0)
        current_date += datetime.timedelta(days=1)
    return total_hours

def calculate_task_cost_by_schedule(start_date, end_date, assignments_list, working_hours_config):
    total_task_hours = compute_task_working_hours(start_date, end_date, working_hours_config)
    total_cost = 0
    for assign in assignments_list:
        role = assign['role']
        allocation = assign['allocation']
        hourly_rate = get_role_rate(role)
        role_hours = total_task_hours * (allocation / 100.0)
        total_cost += role_hours * hourly_rate
    return total_cost

def parse_dependencies(dep_str):
    if isinstance(dep_str, list):
        return [int(d) for d in dep_str]
    if not isinstance(dep_str, str) or not dep_str.strip():
        return []
    try:
        deps = json.loads(dep_str)
        if isinstance(deps, list):
            return [int(d) for d in deps if isinstance(d, (int, str)) and str(d).isdigit()]
        return []
    except:
        return []

def get_task_name(task_id, task_list):
    task = get_task_by_id(task_id, task_list)
    return task['name'] if task else f"ID {task_id}?"

def format_dependencies_display(dep_str, task_list):
    dep_list = parse_dependencies(dep_str)
    return ", ".join([get_task_name(dep_id, task_list) for dep_id in dep_list]) if dep_list else "Ninguna"

def format_assignments_display(assignments_list):
    if not assignments_list:
        return "Ninguno"
    if isinstance(assignments_list, str):
        assignments_list = parse_assignments(assignments_list)
        if not isinstance(assignments_list, list):
            return "Error Formato"
    return ", ".join([f"{a.get('role','?')} ({a.get('allocation',0):.0f}%)" for a in assignments_list])

def calculate_dependent_start_date(dependencies_str, task_list, task_end_dates_map, default_start_date):
    dep_ids = parse_dependencies(dependencies_str)
    if not dep_ids:
        return default_start_date
    latest_dependency_end = None
    all_deps_found = True
    for dep_id in dep_ids:
        dep_end_date = task_end_dates_map.get(dep_id)
        if dep_end_date is None or pd.isna(dep_end_date):
            all_deps_found = False
            break
        if latest_dependency_end is None or dep_end_date > latest_dependency_end:
            latest_dependency_end = dep_end_date
    if not all_deps_found or latest_dependency_end is None:
        return default_start_date
    candidate = latest_dependency_end + datetime.timedelta(days=1)
    if st.session_state.config.get('exclude_weekends', True):
        while candidate.weekday() >= 5:
            candidate += datetime.timedelta(days=1)
    return candidate

def compute_auto_start_date(dep_ids, tasks_list):
    latest_end = None
    for dep_id in dep_ids:
        task = get_task_by_id(dep_id, tasks_list)
        if task is not None and 'start_date' in task and 'duration' in task:
            dep_end = calculate_end_date(task['start_date'], task['duration'], st.session_state.config.get('exclude_weekends', True))
            if dep_end is not None:
                if latest_end is None or dep_end > latest_end:
                    latest_end = dep_end
    if latest_end is None:
        return None
    candidate = latest_end + datetime.timedelta(days=1)
    if st.session_state.config.get('exclude_weekends', True):
        while candidate.weekday() >= 5:
            candidate += datetime.timedelta(days=1)
    return candidate

def get_working_segments(start_date: datetime.date, duration: int) -> list:
    segments = []
    remaining = duration
    current_start = start_date
    while remaining > 0:
        if current_start.weekday() >= 5:
            current_start += datetime.timedelta(days=(7 - current_start.weekday()))
        available_this_week = 5 - current_start.weekday()
        seg_length = min(remaining, available_this_week)
        segment_end = np.busday_offset(np.datetime64(current_start), seg_length - 1, roll='forward').astype(datetime.date)
        segments.append((current_start, segment_end))
        remaining -= seg_length
        next_day = segment_end + datetime.timedelta(days=1)
        if next_day.weekday() >= 5:
            next_day += datetime.timedelta(days=(7 - next_day.weekday()))
        current_start = next_day
    return segments

def get_ai_template_data():
    today = datetime.date.today()
    roles = {'Lider Tecnico': {"availability_percent": 100.0, "rate_eur_hr": 40.0},
             'Ingeniero IA': {"availability_percent": 100.0, "rate_eur_hr": 30.0}}
    tasks_structure = [
        {
          "id": 100,
          "name": "Kick-off y Planificación Detallada",
          "duration": 5,
          "assignments": [{"role": "Lider Tecnico", "allocation": 100}],
          "dependencies": [],
          "notes": "Alinear equipo, refinar plan."
        },
        {
          "id": 1,
          "name": "Investigación benchmarks/métricas multimodales",
          "duration": 3,
          "assignments": [{"role": "Lider Tecnico", "allocation": 30},
                          {"role": "Ingeniero IA", "allocation": 70}],
          "dependencies": [100],
          "notes": ""
        }
    ]
    tasks = []
    task_end_dates_map = {}
    processed_ids = set()
    exclude_weekends = st.session_state.config.get('exclude_weekends', True)
    task_dict = {task['id']: task for task in tasks_structure}
    ids_to_process = sorted(list(task_dict.keys()))
    max_iterations = len(ids_to_process) * 2
    iterations = 0
    while len(processed_ids) < len(ids_to_process) and iterations < max_iterations:
        processed_in_iteration = False
        for task_id in ids_to_process:
            if task_id in processed_ids:
                continue
            task_data = task_dict[task_id]
            dependencies = parse_dependencies(task_data.get('dependencies', []))
            deps_met = all(dep_id in processed_ids for dep_id in dependencies)
            if deps_met:
                start_date = calculate_dependent_start_date(json.dumps(dependencies), tasks, task_end_dates_map, today)
                if start_date is None:
                    continue
                end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends)
                if end_date is None:
                    end_date = start_date
                final_task = task_data.copy()
                final_task['start_date'] = start_date
                final_task['dependencies'] = json.dumps(dependencies)
                final_task['status'] = 'Pendiente'
                final_task['notes'] = task_data.get('notes', '')
                final_task['parent_id'] = None
                final_task['assignments'] = parse_assignments(task_data.get('assignments', []))
                tasks.append(final_task)
                task_end_dates_map[task_id] = end_date
                processed_ids.add(task_id)
                processed_in_iteration = True
        iterations += 1
        if not processed_in_iteration and len(processed_ids) < len(ids_to_process):
            logging.error(f"Could not resolve dependencies after {iterations} iterations.")
            for task_id in ids_to_process:
                if task_id not in processed_ids:
                    task_data = task_dict[task_id]
                    start_date = today
                    end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends) or start_date
                    final_task = task_data.copy()
                    final_task['start_date'] = start_date
                    final_task['dependencies'] = json.dumps(task_data.get('dependencies', []))
                    final_task['status'] = 'Pendiente (Error Dep?)'
                    final_task['notes'] = task_data.get('notes', '')
                    final_task['parent_id'] = None
                    final_task['assignments'] = parse_assignments(task_data.get('assignments', []))
                    tasks.append(final_task)
                    task_end_dates_map[task_id] = end_date
                    processed_ids.add(task_id)
            break
    next_id = max(task_dict.keys()) + 1 if task_dict else 1
    return roles, tasks, next_id

for task in st.session_state.tasks:
    if isinstance(task.get('dependencies'), list):
        task['dependencies'] = json.dumps(task['dependencies'])
    if 'assignments' not in task:
        task['assignments'] = []
    elif isinstance(task.get('assignments'), str):
        task['assignments'] = parse_assignments(task['assignments'])
    elif not isinstance(task.get('assignments'), list):
        task['assignments'] = []

# --- INTERFAZ PRINCIPAL CON PESTAÑAS ---
st.title("🚀 Planificador Avanzado de Proyectos")
tab_tasks, tab_gantt, tab_deps, tab_resources, tab_costs, tab_config = st.tabs([
    "📝 Tareas", "📊 Gantt", "🔗 Dependencias", "👥 Recursos", "💰 Costes", "⚙️ Configuración/Datos"
])

# --- Pestaña de Configuración y Datos ---
with tab_config:
    st.header("⚙️ Configuración General y Gestión de Datos")
    st.subheader("🚀 Acciones del Proyecto")
    col_new, col_load_template = st.columns(2)
    with col_new:
        if st.button("✨ Crear Nuevo Proyecto Vacío", help="Borra todas las tareas y roles actuales."):
            if 'confirm_new' not in st.session_state or not st.session_state.confirm_new:
                st.session_state.confirm_new = True
            else:
                st.session_state.tasks = []
                st.session_state.roles = {}
                st.session_state.macrotasks = {}
                st.session_state.last_macro = None
                st.session_state.next_task_id = 1
                st.session_state.config = {
                    'exclude_weekends': True,
                    'working_hours': {
                        "Monday": 8.0,
                        "Tuesday": 8.0,
                        "Wednesday": 8.0,
                        "Thursday": 8.0,
                        "Friday": 8.0,
                        "Saturday": 0.0,
                        "Sunday": 0.0
                    }
                }
                st.success("Proyecto vacío creado.")
                del st.session_state.confirm_new
                st.rerun()
    with col_load_template:
        if st.button("📋 Cargar Plantilla IA (2 Roles)", help="Carga la plantilla de ejemplo para IA, reemplazando los datos actuales."):
            if 'confirm_load' not in st.session_state or not st.session_state.confirm_load:
                st.session_state.confirm_load = True
            else:
                logging.info("Loading AI template via button.")
                default_roles, default_tasks, default_next_id = get_ai_template_data()
                st.session_state.roles = default_roles
                st.session_state.tasks = default_tasks
                st.session_state.next_task_id = default_next_id
                st.success("Plantilla IA cargada.")
                del st.session_state.confirm_load
                st.rerun()
    st.divider()
    
    st.subheader("👥 Gestión de Roles")
    roles_col1, roles_col2 = st.columns(2)
    with roles_col1:
        with st.form("role_form_config"):
            role_name = st.text_input("Nombre del Rol")
            role_rate = st.number_input("Tarifa HORARIA (€/hora)", min_value=0.0, step=5.0, format="%.2f")
            role_availability = st.number_input("Disponibilidad (%)", min_value=0.0, max_value=100.0, value=100.0, step=1.0)
            submitted_role = st.form_submit_button("Añadir/Actualizar Rol")
            if submitted_role and role_name:
                st.session_state.roles[role_name] = {"availability_percent": role_availability, "rate_eur_hr": role_rate}
                st.success(f"Rol '{role_name}' añadido/actualizado.")
                st.rerun()
        role_to_delete = st.selectbox("Eliminar Rol", options=[""] + list(st.session_state.roles.keys()), index=0, key="delete_role_select_config")
        if st.button("Eliminar Rol Seleccionado", key="delete_role_btn_config") and role_to_delete:
            role_in_use = False
            for task in st.session_state.tasks:
                assignments = task.get('assignments', [])
                if any(assign['role'] == role_to_delete for assign in assignments):
                    role_in_use = True
                    break
            if role_in_use:
                st.warning(f"Rol '{role_to_delete}' asignado. No se puede eliminar.")
            else:
                del st.session_state.roles[role_to_delete]
                st.success(f"Rol '{role_to_delete}' eliminado.")
                st.rerun()
    with roles_col2:
        st.write("**Roles Actuales:**")
        if st.session_state.roles:
            role_table = [
                (k, v.get("rate_eur_hr", 0), v.get("availability_percent", 100))
                for k, v in st.session_state.roles.items()
            ]
            st.dataframe(pd.DataFrame(role_table, columns=["Rol", "Tarifa Horaria (€/h)", "Disponibilidad (%)"]), use_container_width=True)
        else:
            st.info("No hay roles definidos.")

    st.divider()
    
    with st.expander("➕ Gestionar Tareas Macro", expanded=False):
         st.subheader("Definir Tareas Macro")
         with st.form("macro_tasks_form", clear_on_submit=True):
              macro_name = st.text_input("Nombre Tarea Macro")
              macro_color = st.color_picker("Color Asociado", value="#ADD8E6", key="macro_color_picker")
              submitted_macro = st.form_submit_button("Agregar Macro")
              if submitted_macro:
                  if not macro_name:
                      st.error("El nombre de la tarea macro es obligatorio.")
                  else:
                      st.session_state.macrotasks[macro_name] = macro_color
                      st.success(f"Tarea Macro '{macro_name}' agregada.")
                      st.rerun()
         if st.session_state.macrotasks:
              st.write("Tareas Macro definidas:")
              st.dataframe(pd.DataFrame(list(st.session_state.macrotasks.items()), columns=["Tarea Macro", "Color"]))
    st.divider()
    
    st.subheader("Configuración de Horas de Trabajo por Día")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for day in days:
        current_val = st.session_state.config['working_hours'].get(day, 8.0)
        new_val = st.number_input(f"Horas de trabajo para {day}:", min_value=0.0, max_value=24.0, value=current_val, step=0.5, key=f"working_{day}")
        st.session_state.config['working_hours'][day] = new_val
    st.divider()
    
    if st.button("Recalcular Fechas del Proyecto", key="recalc_dates_btn"):
        temp_tasks = st.session_state.tasks[:]
        temp_end_dates = {}
        recalc_processed_ids = set()
        recalc_tasks_final = []
        exclude_weekends_recalc = st.session_state.config['exclude_weekends']
        start_date_map = {t['id']: t['start_date'] for t in temp_tasks}
        ids_to_recalc = sorted([t['id'] for t in temp_tasks])
        max_iter_recalc = len(ids_to_recalc) * 2
        iter_recalc = 0
        while len(recalc_processed_ids) < len(ids_to_recalc) and iter_recalc < max_iter_recalc:
            processed_iter = False
            for task_id in ids_to_recalc:
                if task_id in recalc_processed_ids:
                    continue
                task_data = get_task_by_id(task_id, temp_tasks)
                if not task_data:
                    continue
                dependencies = parse_dependencies(task_data.get('dependencies', '[]'))
                deps_met = all(dep_id in recalc_processed_ids for dep_id in dependencies)
                if deps_met:
                    default_start = start_date_map.get(task_id, datetime.date.today())
                    start_date = calculate_dependent_start_date(task_data.get('dependencies', '[]'), recalc_tasks_final, temp_end_dates, default_start)
                    if start_date is None:
                        continue
                    end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends_recalc)
                    if end_date is None:
                        end_date = start_date
                    new_task_data = task_data.copy()
                    new_task_data['start_date'] = start_date
                    recalc_tasks_final.append(new_task_data)
                    temp_end_dates[task_id] = end_date
                    recalc_processed_ids.add(task_id)
                    processed_iter = True
            iter_recalc += 1
            if not processed_iter and len(recalc_processed_ids) < len(ids_to_recalc):
                logging.error("Error recalculating dates.")
                st.error("Error al recalcular.")
                break
        if len(recalc_processed_ids) == len(ids_to_recalc):
            st.session_state.tasks = recalc_tasks_final
            st.success("Fechas recalculadas.")
            st.rerun()
        else:
            st.error("No se pudieron recalcular todas las fechas.")
    
    st.divider()
    st.subheader("💾 Gestión de Datos del Proyecto")
    col_export, col_import = st.columns(2)
    with col_export:
        st.write("**Exportar Plan**")
        export_data = {
            "roles": st.session_state.roles,
            "tasks": [],
            "next_task_id": st.session_state.next_task_id,
            "config": st.session_state.config,
            "macrotasks": st.session_state.macrotasks
        }
        for task in st.session_state.tasks:
            task_copy = task.copy()
            if isinstance(task_copy.get('start_date'), datetime.date):
                task_copy['start_date'] = task_copy['start_date'].isoformat()
            task_copy.pop('end_date', None)
            if isinstance(task_copy.get('assignments'), str):
                task_copy['assignments'] = parse_assignments(task_copy['assignments'])
            export_data["tasks"].append(task_copy)
        try:
            json_str = json.dumps(export_data, indent=2)
            st.download_button(label="Descargar Plan (JSON)", data=json_str, file_name=f"project_plan_{datetime.date.today()}.json", mime="application/json")
        except Exception as e:
            st.error(f"Error al generar JSON: {e}")
    with col_import:
        st.write("**Importar Plan**")
        uploaded_file = st.file_uploader("Cargar archivo JSON", type=["json"])
        if uploaded_file is not None:
            if st.button("Confirmar Importación", key="confirm_import_btn"):
                try:
                    imported_data = json.load(uploaded_file)
                    if "roles" in imported_data and "tasks" in imported_data and "next_task_id" in imported_data:
                        imported_tasks = []
                        for task_data in imported_data["tasks"]:
                            if isinstance(task_data.get('start_date'), str):
                                try:
                                    task_data['start_date'] = datetime.date.fromisoformat(task_data['start_date'])
                                except ValueError:
                                    task_data['start_date'] = datetime.date.today()
                            task_data['assignments'] = parse_assignments(task_data.get('assignments', '[]'))
                            if isinstance(task_data.get('dependencies'), list):
                                task_data['dependencies'] = json.dumps(task_data['dependencies'])
                            elif not isinstance(task_data.get('dependencies'), str):
                                task_data['dependencies'] = '[]'
                            task_data.setdefault('status', 'Pendiente')
                            task_data.setdefault('notes', '')
                            task_data.setdefault('parent_id', None)
                            imported_tasks.append(task_data)
                        st.session_state.roles = imported_data["roles"]
                        st.session_state.tasks = imported_tasks
                        st.session_state.next_task_id = imported_data["next_task_id"]
                        st.session_state.config = imported_data.get("config", {
                            'exclude_weekends': True,
                            'working_hours': st.session_state.config['working_hours']
                        })
                        st.session_state.macrotasks = imported_data.get("macrotasks", {})
                        st.session_state.config.setdefault('exclude_weekends', True)
                        st.session_state.config.setdefault('working_hours', st.session_state.config['working_hours'])
                        st.success("Plan importado.")
                        st.info("Refrescando...")
                        st.rerun()
                    else:
                        st.error("Formato JSON inválido.")
                except Exception as e:
                    st.error(f"Error al importar: {e}")
            else:
                st.info("Archivo seleccionado. Pulsa 'Confirmar Importación'.")
                
# --- Preparación de Datos Común (Cálculos) ---
tasks_df = pd.DataFrame(st.session_state.tasks)
task_end_dates_map = {}
if not tasks_df.empty:
    tasks_df['duration'] = pd.to_numeric(tasks_df['duration'], errors='coerce').fillna(0).astype(int)
    tasks_df['start_date'] = pd.to_datetime(tasks_df['start_date'], errors='coerce').dt.date
    tasks_df['end_date'] = tasks_df.apply(lambda row: calculate_end_date(row['start_date'], row['duration'], st.session_state.config.get('exclude_weekends', True)), axis=1)
    tasks_df['cost'] = tasks_df.apply(lambda row: calculate_task_cost_by_schedule(row['start_date'], row['end_date'], row['assignments'], st.session_state.config['working_hours']), axis=1)
    valid_end_dates = tasks_df.dropna(subset=['id', 'end_date'])
    task_end_dates_map = pd.Series(valid_end_dates.end_date.values, index=valid_end_dates.id).to_dict()
else:
    tasks_df = pd.DataFrame(columns=['id', 'name', 'start_date', 'duration', 'assignments', 'dependencies', 'status', 'notes', 'end_date', 'cost'])

# --- Pestaña de Tareas (Edición y Creación) ---
with tab_tasks:
    st.header("📝 Gestión Detallada de Tareas")
    with st.expander("➕ Añadir Nueva Tarea", expanded=False):
        with st.form("new_task_form_v3_5", clear_on_submit=True):
            if st.session_state.macrotasks:
                macro_options = list(st.session_state.macrotasks.keys())
                default_index = 0
                if st.session_state.last_macro in macro_options:
                    default_index = macro_options.index(st.session_state.last_macro)
                selected_macro = st.selectbox("Tarea Macro (*)", options=macro_options, index=default_index,
                                              help="Selecciona la tarea macro definida")
                phase_color = st.session_state.macrotasks[selected_macro]
            else:
                selected_macro = st.text_input("Tarea Macro (*)", help="No hay tareas macro definidas, ingresa el nombre")
                phase_color = st.color_picker("Color para la Tarea Macro", value="#ADD8E6", key="newtask_phase_color")
            if not selected_macro or selected_macro.strip() == "":
                if st.session_state.last_macro:
                    selected_macro = st.session_state.last_macro
                else:
                    selected_macro = "Sin Fase"
                    phase_color = "#CCCCCC"
            subtask_name = st.text_input("Subtarea (*)", help="Nombre de la subtarea")
            if selected_macro and subtask_name:
                task_name = f"{selected_macro} - {subtask_name}"
            else:
                task_name = selected_macro or subtask_name
            task_start_date = st.date_input("Fecha Inicio (*)", value=datetime.date.today())
            task_duration = st.number_input("Duración (días) (*)", min_value=1, step=1, value=1)
            dep_options = {task['id']: f"{task['name']} (ID: {task['id']})" for task in st.session_state.tasks}
            task_dependencies_ids = st.multiselect("Dependencias (IDs)", options=list(dep_options.keys()), format_func=lambda x: dep_options[x])
            task_status = st.selectbox("Estado", options=["Pendiente", "En Progreso", "Completada", "Bloqueada"], index=0)
            task_notes = st.text_area("Notas")
            st.markdown("### Asignaciones (Definir % de dedicación por rol)")
            assignment_data = {}
            if st.session_state.roles:
                cols = st.columns(len(st.session_state.roles))
                for i, role in enumerate(st.session_state.roles.keys()):
                    with cols[i]:
                        assignment_data[role] = st.number_input(f"{role} (%)", min_value=0, max_value=100, value=0, step=5, key=f"newtask_alloc_{role}")
            else:
                st.info("No hay roles definidos. Define roles en 'Configuración/Datos'.")
            submitted_new_task = st.form_submit_button("Añadir Tarea")
            if submitted_new_task:
                if not selected_macro or not subtask_name or not task_duration:
                    st.error("Completa los campos obligatorios (*).")
                else:
                    if task_dependencies_ids:
                        computed_start_date = compute_auto_start_date(task_dependencies_ids, st.session_state.tasks)
                        if computed_start_date is not None:
                            task_start_date = computed_start_date
                    new_task_id = st.session_state.next_task_id
                    st.session_state.last_macro = selected_macro
                    new_assignments = [{'role': role, 'allocation': alloc} for role, alloc in assignment_data.items() if alloc > 0]
                    new_task = {
                        'id': new_task_id,
                        'macro': selected_macro,
                        'subtask': subtask_name,
                        'phase_color': phase_color,
                        'name': f"{selected_macro} - {subtask_name}",
                        'start_date': task_start_date,
                        'duration': task_duration,
                        'assignments': new_assignments,
                        'dependencies': json.dumps(task_dependencies_ids),
                        'status': task_status,
                        'notes': task_notes,
                        'parent_id': None
                    }
                    st.session_state.tasks.append(new_task)
                    st.session_state.next_task_id += 1
                    st.success(f"Tarea '{new_task['name']}' añadida con asignaciones.")
                    st.rerun()
    st.divider()
    st.subheader("📋 Lista de Tareas")
    if not tasks_df.empty:
        tasks_df['assignments_display'] = tasks_df['assignments'].apply(format_assignments_display)
        column_config_tasks = {
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "macro": st.column_config.TextColumn("Macro", required=True),
            "subtask": st.column_config.TextColumn("Subtarea", required=True),
            "phase_color": st.column_config.TextColumn("Color Macro", required=True),
            "name": st.column_config.TextColumn("Nombre", disabled=True, width="large"),
            "start_date": st.column_config.DateColumn("Fecha Inicio", required=True, format="YYYY-MM-DD"),
            "duration": st.column_config.NumberColumn("Duración (días)", required=True, min_value=1, step=1),
            "dependencies": st.column_config.TextColumn("Dependencias (IDs JSON)", help="Ej: [1, 3]"),
            "status": st.column_config.SelectboxColumn("Estado", options=["Pendiente", "En Progreso", "Completada", "Bloqueada", "Pendiente (Error Dep?)"]),
            "notes": st.column_config.TextColumn("Notas", width="medium"),
            "end_date": st.column_config.DateColumn("Fecha Fin", disabled=True, format="YYYY-MM-DD"),
            "cost": st.column_config.NumberColumn("Coste (€)", disabled=True, format="€ {:,.2f}"),
            "assignments_display": st.column_config.TextColumn("Asignaciones", disabled=True)
        }
        cols_to_display = ['id', 'macro', 'subtask', 'phase_color', 'name', 'start_date', 'duration', 'dependencies', 'status', 'notes', 'end_date', 'cost', 'assignments_display']
        edited_df_tasks = st.data_editor(
            tasks_df[cols_to_display],
            column_config=column_config_tasks,
            key="task_editor_v3_5",
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
        )
        if edited_df_tasks is not None:
            try:
                updated_tasks_from_editor = []
                current_max_id = st.session_state.next_task_id - 1
                processed_ids_editor = set()
                original_assignments = {task['id']: task['assignments'] for task in st.session_state.tasks}
                for i, row in edited_df_tasks.iterrows():
                    task_id = row.get('id')
                    if pd.isna(task_id) or task_id <= 0:
                        task_id = st.session_state.next_task_id
                        st.session_state.next_task_id += 1
                    elif task_id > current_max_id:
                        st.session_state.next_task_id = task_id + 1
                    else:
                        task_id = int(task_id)
                    processed_ids_editor.add(task_id)
                    current_assignments = original_assignments.get(task_id, [])
                    deps_str = '[]'
                    raw_deps = row.get('dependencies')
                    if isinstance(raw_deps, str) and raw_deps.strip():
                        try:
                            deps_list = parse_dependencies(raw_deps)
                            deps_str = json.dumps(deps_list)
                        except Exception as e:
                            original_task = get_task_by_id(task_id, st.session_state.tasks)
                            deps_str = original_task.get('dependencies', '[]') if original_task else '[]'
                    macro_val = row.get("macro")
                    subtask_val = row.get("subtask")
                    if isinstance(macro_val, str) and isinstance(subtask_val, str):
                        name_val = f"{macro_val.strip()} - {subtask_val.strip()}"
                    else:
                        name_val = row.get("name")
                    task_data = {
                        'id': task_id,
                        'macro': macro_val,
                        'subtask': subtask_val,
                        'phase_color': row.get("phase_color"),
                        'name': name_val,
                        'start_date': pd.to_datetime(row['start_date']).date() if pd.notna(row['start_date']) else datetime.date.today(),
                        'duration': int(row['duration']) if pd.notna(row['duration']) and row['duration'] > 0 else 1,
                        'assignments': current_assignments,
                        'dependencies': deps_str,
                        'status': str(row['status']) if pd.notna(row['status']) else 'Pendiente',
                        'notes': str(row['notes']) if pd.notna(row['notes']) else ''
                    }
                    updated_tasks_from_editor.append(task_data)
                original_ids = set(t['id'] for t in st.session_state.tasks)
                deleted_ids = original_ids - processed_ids_editor
                final_task_list = updated_tasks_from_editor
                safe_to_delete = True
                tasks_depending_on_deleted = []
                if deleted_ids:
                    remaining_tasks = [t for t in final_task_list if t['id'] not in deleted_ids]
                    for task in remaining_tasks:
                        task_deps = parse_dependencies(task.get('dependencies', '[]'))
                        for del_id in deleted_ids:
                            if del_id in task_deps:
                                safe_to_delete = False
                                tasks_depending_on_deleted.append(task['name'])
                                break
                        if not safe_to_delete:
                            break
                if not safe_to_delete:
                    st.error(f"No se pueden eliminar tareas (IDs: {deleted_ids}) porque son dependencias de: {', '.join(list(set(tasks_depending_on_deleted)))}.")
                elif list(st.session_state.tasks) != final_task_list:
                    st.session_state.tasks = final_task_list
                    for task in st.session_state.tasks:
                        deps = parse_dependencies(task.get('dependencies', '[]'))
                        valid_deps = [d for d in deps if d not in deleted_ids]
                        task['dependencies'] = json.dumps(valid_deps)
                    st.rerun()
            except Exception as e:
                logging.error(f"Error processing data editor changes: {e}")
                st.error(f"Error al procesar cambios: {e}")
    else:
        st.info("Crea roles y tareas para empezar.")
    st.divider()
    st.subheader("💼 Editar Asignaciones por Tarea")
    if not st.session_state.tasks:
        st.info("Crea alguna tarea primero.")
    elif not st.session_state.roles:
        st.warning("Crea roles en 'Configuración/Datos' para poder asignar tareas.")
    else:
        task_options = {task['id']: f"{task['name']} (ID: {task['id']})" for task in st.session_state.tasks}
        selected_task_id = st.selectbox(
            "Selecciona Tarea para Editar Asignaciones:",
            options=list(task_options.keys()),
            format_func=lambda x: task_options.get(x, "Selecciona..."),
            index=None,
            placeholder="Elige una tarea..."
        )
        if selected_task_id:
            task_to_edit = get_task_by_id(selected_task_id, st.session_state.tasks)
            if task_to_edit:
                st.write(f"**Editando Asignaciones para:** {task_to_edit['name']}")
                current_assignments = task_to_edit.get('assignments', [])
                current_roles_assigned = [a['role'] for a in current_assignments]
                current_allocations = {a['role']: a['allocation'] for a in current_assignments}
                all_roles = list(st.session_state.roles.keys())
                selected_roles = st.multiselect(
                    "Roles Asignados a esta Tarea:",
                    options=all_roles,
                    default=current_roles_assigned,
                    key=f"ms_{selected_task_id}"
                )
                new_assignments_data = {}
                if selected_roles:
                    st.write("**Definir Dedicación (%):**")
                    cols = st.columns(len(selected_roles))
                    for i, role in enumerate(selected_roles):
                        with cols[i]:
                            default_alloc = current_allocations.get(role, 100)
                            allocation = st.number_input(
                                f"{role} (%)",
                                min_value=0,
                                max_value=100,
                                value=int(default_alloc),
                                step=5,
                                key=f"alloc_{selected_task_id}_{role}"
                            )
                            new_assignments_data[role] = allocation
                else:
                    st.info("Selecciona al menos un rol para asignar a la tarea.")
                if st.button("Guardar Asignaciones", key=f"save_assign_{selected_task_id}"):
                    updated_assignments = [{'role': role, 'allocation': alloc} for role, alloc in new_assignments_data.items()]
                    for i, task in enumerate(st.session_state.tasks):
                        if task['id'] == selected_task_id:
                            st.session_state.tasks[i]['assignments'] = updated_assignments
                            break
                    st.success(f"Asignaciones guardadas para la tarea '{task_to_edit['name']}'.")
                    st.rerun()

# --- Pestaña de Gantt ---
with tab_gantt:
    st.header("📊 Diagrama de Gantt Interactivo")
    if not tasks_df.empty and 'end_date' in tasks_df.columns and tasks_df['start_date'].notna().all() and tasks_df['end_date'].notna().all():
        gantt_df = tasks_df.copy()
        if st.session_state.config.get('exclude_weekends', True):
            segments_list = []
            for idx, row in gantt_df.iterrows():
                segments = get_working_segments(row['start_date'], row['duration'])
                for seg in segments:
                    seg_start, seg_end = seg
                    new_row = row.copy()
                    new_row['start_date'] = seg_start
                    new_row['plotly_end'] = seg_end + datetime.timedelta(days=1)
                    segments_list.append(new_row)
            segments_df = pd.DataFrame(segments_list)
        else:
            gantt_df['plotly_end'] = gantt_df['end_date'] + datetime.timedelta(days=1)
            segments_df = gantt_df
        
        if 'macro' not in segments_df.columns:
            segments_df['macro'] = "Sin Fase"
        else:
            segments_df['macro'] = segments_df['macro'].apply(lambda x: x.strip() if isinstance(x, str) and x.strip() else "Sin Fase")
        
        if 'phase_color' not in segments_df.columns:
            segments_df['phase_color'] = "#CCCCCC"
        else:
            segments_df['phase_color'] = segments_df['phase_color'].fillna("#CCCCCC")
        
        segments_df['assignments_display'] = segments_df['assignments'].apply(format_assignments_display)
        macro_colors = segments_df.groupby("macro")["phase_color"].first().to_dict()
        
        fig = px.timeline(
            segments_df,
            x_start="start_date",
            x_end="plotly_end",
            y="subtask",
            color="macro",
            color_discrete_map=macro_colors,
            title="Cronograma del Proyecto",
            hover_name="subtask",
            hover_data={
                "start_date": "|%Y-%m-%d",
                "end_date": "|%Y-%m-%d",
                "duration": True,
                "assignments_display": True,
                "status": True,
                "cost": ":.2f€",
                "dependencies": True,
                "notes": True,
                "plotly_end": False
            },
            custom_data=["id"]
        )
        fig.update_layout(
            xaxis_title="Fecha",
            yaxis_title="Subtareas",
            legend_title_text="Macro",
            yaxis=dict(autorange="reversed"),
            xaxis=dict(tickformat="%d-%b\n%Y"),
            title_x=0.5
        )
        fig.update_yaxes(tickfont=dict(size=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No hay datos válidos para generar el Gantt.")
        
# --- Pestaña de Dependencias ---
with tab_deps:
    st.header("🔗 Visualización de Dependencias")
    if not tasks_df.empty:
        dot = graphviz.Digraph(comment='Diagrama de Dependencias')
        dot.attr(rankdir='LR')
        task_list_for_graph = st.session_state.tasks
        status_colors_graph = {
            "Pendiente": "lightblue",
            "En Progreso": "orange",
            "Completada": "lightgreen",
            "Bloqueada": "lightcoral",
            "Pendiente (Error Dep?)": "lightgrey"
        }
        for task in task_list_for_graph:
            assign_display = format_assignments_display(task.get('assignments', []))
            node_label = f"{task['name']}\n(ID: {task['id']})\nAsig: {assign_display}\nDur: {task.get('duration', '?')}d"
            node_color = status_colors_graph.get(task.get('status', 'Pendiente'), 'lightgrey')
            dot.node(str(task['id']), label=node_label, shape='box', style='filled', fillcolor=node_color)
        valid_ids_for_graph = set(tasks_df['id'])
        for task in task_list_for_graph:
            dependencies = parse_dependencies(task.get('dependencies', '[]'))
            for dep_id in dependencies:
                if dep_id in valid_ids_for_graph:
                    dot.edge(str(dep_id), str(task['id']))
                else:
                    logging.warning(f"Graph Dep Warning: ID {dep_id} not found for edge to {task['id']}")
        try:
            st.graphviz_chart(dot)
        except Exception as e:
            st.error(f"Error al generar gráfico dependencias: {e}")
    else:
        st.info("Añade tareas para ver el gráfico.")
        
# --- Pestaña de Recursos ---
with tab_resources:
    st.header("👥 Carga de Trabajo por Recurso")
    if (not tasks_df.empty and 'end_date' in tasks_df.columns and 
        tasks_df['start_date'].notna().all() and 
        tasks_df['end_date'].notna().all()):
        
        min_date = tasks_df['start_date'].min()
        max_date = tasks_df['end_date'].max()
        
        if pd.notna(min_date) and pd.notna(max_date) and min_date <= max_date:
            load_data = []
            for _, task in tasks_df.iterrows():
                start = task['start_date']
                end = task['end_date']
                assignments = task.get('assignments', [])
                if pd.notna(start) and pd.notna(end) and start <= end and assignments:
                    task_dates = pd.date_range(start, end, freq='D')
                    for date in task_dates:
                        day_name = date.strftime("%A")
                        daily_hours = st.session_state.config['working_hours'].get(day_name, 9.0)
                        if st.session_state.config.get('exclude_weekends', True) and date.weekday() >= 5:
                            continue
                        for assign in assignments:
                            role = assign['role']
                            allocation = assign['allocation']
                            load = daily_hours * (allocation / 100.0)
                            load_data.append({'Fecha': date, 'Rol': role, 'Carga (h)': load})
            
            if load_data:
                load_df = pd.DataFrame(load_data)
                load_summary = load_df.groupby(['Fecha', 'Rol'])['Carga (h)'].sum().reset_index()
                load_summary = load_summary.sort_values(by=['Fecha', 'Rol'])
                
                # Calcular para cada día la capacidad máxima, en función de la configuración
                dates_range = pd.date_range(min_date, max_date, freq='D')
                max_list = []
                for d in dates_range:
                    day_name = d.strftime("%A")
                    max_hours = st.session_state.config['working_hours'].get(day_name, 9.0)
                    max_list.append({"Fecha": d, "Carga (h)": max_hours})
                max_df = pd.DataFrame(max_list)
                
                fig_load = px.bar(
                    load_summary,
                    x='Fecha',
                    y='Carga (h)',
                    color='Rol',
                    title='Carga de Trabajo Estimada por Rol (Horas Diarias)',
                    labels={'Carga (h)': 'Horas de Trabajo'},
                    hover_name='Rol',
                    hover_data={'Fecha': '|%Y-%m-%d', 'Carga (h)': ':.1f'}
                )
                
                fig_load.add_scatter(
                    x=max_df['Fecha'],
                    y=max_df['Carga (h)'],
                    mode='lines',
                    name='Carga Máxima Diaria',
                    line=dict(dash='dash', color='red')
                )
                
                fig_load.update_layout(
                    xaxis_title="Fecha",
                    yaxis_title="Horas de Trabajo",
                    legend_title="Rol / Capacidad",
                    title_x=0.5
                )
                fig_load.update_xaxes(tickformat="%d-%b\n%Y")
                
                st.plotly_chart(fig_load, use_container_width=True)
                
                st.subheader("Resumen Carga Total (Horas-Persona Estimadas)")
                total_hours_data = []
                for _, task in tasks_df.iterrows():
                    assignments = task.get('assignments', [])
                    if task['start_date'] and task['end_date'] and assignments:
                        task_hours = compute_task_working_hours(task['start_date'], task['end_date'], st.session_state.config['working_hours'])
                        for assign in assignments:
                            role = assign['role']
                            allocation = assign['allocation']
                            total_hours_data.append({'Rol': role, 'Horas Estimadas': task_hours * (allocation / 100.0)})
                if total_hours_data:
                    total_hours_df = pd.DataFrame(total_hours_data)
                    total_hours_summary = total_hours_df.groupby('Rol')['Horas Estimadas'].sum().reset_index()
                    total_hours_summary = total_hours_summary.sort_values(by='Horas Estimadas', ascending=False)
                    st.dataframe(total_hours_summary.style.format({'Horas Estimadas': '{:,.1f} h'}), use_container_width=True)
                else:
                    st.info("No se pudieron calcular las horas totales.")
            else:
                st.info("No se pudo calcular la carga.")
        else:
            st.warning("No se pueden determinar fechas inicio/fin.")
    else:
        st.info("Añade tareas válidas para ver carga.")
        
# --- Pestaña de Costes ---
with tab_costs:
    st.header("💰 Resumen de Costes Estimados")
    if not tasks_df.empty and 'cost' in tasks_df.columns:
        total_cost = tasks_df['cost'].sum()
        st.metric(label="Coste Total Estimado", value=f"€ {total_cost:,.2f}")
        st.subheader("Desglose Costes por Rol")
        cost_by_role_data = []
        for _, task in tasks_df.iterrows():
            assignments = task.get('assignments', [])
            if task['start_date'] and task['end_date'] and assignments:
                task_hours = compute_task_working_hours(task['start_date'], task['end_date'], st.session_state.config['working_hours'])
                for assign in assignments:
                    role = assign['role']
                    allocation = assign['allocation']
                    hourly_rate = get_role_rate(role)
                    role_hours = task_hours * (allocation / 100.0)
                    role_cost = role_hours * hourly_rate
                    cost_by_role_data.append({'Rol': role, 'Coste Total (€)': role_cost})
        if cost_by_role_data:
            cost_by_role_df = pd.DataFrame(cost_by_role_data)
            cost_by_role_summary = cost_by_role_df.groupby('Rol')['Coste Total (€)'].sum().reset_index()
            cost_by_role_summary = cost_by_role_summary.sort_values(by='Coste Total (€)', ascending=False)
            col_cost_table, col_cost_chart = st.columns(2)
            with col_cost_table:
                st.dataframe(cost_by_role_summary.style.format({'Coste Total (€)': '€ {:,.2f}'}), use_container_width=True)
            with col_cost_chart:
                if not cost_by_role_summary.empty and cost_by_role_summary['Coste Total (€)'].sum() > 0:
                    fig_pie = px.pie(cost_by_role_summary, values='Coste Total (€)', names='Rol', title='Distribución Coste por Rol', hole=0.3)
                    fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                    fig_pie.update_layout(showlegend=True)
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.info("No hay costes para gráfico.")
        else:
            st.info("No se pudo calcular desglose costes.")
        st.subheader("Desglose de Costes por Tarea")
        cost_by_task = tasks_df[['name', 'cost']].sort_values(by='cost', ascending=False)
        cost_by_task.rename(columns={'name': 'Tarea', 'cost': 'Coste (€)'}, inplace=True)
        st.dataframe(cost_by_task.style.format({'Coste (€)': '€ {:,.2f}'}), use_container_width=True)
    else:
        st.info("Añade tareas para ver costes.")
