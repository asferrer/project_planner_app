# -*- coding: utf-8 -*-
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
# Se añade 'profit_margin_percent' para el margen de beneficio
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
        },
        'profit_margin_percent': 0.0  # Margen de beneficio inicial
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
st.session_state.config.setdefault('profit_margin_percent', 0.0) # Asegurar que existe

# Los roles ahora se guardarán como diccionarios con dos claves:
# "availability_percent" (por defecto se usará 100) y "rate_eur_hr".
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

# Aseguramos consistencia en la estructura de cada tarea al inicio
# Esta validación es importante, especialmente después de importar datos
for task in st.session_state.tasks:
    # Asegura que 'dependencies' sea un string JSON de una lista
    if isinstance(task.get('dependencies'), list):
        task['dependencies'] = json.dumps(task['dependencies'])
    elif not isinstance(task.get('dependencies'), str):
        task['dependencies'] = '[]'
    else: # Si es string, valida que sea JSON de lista
        try:
            parsed_deps = json.loads(task['dependencies'])
            if not isinstance(parsed_deps, list):
                task['dependencies'] = '[]'
        except (json.JSONDecodeError, TypeError):
            task['dependencies'] = '[]'

    # Asegura que 'assignments' sea una lista de diccionarios
    if 'assignments' not in task:
        task['assignments'] = []
    elif isinstance(task.get('assignments'), str):
        try:
            parsed_assign = json.loads(task['assignments'])
            # Valida que el parseo resulte en una lista y que los elementos sean diccionarios con claves esperadas
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
    else: # Si ya es lista, valida el contenido
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


    # Asegura existencia y formato de macro, subtask, name, phase_color
    task.setdefault('macro', 'Sin Fase')
    task.setdefault('subtask', 'Sin Subtarea')
    task.setdefault('phase_color', st.session_state.macrotasks.get(task['macro'], "#CCCCCC"))
    # Reconstruye el nombre por consistencia, manejando posibles None o tipos incorrectos
    task['name'] = f"{str(task.get('macro','Sin Fase')).strip()} - {str(task.get('subtask','Sin Subtarea')).strip()}"


# --- FUNCIONES AUXILIARES ---
def calculate_end_date(start_date, duration_days, exclude_weekends=True):
    """Calcula la fecha de fin basada en la fecha de inicio y duración."""
    if not isinstance(start_date, datetime.date) or not isinstance(duration_days, (int, float)) or duration_days <= 0:
        logging.warning(f"Invalid input for calculate_end_date: start={start_date}, duration={duration_days}")
        return None # Devuelve None si los datos no son válidos
    duration_int = math.ceil(duration_days) # Redondea hacia arriba para asegurar que se incluye el día parcial
    if duration_int <= 0:
        return start_date # Si la duración es 0 o negativa, la fecha fin es la misma que la inicio

    current_date = start_date
    days_added = 0

    if exclude_weekends:
        # Lógica para contar solo días laborables
        temp_date = current_date
        final_date = current_date
        count = 0
        while count < duration_int:
            if temp_date.weekday() < 5: # 0-4 son Lunes a Viernes
                count += 1
                final_date = temp_date # Guarda el último día laborable válido
            # Avanza siempre al siguiente día natural para la comprobación
            temp_date += datetime.timedelta(days=1)
            # Control anti-bucle infinito (muy improbable pero seguro)
            if (temp_date - start_date).days > duration_int * 7 + 14:
                 logging.error(f"Potential infinite loop in calculate_end_date detected for start={start_date}, duration={duration_int}")
                 return start_date # Devuelve start_date como fallback extremo
        return final_date
    else:
        # Cálculo simple si no se excluyen fines de semana
        return start_date + datetime.timedelta(days=duration_int - 1)


def get_task_by_id(task_id, task_list):
    """Obtiene una tarea por su ID de la lista de tareas."""
    try:
        task_id_int = int(task_id) # Asegura que el ID sea entero para la comparación
        for task in task_list:
            if task.get('id') == task_id_int:
                return task
    except (ValueError, TypeError):
         logging.error(f"Invalid task_id type passed to get_task_by_id: {task_id}")
         return None
    return None

def get_role_rate(role_name):
    """Obtiene la tarifa horaria de un rol específico."""
    role = st.session_state.roles.get(role_name, {})
    return role.get("rate_eur_hr", 0) # Devuelve 0 si el rol o la tarifa no existen

def parse_assignments(assign_input):
    """Parsea y valida las asignaciones de roles desde diferentes formatos (lista, JSON string). Devuelve siempre una lista."""
    if isinstance(assign_input, list):
        valid_assignments = []
        for assign in assign_input:
            # Verifica que sea un diccionario con las claves esperadas
            if isinstance(assign, dict) and 'role' in assign and 'allocation' in assign:
                try:
                    # Convierte la asignación a float y valida el rango
                    allocation_val = float(assign['allocation'])
                    if 0 <= allocation_val <= 100:
                        # Crea una nueva copia del diccionario para evitar modificar el original
                        valid_assignments.append({'role': assign['role'], 'allocation': allocation_val})
                    else:
                         logging.warning(f"Invalid allocation value {allocation_val} for role {assign['role']}. Skipping.")
                except (ValueError, TypeError):
                    logging.warning(f"Non-numeric allocation for role {assign['role']}. Skipping.")
                    continue # Ignora asignaciones con formato inválido
            else:
                 logging.warning(f"Invalid assignment item format found: {assign}. Skipping.")
        return valid_assignments
    elif isinstance(assign_input, str) and assign_input.strip():
        try:
            # Intenta cargar desde JSON si es un string no vacío
            assignments = json.loads(assign_input)
            return parse_assignments(assignments) # Llama recursivamente para validar la lista parseada
        except (json.JSONDecodeError, TypeError):
            logging.warning(f"Could not parse assignments string: {assign_input}")
            return [] # Devuelve lista vacía si el JSON es inválido
    # Si no es lista ni string JSON válido, devuelve lista vacía
    if assign_input is not None: # Log solo si no era None
         logging.warning(f"Invalid input type for parse_assignments: {type(assign_input)}. Returning empty list.")
    return []

def compute_task_working_hours(start_date: datetime.date, end_date: datetime.date, working_hours_config: dict, exclude_weekends: bool) -> float:
    """Calcula el total de horas laborables entre dos fechas, según la configuración diaria y exclusión de fines de semana."""
    if not isinstance(start_date, datetime.date) or not isinstance(end_date, datetime.date) or start_date > end_date:
        return 0.0

    total_hours = 0.0
    current_date = start_date
    while current_date <= end_date:
        is_weekend = current_date.weekday() >= 5
        # Si no se excluyen fines de semana O si se excluyen Y no es fin de semana
        if not exclude_weekends or (exclude_weekends and not is_weekend):
             day_name = current_date.strftime("%A") # Obtiene el nombre del día ("Monday", "Tuesday", etc.)
             total_hours += working_hours_config.get(day_name, 0.0) # Suma las horas configuradas para ese día (0 si no está)

        current_date += datetime.timedelta(days=1) # Avanza al siguiente día
    return total_hours


def calculate_task_cost_by_schedule(start_date, end_date, assignments_list, working_hours_config, exclude_weekends):
    """Calcula el coste de una tarea basado en su duración, asignaciones, tarifas y horas laborables."""
    if not isinstance(start_date, datetime.date) or not isinstance(end_date, datetime.date) or start_date > end_date:
        return 0.0 # Coste cero si las fechas son inválidas

    # Calcula las horas totales de la tarea usando la función auxiliar
    total_task_hours = compute_task_working_hours(start_date, end_date, working_hours_config, exclude_weekends)
    total_cost = 0.0

    # Itera sobre cada asignación de rol a la tarea
    valid_assignments = parse_assignments(assignments_list) # Asegura que assignments_list sea una lista válida
    for assign in valid_assignments:
        role = assign.get('role')
        allocation = assign.get('allocation')
        if role and allocation is not None: # Asegura que el rol y la asignación existen
            hourly_rate = get_role_rate(role) # Obtiene la tarifa para ese rol
            # Calcula las horas dedicadas por este rol (proporcional a su asignación)
            role_hours = total_task_hours * (allocation / 100.0)
            # Suma el coste de este rol al coste total de la tarea
            total_cost += role_hours * hourly_rate
    return total_cost


def parse_dependencies(dep_input):
    """Parsea y valida las dependencias desde diferentes formatos (lista, JSON string). Devuelve siempre una lista de enteros."""
    if isinstance(dep_input, list):
        # Si ya es una lista, convierte cada elemento a entero si es posible
        valid_deps = []
        for d in dep_input:
            try:
                valid_deps.append(int(d))
            except (ValueError, TypeError):
                logging.warning(f"Invalid dependency format in list: {d}. Skipping.")
        return valid_deps
    elif isinstance(dep_input, str) and dep_input.strip():
        try:
            # Intenta cargar desde JSON si es un string no vacío
            deps = json.loads(dep_input)
            # Asegurarse de que el resultado del JSON sea una lista
            if isinstance(deps, list):
                 return parse_dependencies(deps) # Llama recursivamente para validar la lista parseada
            else:
                 logging.warning(f"Dependencies string '{dep_input}' did not decode to a list.")
                 return []
        except (json.JSONDecodeError, TypeError):
            logging.warning(f"Could not parse dependencies string: {dep_input}")
            return [] # Devuelve lista vacía si el JSON es inválido
    # Si no es lista ni string JSON válido, devuelve lista vacía
    if dep_input is not None and not isinstance(dep_input, list): # Log solo si no era None ni lista
         logging.warning(f"Invalid input type for parse_dependencies: {type(dep_input)}. Returning empty list.")
    return []

def get_task_name(task_id, task_list):
    """Obtiene el nombre de una tarea por su ID."""
    task = get_task_by_id(task_id, task_list)
    return task.get('name', f"ID {task_id}?") if task else f"ID {task_id}?" # Usa .get con default

def format_dependencies_display(dep_str, task_list):
    """Formatea la lista de dependencias para mostrar nombres en lugar de IDs."""
    dep_list = parse_dependencies(dep_str)
    # Convierte la lista de IDs en una cadena de nombres separados por coma
    return ", ".join([get_task_name(dep_id, task_list) for dep_id in dep_list]) if dep_list else "Ninguna"

def format_assignments_display(assignments_list):
    """Formatea la lista de asignaciones para mostrarla de forma legible."""
    valid_assignments = parse_assignments(assignments_list) # Asegura que sea una lista válida
    if not valid_assignments:
        return "Ninguno"
    # Formatea cada asignación como "Rol (Alloc%)"
    return ", ".join([f"{a.get('role','?')} ({a.get('allocation',0):.0f}%)" for a in valid_assignments])


def calculate_dependent_start_date(dependencies_str, task_list, task_end_dates_map, default_start_date):
    """Calcula la fecha de inicio de una tarea basada en la fecha de fin de sus dependencias."""
    dep_ids = parse_dependencies(dependencies_str)
    if not dep_ids:
        return default_start_date # Si no hay dependencias, usa la fecha por defecto

    latest_dependency_end = None
    all_deps_met = True
    # Encuentra la fecha de fin más tardía entre todas las dependencias
    for dep_id in dep_ids:
        dep_end_date = task_end_dates_map.get(dep_id)
        # Verifica si la fecha de fin existe y es una fecha válida
        if dep_end_date is None or pd.isna(dep_end_date) or not isinstance(dep_end_date, datetime.date):
            # Si alguna dependencia no tiene fecha de fin calculada o válida
            logging.warning(f"Dependency task {dep_id} end date not found or invalid ({dep_end_date}) for calculating start date of dependent task.")
            all_deps_met = False
            break # Sale del bucle si falta una dependencia o su fecha es inválida
        if latest_dependency_end is None or dep_end_date > latest_dependency_end:
            latest_dependency_end = dep_end_date

    if not all_deps_met or latest_dependency_end is None:
        # Si faltan dependencias o no se encontró ninguna fecha válida, usa la fecha por defecto
        logging.warning(f"Could not determine start date based on dependencies {dep_ids}. Using default: {default_start_date}")
        return default_start_date

    # La fecha de inicio candidata es el día siguiente a la última dependencia finalizada
    candidate = latest_dependency_end + datetime.timedelta(days=1)

    # Si se excluyen fines de semana, ajusta la fecha para que no caiga en sábado o domingo
    if st.session_state.config.get('exclude_weekends', True):
        while candidate.weekday() >= 5: # 5 = Sábado, 6 = Domingo
            candidate += datetime.timedelta(days=1)
    return candidate


def compute_auto_start_date(dep_ids, tasks_list):
    """Calcula automáticamente la fecha de inicio basada en las dependencias, recalculando fechas fin si es necesario."""
    latest_end = None
    parsed_dep_ids = parse_dependencies(dep_ids) # Asegura que sea una lista de IDs
    # Itera sobre los IDs de las dependencias
    for dep_id in parsed_dep_ids:
        task = get_task_by_id(dep_id, tasks_list) # Obtiene la tarea dependencia
        if task is not None and isinstance(task.get('start_date'), datetime.date) and isinstance(task.get('duration'), (int, float)):
            # Calcula la fecha de fin de la dependencia
            dep_end = calculate_end_date(task['start_date'], task['duration'], st.session_state.config.get('exclude_weekends', True))
            if dep_end is not None:
                # Actualiza la fecha de fin más tardía encontrada hasta ahora
                if latest_end is None or dep_end > latest_end:
                    latest_end = dep_end
            else:
                 logging.warning(f"Could not calculate end date for dependency task {dep_id} ({task.get('name')}) in compute_auto_start_date.")
                 return None # Falla si no se puede calcular la fecha fin de una dependencia
        else:
             # Si no se encuentra una tarea dependencia o le faltan datos, no se puede calcular
             logging.warning(f"Dependency task {dep_id} not found or missing valid start_date/duration for auto start date calculation.")
             return None # Devuelve None indicando que no se pudo calcular

    if latest_end is None:
        # Si no se encontró ninguna fecha de fin válida (quizás dep_ids estaba vacío o todas fallaron)
        return None

    # La fecha de inicio es el día siguiente a la última dependencia
    candidate = latest_end + datetime.timedelta(days=1)
    # Ajusta si cae en fin de semana y está configurado para excluirlos
    if st.session_state.config.get('exclude_weekends', True):
        while candidate.weekday() >= 5:
            candidate += datetime.timedelta(days=1)
    return candidate


def get_working_segments(start_date: datetime.date, duration: int, exclude_weekends: bool) -> list:
    """Divide una tarea en segmentos de días laborables para el Gantt, excluyendo fines de semana si es necesario."""
    segments = []
    if not isinstance(start_date, datetime.date) or not isinstance(duration, (int, float)) or duration <= 0:
        logging.warning(f"Invalid input for get_working_segments: start={start_date}, duration={duration}")
        return segments # Devuelve lista vacía si los datos son inválidos

    remaining_days = math.ceil(duration) # Redondea hacia arriba
    current_start = start_date

    while remaining_days > 0:
        # Ajusta si la fecha actual cae en fin de semana (si se excluyen)
        if exclude_weekends and current_start.weekday() >= 5:
            current_start += datetime.timedelta(days=7 - current_start.weekday())
            # Si después de ajustar sigue siendo finde (improbable), salimos
            if current_start.weekday() >=5:
                 logging.error(f"Error in get_working_segments: Adjusted start date {current_start} is still a weekend.")
                 break


        segment_end = current_start
        days_in_segment = 0

        # Calcula el fin del segmento actual
        temp_date = current_start
        count = 0
        # Itera mientras queden días por asignar al segmento y no superemos un límite razonable
        while count < remaining_days and (temp_date - current_start).days < remaining_days * 2 + 7:
            is_weekend = temp_date.weekday() >= 5
            if not exclude_weekends or (exclude_weekends and not is_weekend):
                segment_end = temp_date # El último día válido es el fin del segmento
                days_in_segment += 1
                count += 1
            elif exclude_weekends and is_weekend and count > 0:
                 # Si estamos excluyendo findes y llegamos a uno DESPUÉS de empezar el segmento, paramos aquí.
                 break
            # Avanza siempre al siguiente día natural
            temp_date += datetime.timedelta(days=1)


        # Añade el segmento encontrado a la lista
        if days_in_segment > 0:
            segments.append((current_start, segment_end))
            remaining_days -= days_in_segment
            # El inicio del siguiente segmento es el día después del fin del actual
            current_start = segment_end + datetime.timedelta(days=1)
        else:
            # Si no se añadieron días (ej. empezamos en finde y dura 0, o error), salimos para evitar bucle infinito
            if remaining_days > 0: # Log solo si aún quedaban días
                 logging.warning(f"get_working_segments: No days added to segment starting {current_start} with {remaining_days} days remaining. Exiting loop.")
            break


    return segments


def get_ai_template_data():
    """Genera datos de ejemplo para una plantilla de proyecto de IA."""
    today = datetime.date.today()
    # Define roles de ejemplo con sus tarifas
    roles = {'Lider Tecnico': {"availability_percent": 100.0, "rate_eur_hr": 40.0},
             'Ingeniero IA': {"availability_percent": 100.0, "rate_eur_hr": 30.0}}
    # Define una estructura de tareas de ejemplo
    tasks_structure = [
        {
          "id": 100, "macro": "Fase 0", "subtask": "Kick-off y Planificación", "duration": 5,
          "assignments": [{"role": "Lider Tecnico", "allocation": 100}],
          "dependencies": [], "notes": "Alinear equipo, refinar plan."
        },
        {
          "id": 1, "macro": "Fase 1", "subtask": "Investigación benchmarks", "duration": 3,
          "assignments": [{"role": "Lider Tecnico", "allocation": 30}, {"role": "Ingeniero IA", "allocation": 70}],
          "dependencies": [100], "notes": ""
        },
         {
          "id": 2, "macro": "Fase 1", "subtask": "Definir métricas", "duration": 2,
          "assignments": [{"role": "Lider Tecnico", "allocation": 50}, {"role": "Ingeniero IA", "allocation": 50}],
          "dependencies": [1], "notes": "Métricas clave para evaluación"
        },
         {
          "id": 3, "macro": "Fase 2", "subtask": "Desarrollo Modelo Base", "duration": 10,
          "assignments": [{"role": "Ingeniero IA", "allocation": 100}],
          "dependencies": [2], "notes": "Primera versión funcional"
        }
    ]

    tasks = [] # Lista para almacenar las tareas procesadas
    task_end_dates_map = {} # Mapa para guardar las fechas de fin calculadas
    processed_ids = set() # Conjunto para llevar registro de las tareas ya procesadas
    exclude_weekends = st.session_state.config.get('exclude_weekends', True)
    task_dict = {task['id']: task for task in tasks_structure} # Diccionario para acceso rápido por ID
    ids_to_process = sorted(list(task_dict.keys())) # Lista ordenada de IDs a procesar

    # Bucle para procesar tareas asegurando que las dependencias se procesan antes
    max_iterations = len(ids_to_process) * 2 # Límite de iteraciones para evitar bucles infinitos
    iterations = 0
    calculation_ok = True
    while len(processed_ids) < len(ids_to_process) and iterations < max_iterations and calculation_ok:
        processed_in_iteration = False
        for task_id in ids_to_process:
            if task_id in processed_ids:
                continue # Salta si ya se procesó

            task_data = task_dict[task_id]
            dependencies = parse_dependencies(task_data.get('dependencies', []))
            # Verifica si todas las dependencias de esta tarea ya han sido procesadas
            deps_met = all(dep_id in processed_ids for dep_id in dependencies)

            if deps_met:
                # Calcula la fecha de inicio basada en las dependencias
                start_date = calculate_dependent_start_date(json.dumps(dependencies), tasks, task_end_dates_map, today)
                if start_date is None:
                    logging.error(f"Template Load: Could not calculate start date for task {task_id}. Aborting template load.")
                    calculation_ok = False
                    break # Aborta si falla el cálculo de una fecha de inicio

                # Calcula la fecha de fin
                end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends)
                if end_date is None:
                    logging.warning(f"Template Load: Could not calculate end date for task {task_id}. Using start date {start_date}.")
                    end_date = start_date # Si falla el cálculo, usa la fecha de inicio

                # Crea el diccionario final de la tarea con todos los datos
                final_task = task_data.copy()
                final_task['start_date'] = start_date
                final_task['dependencies'] = json.dumps(dependencies) # Guarda dependencias como JSON
                final_task['status'] = 'Pendiente'
                final_task['notes'] = task_data.get('notes', '')
                final_task['parent_id'] = None # Campo no usado actualmente
                # Parsea y valida las asignaciones
                final_task['assignments'] = parse_assignments(task_data.get('assignments', []))
                # Añade el color de la fase (asume que las macros de la plantilla se añadirán también)
                final_task['phase_color'] = st.session_state.macrotasks.get(final_task.get('macro', ''), "#CCCCCC")
                # Genera el nombre completo
                final_task['name'] = f"{final_task.get('macro','Sin Fase')} - {final_task.get('subtask','Sin Subtarea')}"


                tasks.append(final_task) # Añade la tarea procesada a la lista
                task_end_dates_map[task_id] = end_date # Guarda la fecha de fin calculada
                processed_ids.add(task_id) # Marca la tarea como procesada
                processed_in_iteration = True
        # Sale del bucle for si calculation_ok es False
        if not calculation_ok:
            break

        iterations += 1
        # Si en una iteración no se procesó nada pero aún quedan tareas, hay un ciclo o error
        if not processed_in_iteration and len(processed_ids) < len(ids_to_process):
            logging.error(f"Template Load: Could not resolve dependencies after {iterations} iterations. Possible circular dependency or missing task data.")
            calculation_ok = False # Marca como fallido
            # Intenta añadir las tareas restantes con fecha de hoy y marca como error
            for task_id in ids_to_process:
                if task_id not in processed_ids:
                    task_data = task_dict[task_id]
                    start_date = today
                    end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends) or start_date
                    final_task = task_data.copy()
                    final_task['start_date'] = start_date
                    final_task['dependencies'] = json.dumps(task_data.get('dependencies', []))
                    final_task['status'] = 'Pendiente (Error Dep?)' # Estado especial
                    final_task['notes'] = task_data.get('notes', '')
                    final_task['parent_id'] = None
                    final_task['assignments'] = parse_assignments(task_data.get('assignments', []))
                    final_task['phase_color'] = st.session_state.macrotasks.get(final_task.get('macro', ''), "#CCCCCC")
                    final_task['name'] = f"{final_task.get('macro','Sin Fase')} - {final_task.get('subtask','Sin Subtarea')}"

                    tasks.append(final_task)
                    task_end_dates_map[task_id] = end_date
                    processed_ids.add(task_id)
            break # Sale del bucle while después de manejar las tareas restantes

    # Si el cálculo falló, devuelve vacío para no cargar datos parciales
    if not calculation_ok:
         st.error("Error al calcular las fechas de la plantilla. No se cargaron los datos.")
         return {}, [], 1 # Devuelve vacío

    # Calcula el siguiente ID disponible
    next_id = max(task_dict.keys()) + 1 if task_dict else 1
    # Añade las macros de la plantilla al estado si no existen
    for task in tasks_structure:
         macro_name = task.get('macro')
         if macro_name and macro_name not in st.session_state.macrotasks:
              # Asigna un color por defecto si la macro es nueva
              st.session_state.macrotasks[macro_name] = "#ADD8E6" # Azul claro por defecto

    return roles, tasks, next_id


# --- INTERFAZ PRINCIPAL CON PESTAÑAS ---
st.title("🚀 Planificador Avanzado de Proyectos")
tab_tasks, tab_gantt, tab_deps, tab_resources, tab_costs, tab_config = st.tabs([
    "📝 Tareas", "📊 Gantt", "🔗 Dependencias", "👥 Recursos", "💰 Costes", "⚙️ Configuración/Datos"
])

# --- Pestaña de Configuración y Datos ---
with tab_config:
    st.header("⚙️ Configuración General y Gestión de Datos")

    # --- Sección Acciones del Proyecto ---
    st.subheader("🚀 Acciones del Proyecto")
    col_new, col_load_template = st.columns(2)
    with col_new:
        # Botón para crear un proyecto vacío (con confirmación)
        if st.button("✨ Crear Nuevo Proyecto Vacío", help="Borra todas las tareas y roles actuales."):
            # Lógica de confirmación simple usando session_state
            if 'confirm_new' not in st.session_state or not st.session_state.confirm_new:
                st.session_state.confirm_new = True
                st.warning("¿Seguro? Se borrarán todos los datos. Vuelve a pulsar para confirmar.")
            else:
                # Resetea el estado de la sesión a valores iniciales
                st.session_state.tasks = []
                st.session_state.roles = {}
                st.session_state.macrotasks = {}
                st.session_state.last_macro = None
                st.session_state.next_task_id = 1
                # Restablece la configuración por defecto, incluyendo el margen
                st.session_state.config = {
                    'exclude_weekends': True,
                    'working_hours': {
                        "Monday": 8.0, "Tuesday": 8.0, "Wednesday": 8.0,
                        "Thursday": 8.0, "Friday": 8.0, "Saturday": 0.0, "Sunday": 0.0
                    },
                    'profit_margin_percent': 0.0 # Restablece margen
                }
                st.success("Proyecto vacío creado.")
                del st.session_state.confirm_new # Limpia la bandera de confirmación
                st.rerun() # Refresca la app para reflejar los cambios

    with col_load_template:
        # Botón para cargar la plantilla de IA (con confirmación)
        if st.button("📋 Cargar Plantilla IA", help="Carga una plantilla de ejemplo, reemplazando los datos actuales."):
            if 'confirm_load' not in st.session_state or not st.session_state.confirm_load:
                st.session_state.confirm_load = True
                st.warning("¿Seguro? Se reemplazarán los datos actuales. Vuelve a pulsar para confirmar.")
            else:
                logging.info("Loading AI template via button.")
                # Obtiene los datos de la plantilla (esto también añade macros al estado)
                template_result = get_ai_template_data()
                if template_result[1]: # Verifica si la carga fue exitosa (lista de tareas no vacía)
                    default_roles, default_tasks, default_next_id = template_result
                    # Actualiza el estado de la sesión con los datos de la plantilla
                    st.session_state.roles = default_roles
                    st.session_state.tasks = default_tasks
                    st.session_state.next_task_id = default_next_id
                    # Mantén la configuración actual (o podrías resetearla si la plantilla lo requiere)
                    st.success("Plantilla IA cargada.")
                    del st.session_state.confirm_load # Limpia la bandera de confirmación
                    st.rerun() # Refresca la app
                else:
                     # Si get_ai_template_data devolvió vacío por error, no hacer nada más
                     del st.session_state.confirm_load # Limpia la bandera de confirmación


    st.divider() # Separador visual

    # --- Sección Gestión de Roles ---
    st.subheader("👥 Gestión de Roles")
    roles_col1, roles_col2 = st.columns([0.4, 0.6]) # Ajusta proporción

    with roles_col1:
        # Formulario para añadir o actualizar roles (para añadir y renombrar)
        with st.form("role_form_config"):
            role_name = st.text_input("Nombre del Rol (Nuevo o Existente para Actualizar)")
            role_rate = st.number_input("Tarifa HORARIA (€/hora)", min_value=0.0, step=1.0, format="%.2f")
            role_availability = st.number_input("Disponibilidad (%)", min_value=0.0, max_value=100.0, value=100.0, step=1.0)
            submitted_role = st.form_submit_button("Añadir/Actualizar Rol")
            if submitted_role and role_name.strip(): # Solo procesa si se envió y hay un nombre
                # Guarda o actualiza el rol en el estado de la sesión
                st.session_state.roles[role_name.strip()] = {"availability_percent": role_availability, "rate_eur_hr": role_rate}
                st.success(f"Rol '{role_name.strip()}' añadido/actualizado.")
                st.rerun() # Refresca para actualizar la tabla de roles
            elif submitted_role:
                 st.error("El nombre del rol no puede estar vacío.")

        st.markdown("---") # Separador
        # Sección para eliminar roles
        role_to_delete = st.selectbox("Eliminar Rol", options=[""] + sorted(list(st.session_state.roles.keys())), index=0, key="delete_role_select_config", help="Selecciona un rol para eliminar (solo si no está asignado).")
        if st.button("Eliminar Rol Seleccionado", key="delete_role_btn_config") and role_to_delete:
            # Verifica si el rol está asignado a alguna tarea antes de eliminar
            role_in_use = False
            for task in st.session_state.tasks:
                assignments = parse_assignments(task.get('assignments', [])) # Parsea por si acaso
                if any(assign.get('role') == role_to_delete for assign in assignments):
                    role_in_use = True
                    break
            if role_in_use:
                st.warning(f"El rol '{role_to_delete}' está asignado a una o más tareas y no se puede eliminar.")
            else:
                # Elimina el rol si no está en uso
                del st.session_state.roles[role_to_delete]
                st.success(f"Rol '{role_to_delete}' eliminado.")
                st.rerun() # Refresca para actualizar la tabla

    with roles_col2:
        # Muestra la tabla de roles actuales usando data_editor
        st.write("**Roles Actuales (Editable: Tarifa, Disponibilidad):**")
        if st.session_state.roles:
            # Prepara los datos para el editor
            roles_list = [
                {"Rol": name, "Tarifa Horaria (€/h)": data.get("rate_eur_hr", 0), "Disponibilidad (%)": data.get("availability_percent", 100)}
                for name, data in st.session_state.roles.items()
            ]
            roles_editor_df = pd.DataFrame(roles_list)

            # Guarda el estado actual para comparar después de la edición
            original_roles_editor_df = roles_editor_df.copy()

            edited_roles_df = st.data_editor(
                roles_editor_df,
                key="roles_editor",
                use_container_width=True,
                hide_index=True,
                # Configura las columnas
                column_config={
                    "Rol": st.column_config.TextColumn(disabled=True, help="El nombre del rol no se puede editar aquí. Usa el formulario."),
                    "Tarifa Horaria (€/h)": st.column_config.NumberColumn(required=True, min_value=0.0, format="%.2f €"),
                    "Disponibilidad (%)": st.column_config.NumberColumn(required=True, min_value=0.0, max_value=100.0, format="%.1f %%") # Usa %% para mostrar % literal
                },
                num_rows="fixed" # No permitir añadir/eliminar filas aquí
            )

            # Compara el DataFrame editado con el original para detectar cambios
            if not edited_roles_df.equals(original_roles_editor_df):
                st.info("Detectados cambios en los roles. Actualizando...")
                roles_updated = False
                # Itera sobre el DF editado para actualizar el estado
                for index, row in edited_roles_df.iterrows():
                    role_name = row["Rol"]
                    # Compara con el valor original antes de actualizar
                    original_row = original_roles_editor_df.iloc[index]
                    if row["Tarifa Horaria (€/h)"] != original_row["Tarifa Horaria (€/h)"] or \
                       row["Disponibilidad (%)"] != original_row["Disponibilidad (%)"]:
                        if role_name in st.session_state.roles:
                            st.session_state.roles[role_name]["rate_eur_hr"] = row["Tarifa Horaria (€/h)"]
                            st.session_state.roles[role_name]["availability_percent"] = row["Disponibilidad (%)"]
                            roles_updated = True
                        else:
                             logging.error(f"Role '{role_name}' found in edited roles table but not in session state.")

                if roles_updated:
                    st.success("Roles actualizados.")
                    st.rerun() # Refresca para recalcular costes, etc.
                else:
                     st.info("No se detectaron cambios netos para guardar.")


        else:
            st.info("No hay roles definidos.")

    st.divider()

    # --- Sección Tareas Macro ---
    with st.expander("➕ Gestionar Tareas Macro (Fases)", expanded=False):
        st.subheader("Definir y Editar Tareas Macro / Fases")
        macro_form_col, macro_table_col = st.columns(2)

        with macro_form_col:
            # Formulario para añadir tareas macro (solo añadir)
            with st.form("macro_tasks_form", clear_on_submit=True):
                macro_name_new = st.text_input("Nombre Nueva Tarea Macro / Fase")
                macro_color_new = st.color_picker("Color Asociado", value="#ADD8E6", key="macro_color_picker_new")
                submitted_macro = st.form_submit_button("Agregar Nueva Macro/Fase")
                if submitted_macro:
                    if not macro_name_new or not macro_name_new.strip():
                        st.error("El nombre de la tarea macro/fase es obligatorio.")
                    elif macro_name_new.strip() in st.session_state.macrotasks:
                        st.warning(f"La macro/fase '{macro_name_new.strip()}' ya existe.")
                    else:
                        # Añade la macro al estado de la sesión
                        st.session_state.macrotasks[macro_name_new.strip()] = macro_color_new
                        st.success(f"Tarea Macro/Fase '{macro_name_new.strip()}' agregada.")
                        st.rerun() # Refresca para mostrar en la lista

            st.markdown("---")
            # Sección para eliminar tareas macro
            macro_to_delete = st.selectbox("Eliminar Tarea Macro / Fase", options=[""] + sorted(list(st.session_state.macrotasks.keys())), index=0, key="delete_macro_select")
            if st.button("Eliminar Macro/Fase Seleccionada", key="delete_macro_btn") and macro_to_delete:
                # Verifica si la macro está en uso antes de eliminar
                macro_in_use = any(task.get('macro') == macro_to_delete for task in st.session_state.tasks)
                if macro_in_use:
                    st.warning(f"La macro '{macro_to_delete}' está asignada a tareas. No se puede eliminar directamente. Edita las tareas primero.")
                else:
                    del st.session_state.macrotasks[macro_to_delete]
                    st.success(f"Tarea Macro/Fase '{macro_to_delete}' eliminada.")
                    st.rerun()

        with macro_table_col:
            # Muestra las tareas macro definidas en una tabla editable (solo color)
            st.write("**Tareas Macro / Fases (Editable: Color):**")
            if st.session_state.macrotasks:
                macros_list = [{"Macro/Fase": name, "Color": color} for name, color in st.session_state.macrotasks.items()]
                macros_editor_df = pd.DataFrame(macros_list)
                original_macros_editor_df = macros_editor_df.copy() # Guarda estado original

                edited_macros_df = st.data_editor(
                    macros_editor_df,
                    key="macros_editor",
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Macro/Fase": st.column_config.TextColumn(disabled=True, help="El nombre no se edita aquí."),
                        "Color": st.column_config.TextColumn(required=True, help="Edita el código hexadecimal del color (ej: #FF0000).")
                    },
                    num_rows="fixed"
                )

                # Compara para detectar cambios
                if not edited_macros_df.equals(original_macros_editor_df):
                    st.info("Detectados cambios en los colores de las macros. Actualizando...")
                    macros_updated = False
                    tasks_to_update = False
                    # Actualiza el estado de macrotasks
                    for index, row in edited_macros_df.iterrows():
                        macro_name = row["Macro/Fase"]
                        new_color = row["Color"]
                        # Compara con el original
                        if new_color != original_macros_editor_df.iloc[index]["Color"]:
                            if macro_name in st.session_state.macrotasks:
                                st.session_state.macrotasks[macro_name] = new_color
                                macros_updated = True
                                tasks_to_update = True # Marca que hay que actualizar tareas
                            else:
                                logging.error(f"Macro '{macro_name}' found in edited macros table but not in session state.")

                    # Si se cambió un color, actualiza el color en las tareas asociadas
                    if tasks_to_update:
                        for i, task in enumerate(st.session_state.tasks):
                            task_macro = task.get('macro')
                            if task_macro in st.session_state.macrotasks:
                                new_task_color = st.session_state.macrotasks[task_macro]
                                if st.session_state.tasks[i].get('phase_color') != new_task_color:
                                    st.session_state.tasks[i]['phase_color'] = new_task_color
                                    # No marcamos 'macros_updated' aquí, ya se hizo si el color cambió en macrotasks

                    if macros_updated:
                        st.success("Colores de Macros actualizados.")
                        st.rerun() # Refresca para que Gantt, etc., usen los nuevos colores
                    else:
                        st.info("No se detectaron cambios netos para guardar.")

            else:
                st.info("No hay tareas macro definidas.")

    st.divider()

    # --- Sección Configuración de Horas ---
    st.subheader("🕒 Configuración de Horas de Trabajo por Día")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    cols_days = st.columns(len(days)) # Crea columnas para cada día
    working_hours_changed = False
    for i, day in enumerate(days):
        with cols_days[i]:
            current_val = st.session_state.config['working_hours'].get(day, 8.0)
            # Input numérico para las horas de cada día
            new_val = st.number_input(
                f"{day[:3]}", # Muestra solo las 3 primeras letras del día
                min_value=0.0, max_value=24.0, value=current_val, step=0.5,
                key=f"working_{day}", help=f"Horas de trabajo para {day}"
            )
            if new_val != current_val:
                 working_hours_changed = True
                 st.session_state.config['working_hours'][day] = new_val

    # Checkbox para excluir fines de semana
    exclude_weekends_current = st.session_state.config.get('exclude_weekends', True)
    exclude_weekends_new = st.checkbox("Excluir Sábados y Domingos del cálculo de duración", value=exclude_weekends_current, key="exclude_weekends_toggle")
    config_changed = False
    if exclude_weekends_new != exclude_weekends_current:
        st.session_state.config['exclude_weekends'] = exclude_weekends_new
        config_changed = True

    if working_hours_changed or config_changed:
         st.info("Cambio detectado en la configuración de horas/días. Considera recalcular las fechas del proyecto para aplicar los cambios.")
         st.rerun() # Rerun para actualizar visualmente el checkbox si cambió y registrar cambio de horas

    st.divider()

    # --- Sección Margen de Beneficio ---
    st.subheader("📈 Margen de Beneficio")
    current_margin = st.session_state.config.get('profit_margin_percent', 0.0)
    new_margin = st.number_input(
        "Margen de Beneficio (%) sobre Coste Bruto",
        min_value=0.0,
        value=current_margin,
        step=1.0,
        format="%.2f",
        key="profit_margin_input",
        help="Introduce el porcentaje de margen deseado. El coste de venta se calculará como Coste Bruto * (1 + Margen/100)."
    )
    if new_margin != current_margin:
        st.session_state.config['profit_margin_percent'] = new_margin
        st.success(f"Margen de beneficio actualizado a {new_margin:.2f}%.")
        st.rerun() # Rerun para que la pestaña de costes refleje el cambio inmediatamente


    st.divider()


    # --- Sección Recalcular Fechas ---
    st.subheader("🔄 Recalcular Fechas del Proyecto")
    st.warning("Recalcular ajustará las fechas de inicio y fin de TODAS las tareas según sus dependencias y la configuración actual. Las fechas de inicio manuales de tareas sin dependencias NO se modificarán.")
    if st.button("Recalcular Fechas Ahora", key="recalc_dates_btn"):
        if not st.session_state.tasks:
             st.info("No hay tareas para recalcular.")
        else:
            temp_tasks = [t.copy() for t in st.session_state.tasks] # Copia profunda
            temp_end_dates = {} # Mapa temporal para fechas de fin
            recalc_processed_ids = set() # IDs procesados
            recalc_tasks_final = [] # Lista final
            exclude_weekends_recalc = st.session_state.config['exclude_weekends']
            # Mapa inicial de fechas de inicio (fallback)
            start_date_map = {t['id']: t['start_date'] for t in temp_tasks if isinstance(t.get('start_date'), datetime.date)}
            ids_to_recalc = sorted([t['id'] for t in temp_tasks]) # IDs a procesar

            max_iter_recalc = len(ids_to_recalc) * 2 # Límite
            iter_recalc = 0
            calculation_successful = True

            while len(recalc_processed_ids) < len(ids_to_recalc) and iter_recalc < max_iter_recalc and calculation_successful:
                processed_iter = False
                for task_id in ids_to_recalc:
                    if task_id in recalc_processed_ids:
                        continue # Salta si ya se procesó

                    task_data = next((t for t in temp_tasks if t['id'] == task_id), None) # Busca la tarea
                    if not task_data:
                        logging.warning(f"Recalc: Task ID {task_id} not found in temp list.")
                        continue

                    dependencies = parse_dependencies(task_data.get('dependencies', '[]'))
                    # Verifica si todas las dependencias ya tienen fecha de fin calculada
                    deps_met = all(dep_id in recalc_processed_ids for dep_id in dependencies)

                    if deps_met:
                        # Obtiene la fecha de inicio original como fallback
                        default_start = start_date_map.get(task_id, datetime.date.today())
                        # Calcula la nueva fecha de inicio basada en dependencias
                        start_date = calculate_dependent_start_date(task_data.get('dependencies', '[]'), recalc_tasks_final, temp_end_dates, default_start)

                        if start_date is None:
                            logging.error(f"Recalc: Failed to calculate start date for task {task_id} ({task_data.get('name')}). Aborting recalc.")
                            calculation_successful = False
                            break # Aborta si falla el cálculo

                        # Calcula la nueva fecha de fin
                        end_date = calculate_end_date(start_date, task_data['duration'], exclude_weekends_recalc)
                        if end_date is None:
                            logging.error(f"Recalc: Failed to calculate end date for task {task_id} ({task_data.get('name')}) (Start: {start_date}, Duration: {task_data['duration']}). Using start date.")
                            end_date = start_date # Usa fecha de inicio si falla

                        # Actualiza la tarea con las nuevas fechas
                        task_data['start_date'] = start_date

                        recalc_tasks_final.append(task_data) # Añade a la lista final
                        temp_end_dates[task_id] = end_date # Guarda la fecha fin para dependientes
                        recalc_processed_ids.add(task_id) # Marca como procesado
                        processed_iter = True
                # Sale del bucle for si calculation_successful se volvió False
                if not calculation_successful:
                     break

                iter_recalc += 1
                # Si no se procesó nada y quedan tareas
                if (not processed_iter and len(recalc_processed_ids) < len(ids_to_recalc)):
                    logging.error("Error recalculating dates. Possible circular dependency or unresolved task.")
                    st.error("Error al recalcular fechas. Revisa dependencias circulares o datos inválidos en tareas no procesadas.")
                    calculation_successful = False
                    break # Sale del bucle while

            # Si todas las tareas se procesaron con éxito
            if calculation_successful and len(recalc_processed_ids) == len(ids_to_recalc):
                st.session_state.tasks = recalc_tasks_final # Actualiza el estado principal
                st.success("Fechas del proyecto recalculadas con éxito.")
                st.rerun() # Refresca toda la app
            elif calculation_successful: # No se procesaron todas, pero no hubo error explícito
                 unprocessed_ids = set(ids_to_recalc) - recalc_processed_ids
                 logging.warning(f"Could not recalculate all dates. Unprocessed task IDs: {unprocessed_ids}")
                 st.warning(f"No se pudieron recalcular todas las fechas. Podría haber dependencias no resolubles para las tareas con ID: {unprocessed_ids}")
            # Si calculation_successful es False, ya se mostró un error


    st.divider()

    # --- Sección Exportar/Importar ---
    st.subheader("💾 Gestión de Datos del Proyecto")
    col_export, col_import = st.columns(2)

    with col_export:
        st.write("**Exportar Plan**")
        # Prepara los datos para exportar a JSON
        export_data = {
            "roles": st.session_state.roles,
            "tasks": [],
            "next_task_id": st.session_state.next_task_id,
            "config": st.session_state.config, # Incluye toda la configuración
            "macrotasks": st.session_state.macrotasks
        }
        # Procesa las tareas para el formato JSON
        for task in st.session_state.tasks:
            task_copy = task.copy()
            if isinstance(task_copy.get('start_date'), datetime.date):
                task_copy['start_date'] = task_copy['start_date'].isoformat() # Formato ISO
            task_copy.pop('end_date', None) # Elimina end_date (se recalcula)

            # Asegura formato correcto de asignaciones y dependencias
            task_copy['assignments'] = parse_assignments(task_copy.get('assignments', []))
            task_copy['dependencies'] = json.dumps(parse_dependencies(task_copy.get('dependencies', '[]')))

            export_data["tasks"].append(task_copy)

        try:
            # Convierte a string JSON
            json_str = json.dumps(export_data, indent=2, ensure_ascii=False)
            # Botón de descarga
            st.download_button(
                label="Descargar Plan (JSON)",
                data=json_str,
                file_name=f"project_plan_{datetime.date.today()}.json",
                mime="application/json"
            )
        except Exception as e:
            st.error(f"Error al generar JSON para exportar: {e}")
            logging.error(f"JSON export error: {e}", exc_info=True)

    with col_import:
        st.write("**Importar Plan**")
        # Widget para subir archivo JSON
        uploaded_file = st.file_uploader("Cargar archivo JSON del plan", type=["json"])
        if uploaded_file is not None:
            # Botón para confirmar la importación
            if st.button("Confirmar Importación", key="confirm_import_btn"):
                try:
                    # Lee y parsea el archivo JSON
                    imported_data = json.load(uploaded_file)

                    # Validación básica de estructura
                    if "roles" in imported_data and "tasks" in imported_data and "next_task_id" in imported_data:
                        imported_tasks = []
                        # Procesa cada tarea importada
                        for task_data in imported_data["tasks"]:
                            # Convierte fecha de string a objeto date
                            if isinstance(task_data.get('start_date'), str):
                                try:
                                    task_data['start_date'] = datetime.date.fromisoformat(task_data['start_date'])
                                except ValueError:
                                    logging.warning(f"Invalid date format in imported task {task_data.get('id')}. Using today.")
                                    task_data['start_date'] = datetime.date.today()
                            elif not isinstance(task_data.get('start_date'), datetime.date):
                                 logging.warning(f"Missing or invalid start_date type for task {task_data.get('id')}. Using today.")
                                 task_data['start_date'] = datetime.date.today()

                            # Parsea y valida asignaciones y dependencias
                            task_data['assignments'] = parse_assignments(task_data.get('assignments', []))
                            task_data['dependencies'] = json.dumps(parse_dependencies(task_data.get('dependencies', '[]')))

                            # Añade campos faltantes con valores por defecto
                            task_data.setdefault('status', 'Pendiente')
                            task_data.setdefault('notes', '')
                            task_data.setdefault('parent_id', None)
                            task_data.setdefault('macro', 'Sin Fase')
                            task_data.setdefault('subtask', task_data.get('name', 'Sin Subtarea'))
                            task_data.setdefault('phase_color', '#CCCCCC')
                            # Reconstruye el nombre
                            task_data['name'] = f"{task_data['macro']} - {task_data['subtask']}"

                            imported_tasks.append(task_data)

                        # Actualiza el estado de la sesión
                        st.session_state.roles = imported_data["roles"]
                        st.session_state.tasks = imported_tasks
                        st.session_state.next_task_id = imported_data["next_task_id"]
                        # Importa la configuración completa
                        st.session_state.config = imported_data.get("config", st.session_state.config)
                        st.session_state.macrotasks = imported_data.get("macrotasks", {})

                        # Asegura valores por defecto en config si faltan
                        st.session_state.config.setdefault('exclude_weekends', True)
                        st.session_state.config.setdefault('working_hours', {
                            "Monday": 8.0, "Tuesday": 8.0, "Wednesday": 8.0, "Thursday": 8.0,
                            "Friday": 8.0, "Saturday": 0.0, "Sunday": 0.0
                        })
                        st.session_state.config.setdefault('profit_margin_percent', 0.0)

                        # Asegura que los colores de fase coincidan con las macros importadas
                        for i, task in enumerate(st.session_state.tasks):
                             st.session_state.tasks[i]['phase_color'] = st.session_state.macrotasks.get(task['macro'], "#CCCCCC")


                        st.success("Plan importado con éxito.")
                        st.info("Refrescando la aplicación...")
                        st.rerun() # Refresca para mostrar los datos
                    else:
                        st.error("El archivo JSON no tiene la estructura esperada (faltan 'roles', 'tasks' o 'next_task_id').")
                except json.JSONDecodeError:
                     st.error("Error: El archivo subido no es un JSON válido.")
                except Exception as e:
                    st.error(f"Error inesperado al importar el archivo: {e}")
                    logging.error(f"File import error: {e}", exc_info=True)
            else:
                # Mensaje mientras el archivo está cargado pero no confirmado
                st.info("Archivo JSON seleccionado. Pulsa 'Confirmar Importación' para cargar los datos (esto reemplazará el plan actual).")


# --- Preparación de Datos Común (Cálculos) ---
# Esta sección se ejecuta siempre para tener los datos listos para las pestañas

# Convierte la lista de tareas del estado a un DataFrame de Pandas
tasks_list_for_df = st.session_state.tasks
if tasks_list_for_df:
     # Crea copia para evitar modificar el estado directamente en algunos pasos
     tasks_df_list_copy = [t.copy() for t in tasks_list_for_df]
     tasks_df = pd.DataFrame(tasks_df_list_copy)

     # --- Limpieza y Validación de Datos del DataFrame ---
     # Asegura tipos numéricos y maneja errores
     tasks_df['duration'] = pd.to_numeric(tasks_df['duration'], errors='coerce').fillna(1).astype(int)
     tasks_df['duration'] = tasks_df['duration'].apply(lambda x: 1 if x <= 0 else x)

     # Convierte a fecha, NaT si error
     tasks_df['start_date'] = pd.to_datetime(tasks_df['start_date'], errors='coerce').dt.date

     # Asegura que 'assignments' sea lista válida
     tasks_df['assignments'] = tasks_df['assignments'].apply(parse_assignments)

     # Asegura que 'macro' y 'subtask' existan y no sean NaN
     tasks_df['macro'] = tasks_df['macro'].fillna('Sin Fase').astype(str)
     tasks_df['subtask'] = tasks_df['subtask'].fillna('Sin Subtarea').astype(str)
     tasks_df['name'] = tasks_df['macro'] + " - " + tasks_df['subtask'] # Reconstruye nombre

     # Asegura 'phase_color'
     tasks_df['phase_color'] = tasks_df['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))

     # --- Cálculos Derivados ---
     # Calcula la fecha de fin para cada tarea
     tasks_df['end_date'] = tasks_df.apply(
         lambda row: calculate_end_date(row['start_date'], row['duration'], st.session_state.config.get('exclude_weekends', True))
                     if pd.notna(row['start_date']) else pd.NaT, # Solo calcula si hay fecha de inicio válida, sino NaT
         axis=1
     )
     # Intenta convertir a tipo fecha después del cálculo, manejando NaT
     tasks_df['end_date'] = pd.to_datetime(tasks_df['end_date'], errors='coerce').dt.date


     # Calcula el coste para cada tarea
     tasks_df['cost'] = tasks_df.apply(
         lambda row: calculate_task_cost_by_schedule(
                         row['start_date'],
                         row['end_date'],
                         row['assignments'],
                         st.session_state.config['working_hours'],
                         st.session_state.config['exclude_weekends']
                     ) if isinstance(row['start_date'], datetime.date) and isinstance(row['end_date'], datetime.date) else 0.0, # Coste 0 si fechas inválidas
         axis=1
     )

     # Crea un mapa de ID de tarea -> fecha de fin (solo para tareas con fecha de fin válida)
     valid_end_dates = tasks_df.dropna(subset=['id', 'end_date'])
     task_end_dates_map = pd.Series(valid_end_dates.end_date.values, index=valid_end_dates.id).to_dict()

else:
     # Si no hay tareas, crea un DataFrame vacío con las columnas esperadas
     tasks_df = pd.DataFrame(columns=[
         'id', 'macro', 'subtask', 'phase_color', 'name', 'start_date', 'duration',
         'assignments', 'dependencies', 'status', 'notes', 'end_date', 'cost'
     ])
     task_end_dates_map = {}


# --- Pestaña de Tareas (Edición y Creación) ---
with tab_tasks:
    st.header("📝 Gestión Detallada de Tareas")

    # --- Expander para Añadir Nueva Tarea ---
    with st.expander("➕ Añadir Nueva Tarea", expanded=False):
        # Formulario para la nueva tarea
        with st.form("new_task_form_v3_7", clear_on_submit=True): # Key actualizada
            st.write("Define los detalles de la nueva tarea:")
            # Selección o input de Tarea Macro/Fase
            if st.session_state.macrotasks:
                macro_options = [""] + sorted(list(st.session_state.macrotasks.keys())) # Añade opción vacía y ordena
                default_macro_index = 0
                # Preselecciona la última macro usada si existe
                if st.session_state.last_macro in macro_options:
                    default_macro_index = macro_options.index(st.session_state.last_macro)
                selected_macro = st.selectbox("Tarea Macro / Fase (*)", options=macro_options, index=default_macro_index,
                                              help="Selecciona la fase o tarea macro a la que pertenece esta subtarea.")
                phase_color = st.session_state.macrotasks.get(selected_macro, "#CCCCCC") # Obtiene color o usa gris
            else:
                # Si no hay macros definidas, permite input de texto y color
                selected_macro = st.text_input("Tarea Macro / Fase (*)", help="No hay tareas macro definidas. Ingresa un nombre para la fase.")
                phase_color = st.color_picker("Color para esta Fase", value="#ADD8E6", key="newtask_phase_color")

            # Input para el nombre de la subtarea
            subtask_name = st.text_input("Nombre de la Subtarea (*)", help="Nombre específico de esta tarea.")

            # Combina macro y subtarea para el nombre completo (si ambos están definidos)
            task_name_preview = ""
            if selected_macro and selected_macro.strip() and subtask_name and subtask_name.strip():
                task_name_preview = f"{selected_macro.strip()} - {subtask_name.strip()}"
                st.caption(f"Nombre completo será: {task_name_preview}") # Muestra el nombre generado

            # Inputs para fecha de inicio y duración
            task_start_date = st.date_input("Fecha Inicio (*)", value=datetime.date.today())
            task_duration = st.number_input("Duración (días) (*)", min_value=1, step=1, value=1)

            # Selección de dependencias
            dep_options = {task['id']: f"{task['name']} (ID: {task['id']})"
                           for task in sorted(st.session_state.tasks, key=lambda x: x.get('start_date', datetime.date.min))} # Ordena opciones por fecha
            task_dependencies_ids = st.multiselect(
                "Dependencias (La tarea empezará después de estas)",
                options=list(dep_options.keys()),
                format_func=lambda x: dep_options.get(x, f"ID {x}?"), # Muestra nombre e ID
                help="Selecciona las tareas que deben completarse antes de que esta pueda comenzar."
            )

            # Selección de estado inicial y notas
            task_status = st.selectbox("Estado Inicial", options=["Pendiente", "En Progreso", "Completada", "Bloqueada"], index=0)
            task_notes = st.text_area("Notas Adicionales")

            # Sección para asignar roles y dedicación
            st.markdown("--- \n ### Asignaciones (Definir % de dedicación por rol)")
            assignment_data = {} # Diccionario para guardar las asignaciones
            if st.session_state.roles:
                cols = st.columns(len(st.session_state.roles)) # Columnas para cada rol
                for i, role in enumerate(sorted(st.session_state.roles.keys())): # Ordena roles alfabéticamente
                    with cols[i]:
                        # Input numérico para la dedicación de cada rol
                        assignment_data[role] = st.number_input(f"{role} (%)", min_value=0, max_value=100, value=0, step=5, key=f"newtask_alloc_{role}")
            else:
                st.warning("No hay roles definidos. Ve a '⚙️ Configuración/Datos' para añadirlos y poder asignar tareas.")

            # Botón de envío del formulario
            submitted_new_task = st.form_submit_button("✅ Añadir Tarea al Plan")

            # Procesamiento al enviar el formulario
            if submitted_new_task:
                # Validaciones básicas de campos obligatorios
                final_selected_macro = selected_macro.strip() if selected_macro else ""
                final_subtask_name = subtask_name.strip() if subtask_name else ""

                if not final_selected_macro or not final_subtask_name or not task_duration:
                    st.error("Por favor, completa todos los campos obligatorios (*): Tarea Macro/Fase, Subtarea y Duración.")
                else:
                    # Si hay dependencias seleccionadas, intenta calcular la fecha de inicio automáticamente
                    if task_dependencies_ids:
                        # Usa la lista actual de tareas para calcular dependencias
                        computed_start_date = compute_auto_start_date(task_dependencies_ids, st.session_state.tasks)
                        if computed_start_date is not None:
                            task_start_date = computed_start_date
                            st.info(f"Fecha de inicio calculada automáticamente: {task_start_date.strftime('%Y-%m-%d')} basada en dependencias.")
                        else:
                            st.warning("No se pudo calcular la fecha de inicio automáticamente por falta de datos en dependencias. Se usará la fecha manual.")

                    # Obtiene el nuevo ID y actualiza el contador
                    new_task_id = st.session_state.next_task_id
                    st.session_state.next_task_id += 1
                    # Guarda la última macro usada para preselección futura
                    st.session_state.last_macro = final_selected_macro
                    # Filtra las asignaciones para guardar solo las > 0%
                    new_assignments = [{'role': role, 'allocation': alloc} for role, alloc in assignment_data.items() if alloc > 0]

                    # Obtiene el color final (puede haber cambiado si se creó la macro aquí)
                    final_phase_color = st.session_state.macrotasks.get(final_selected_macro, phase_color) # Usa phase_color si la macro es nueva

                    # Crea el diccionario de la nueva tarea
                    new_task = {
                        'id': new_task_id,
                        'macro': final_selected_macro,
                        'subtask': final_subtask_name,
                        'phase_color': final_phase_color,
                        'name': f"{final_selected_macro} - {final_subtask_name}",
                        'start_date': task_start_date,
                        'duration': task_duration,
                        'assignments': new_assignments,
                        'dependencies': json.dumps(task_dependencies_ids), # Guarda IDs como JSON string
                        'status': task_status,
                        'notes': task_notes,
                        'parent_id': None # No implementado aún
                    }

                    # Añade la nueva tarea a la lista en el estado de la sesión
                    st.session_state.tasks.append(new_task)
                    st.success(f"Tarea '{new_task['name']}' (ID: {new_task_id}) añadida con éxito.")
                    st.rerun() # Refresca la app para mostrar la nueva tarea en la tabla

    st.divider() # Separador visual

    # --- Sección Lista de Tareas (Editable) ---
    st.subheader("📋 Lista de Tareas")
    if not tasks_df.empty:
        # Prepara el DataFrame para el editor
        # Asegura que tasks_df tenga las últimas tareas añadidas si hubo un rerun justo antes
        tasks_df_display = pd.DataFrame([t.copy() for t in st.session_state.tasks])
        if not tasks_df_display.empty:
            # Recalcula columnas calculadas/formateadas para el display
            tasks_df_display['end_date'] = tasks_df_display.apply(
                lambda row: calculate_end_date(row.get('start_date'), row.get('duration'), st.session_state.config.get('exclude_weekends', True))
                            if isinstance(row.get('start_date'), datetime.date) else pd.NaT,
                axis=1
            )
            tasks_df_display['end_date'] = pd.to_datetime(tasks_df_display['end_date'], errors='coerce').dt.date

            tasks_df_display['cost'] = tasks_df_display.apply(
                lambda row: calculate_task_cost_by_schedule(
                                row.get('start_date'), row.get('end_date'), row.get('assignments',[]),
                                st.session_state.config['working_hours'], st.session_state.config['exclude_weekends']
                            ) if isinstance(row.get('start_date'), datetime.date) and isinstance(row.get('end_date'), datetime.date) else 0.0,
                axis=1
            )
            tasks_df_display['assignments_display'] = tasks_df_display['assignments'].apply(format_assignments_display)
            tasks_df_display['dependencies_display'] = tasks_df_display['dependencies'].apply(
                lambda d: format_dependencies_display(d, st.session_state.tasks)
            )
            tasks_df_display['cost_display'] = tasks_df_display['cost'].apply(lambda x: f"€ {x:,.2f}")
            tasks_df_display['end_date_display'] = tasks_df_display['end_date'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) else 'N/A')
            # Asegura que phase_color esté actualizado
            tasks_df_display['phase_color'] = tasks_df_display['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))
            # Reconstruye nombre por si acaso
            tasks_df_display['name'] = tasks_df_display['macro'] + " - " + tasks_df_display['subtask']


            # Guarda estado original para comparación
            original_tasks_editor_df = tasks_df_display.copy()


            # Configuración de las columnas del data_editor
            column_config_tasks = {
                "id": st.column_config.NumberColumn("ID", disabled=True, help="ID único de la tarea (no editable aquí)."),
                "macro": st.column_config.TextColumn("Macro/Fase", required=True, help="Edita la fase o macro."),
                "subtask": st.column_config.TextColumn("Subtarea", required=True, help="Edita el nombre de la subtarea."),
                "phase_color": st.column_config.TextColumn("Color", disabled=True, help="Color asociado a la Macro/Fase (se edita en Configuración)."),
                "name": st.column_config.TextColumn("Nombre Completo", disabled=True, width="large", help="Generado automáticamente (Macro - Subtarea)."),
                "start_date": st.column_config.DateColumn("Fecha Inicio", required=True, format="YYYY-MM-DD", help="Edita la fecha de inicio."),
                "duration": st.column_config.NumberColumn("Duración (días)", required=True, min_value=1, step=1, help="Edita la duración en días laborables."),
                "dependencies": st.column_config.TextColumn("Dependencias (IDs JSON)", help="Edita los IDs de las dependencias en formato JSON, ej: [1, 3]. ¡Cuidado!"),
                "dependencies_display": st.column_config.TextColumn("Dependencias (Nombres)", disabled=True, help="Tareas de las que depende (solo lectura)."),
                "status": st.column_config.SelectboxColumn("Estado", options=["Pendiente", "En Progreso", "Completada", "Bloqueada", "Pendiente (Error Dep?)"], help="Actualiza el estado de la tarea."),
                "notes": st.column_config.TextColumn("Notas", width="medium", help="Edita las notas adicionales."),
                "end_date": None, # Oculta la columna de fecha fin calculada original
                "end_date_display": st.column_config.TextColumn("Fecha Fin (Calc.)", disabled=True, help="Fecha de fin calculada (solo lectura)."),
                "cost": None, # Oculta la columna de coste original
                "cost_display": st.column_config.TextColumn("Coste (Calc.)", disabled=True, help="Coste calculado (solo lectura)."),
                "assignments": None, # Oculta la columna de asignaciones original (lista de dicts)
                "assignments_display": st.column_config.TextColumn("Asignaciones", disabled=True, help="Roles asignados (se editan abajo).")
            }

            # Columnas a mostrar en el editor (orden deseado)
            cols_to_display_editor = [
                'id', 'macro', 'subtask', 'start_date', 'duration',
                'dependencies_display', 'status', 'notes', 'end_date_display',
                'cost_display', 'assignments_display', 'dependencies' # 'dependencies' al final para edición experta
            ]

            # Muestra el data_editor
            edited_df_tasks = st.data_editor(
                tasks_df_display[cols_to_display_editor],
                column_config=column_config_tasks,
                key="task_editor_v3_8", # Clave actualizada
                num_rows="dynamic", # Permite añadir/eliminar filas
                use_container_width=True,
                hide_index=True,
            )

            # --- Procesamiento de Cambios del Data Editor ---
            if edited_df_tasks is not None:
                try:
                    updated_tasks_from_editor = [] # Lista para guardar tareas actualizadas/nuevas
                    current_max_id = st.session_state.next_task_id - 1 # ID máximo actual antes de procesar
                    processed_ids_editor = set() # IDs vistos en el editor
                    # Mapas de datos originales para mantener lo no editable
                    original_assignments = {task['id']: task['assignments'] for task in st.session_state.tasks}
                    original_colors = {task['id']: task.get('phase_color', '#CCCCCC') for task in st.session_state.tasks}
                    original_dependencies = {task['id']: task.get('dependencies', '[]') for task in st.session_state.tasks}


                    # Itera sobre las filas del DataFrame editado
                    for i, row in edited_df_tasks.iterrows():
                        task_id = row.get('id')
                        is_new_row = pd.isna(task_id) or task_id <= 0 # Detecta si es una fila nueva

                        if is_new_row:
                            # Asigna un nuevo ID si es una fila nueva
                            task_id = st.session_state.next_task_id
                            st.session_state.next_task_id += 1
                            current_assignments = [] # Sin asignaciones por defecto
                            current_color = "#CCCCCC" # Color por defecto
                            current_deps_str = '[]' # Sin dependencias por defecto
                        else:
                            # Si es una fila existente, obtiene el ID y datos originales
                            task_id = int(task_id)
                            current_assignments = original_assignments.get(task_id, [])
                            current_color = original_colors.get(task_id, '#CCCCCC')
                            current_deps_str = original_dependencies.get(task_id, '[]')


                        processed_ids_editor.add(task_id) # Marca el ID como procesado

                        # Procesa las dependencias editadas (columna 'dependencies')
                        raw_deps = row.get('dependencies')
                        # Comprueba si el valor es diferente del original (o si es fila nueva) y no es NaN/None
                        if (raw_deps != current_deps_str or is_new_row) and pd.notna(raw_deps):
                            if isinstance(raw_deps, str) and raw_deps.strip():
                                try:
                                    deps_list = parse_dependencies(raw_deps) # Parsea para validar y limpiar
                                    deps_str = json.dumps(deps_list) # Guarda como JSON string limpio
                                except Exception as e:
                                    st.warning(f"Error al parsear dependencias para Tarea ID {task_id}: '{raw_deps}'. Se mantendrán las originales. Error: {e}")
                                    deps_str = current_deps_str # Usa las originales si hay error
                            elif is_new_row and (not isinstance(raw_deps, str) or not raw_deps.strip()):
                                deps_str = '[]' # Si es nueva y vacía, usa '[]'
                            else:
                                # Si no es nueva y se borró el contenido, usa '[]'
                                deps_str = '[]'
                        else:
                            # Si no cambió o es NaN, mantiene el valor original
                            deps_str = current_deps_str

                        # Obtiene macro y subtarea, maneja NaNs o vacíos
                        macro_val = str(row.get("macro", "")).strip()
                        subtask_val = str(row.get("subtask", "")).strip()
                        if not macro_val: macro_val = "Sin Fase"
                        if not subtask_val: subtask_val = "Sin Subtarea"

                        # Reconstruye el nombre
                        name_val = f"{macro_val} - {subtask_val}"
                        # Obtiene el color asociado a la macro (si existe) o usa el actual/default
                        phase_color_val = st.session_state.macrotasks.get(macro_val, current_color)

                        # Construye el diccionario de la tarea actualizada/nueva
                        task_data = {
                            'id': task_id,
                            'macro': macro_val,
                            'subtask': subtask_val,
                            'phase_color': phase_color_val,
                            'name': name_val,
                            # Convierte fecha a objeto date, usa hoy si es inválida o NaT
                            'start_date': pd.to_datetime(row.get('start_date'), errors='coerce').date() if pd.notna(row.get('start_date')) else datetime.date.today(),
                            # Asegura duración mínima de 1
                            'duration': max(1, int(row['duration'])) if pd.notna(row.get('duration')) else 1,
                            'assignments': current_assignments, # Mantiene asignaciones (se editan abajo)
                            'dependencies': deps_str, # Dependencias procesadas
                            'status': str(row.get('status', 'Pendiente')),
                            'notes': str(row.get('notes', ''))
                            # 'parent_id' no se maneja aquí
                        }
                        updated_tasks_from_editor.append(task_data)

                    # --- Detección y Manejo de Filas Eliminadas ---
                    original_ids = set(t['id'] for t in st.session_state.tasks)
                    deleted_ids = original_ids - processed_ids_editor # IDs que estaban antes pero no ahora

                    final_task_list = updated_tasks_from_editor # Empieza con las tareas actualizadas/nuevas
                    safe_to_delete = True
                    tasks_depending_on_deleted = []

                    if deleted_ids:
                        logging.info(f"Attempting to delete task IDs: {deleted_ids}")
                        # Verifica si alguna tarea *restante* depende de las eliminadas
                        remaining_tasks_after_potential_delete = [t for t in final_task_list if t['id'] not in deleted_ids]
                        for task in remaining_tasks_after_potential_delete:
                            task_deps = parse_dependencies(task.get('dependencies', '[]'))
                            # Comprueba si alguna de las dependencias de esta tarea está en la lista de eliminadas
                            offending_deps = set(task_deps) & deleted_ids
                            if offending_deps:
                                safe_to_delete = False
                                tasks_depending_on_deleted.append(f"'{task['name']}' (depende de ID(s): {offending_deps})")
                                # No necesitamos seguir buscando para esta tarea eliminada si ya encontramos una dependencia

                        if not safe_to_delete:
                            st.error(f"No se pueden eliminar tareas (IDs: {deleted_ids}) porque otras tareas dependen de ellas: {'; '.join(list(set(tasks_depending_on_deleted)))}. Edita primero las dependencias de esas tareas.")
                            # Si no es seguro eliminar, no actualizamos el estado (los cambios se pierden)
                        else:
                            logging.info(f"Tasks {deleted_ids} are safe to delete.")
                            # Si es seguro eliminar, procedemos a actualizar el estado
                            # Compara listas de diccionarios de forma más robusta
                            current_tasks_json = json.dumps(st.session_state.tasks, sort_keys=True, default=str)
                            final_tasks_json = json.dumps(final_task_list, sort_keys=True, default=str)
                            if current_tasks_json != final_tasks_json:
                                st.session_state.tasks = final_task_list
                                st.success("Cambios guardados (incluyendo eliminaciones).")
                                st.rerun() # Refresca para mostrar el estado actualizado
                    else:
                        # Si no hubo eliminaciones, simplemente actualiza si hubo otros cambios
                        # Compara listas de diccionarios de forma más robusta
                        current_tasks_json = json.dumps(st.session_state.tasks, sort_keys=True, default=str)
                        final_tasks_json = json.dumps(final_task_list, sort_keys=True, default=str)
                        if current_tasks_json != final_tasks_json:
                            st.session_state.tasks = final_task_list
                            st.success("Cambios guardados.")
                            st.rerun()

                except Exception as e:
                    logging.error(f"Error processing data editor changes: {e}", exc_info=True)
                    st.error(f"Error al procesar los cambios de la tabla: {e}")
        else:
             # Caso donde tasks_df_display está vacío después de la copia y recálculo
             st.info("No hay tareas para mostrar en el editor.")


    else:
        st.info("Aún no hay tareas en el plan. Añade una tarea usando el formulario de arriba o importa un plan existente.")

    st.divider()

    # --- Sección Editar Asignaciones por Tarea ---
    st.subheader("💼 Editar Asignaciones de Roles por Tarea")
    if not st.session_state.tasks:
        st.info("Crea o importa alguna tarea primero para poder editar sus asignaciones.")
    elif not st.session_state.roles:
        st.warning("No hay roles definidos. Ve a '⚙️ Configuración/Datos' para añadirlos y poder asignar tareas.")
    else:
        # Selector para elegir la tarea a editar
        task_options_assign = {task['id']: f"{task['name']} (ID: {task['id']})"
                               for task in sorted(st.session_state.tasks, key=lambda x: x.get('start_date', datetime.date.min))} # Ordena tareas
        selected_task_id_assign = st.selectbox(
            "Selecciona Tarea para Editar Asignaciones:",
            options=[None] + list(task_options_assign.keys()), # Añade None como opción por defecto
            format_func=lambda x: task_options_assign.get(x, "Elige una tarea..."), # Texto placeholder
            index=0, # Empieza sin selección
            key="assign_task_selector"
        )

        # Si se ha seleccionado una tarea
        if selected_task_id_assign is not None:
            task_to_edit = get_task_by_id(selected_task_id_assign, st.session_state.tasks)
            if task_to_edit:
                st.write(f"**Editando Asignaciones para:** {task_to_edit['name']}")
                # Obtiene las asignaciones actuales de la tarea
                current_assignments = parse_assignments(task_to_edit.get('assignments', [])) # Parsea por seguridad
                # Crea un diccionario con las asignaciones actuales para fácil acceso: {rol: alloc}
                current_allocations = {a['role']: a['allocation'] for a in current_assignments if isinstance(a, dict)}

                new_assignments_data = {} # Diccionario para guardar las nuevas asignaciones
                st.write("**Define la Dedicación (%) de cada Rol para esta Tarea:**")
                cols_assign = st.columns(len(st.session_state.roles)) # Columnas para cada rol disponible

                # Itera sobre todos los roles definidos en el proyecto (ordenados)
                for i, role in enumerate(sorted(st.session_state.roles.keys())):
                    with cols_assign[i]:
                        # Obtiene la asignación actual para este rol (0 si no estaba asignado)
                        default_alloc = current_allocations.get(role, 0)
                        # Input numérico para la nueva asignación
                        allocation = st.number_input(
                            f"{role} (%)",
                            min_value=0, max_value=100,
                            value=int(default_alloc), # Usa la asignación actual como valor por defecto
                            step=5,
                            key=f"alloc_{selected_task_id_assign}_{role}" # Clave única para el widget
                        )
                        # Guarda la nueva asignación (incluso si es 0, para poder desasignar)
                        new_assignments_data[role] = allocation

                # Botón para guardar los cambios
                if st.button("💾 Guardar Asignaciones", key=f"save_assign_{selected_task_id_assign}"):
                    # Filtra para guardar solo roles con asignación > 0%
                    updated_assignments = [{'role': role, 'allocation': alloc}
                                           for role, alloc in new_assignments_data.items() if alloc > 0]

                    # Busca la tarea en el estado y actualiza sus asignaciones
                    for i, task in enumerate(st.session_state.tasks):
                        if task['id'] == selected_task_id_assign:
                            st.session_state.tasks[i]['assignments'] = updated_assignments
                            break # Sale del bucle una vez encontrada y actualizada

                    st.success(f"Asignaciones guardadas para la tarea '{task_to_edit['name']}'.")
                    st.rerun() # Refresca para mostrar los cambios en la tabla y otros lugares
            else:
                # Esto no debería ocurrir si el selectbox funciona bien
                st.error(f"No se encontró la tarea con ID {selected_task_id_assign}. Algo fue mal.")


# --- Pestaña de Gantt ---
with tab_gantt:
    st.header("📊 Diagrama de Gantt Interactivo")

    # Verifica si hay tareas y si las fechas necesarias son válidas
    # Usa el DataFrame recalculado al principio de la ejecución
    if not tasks_df.empty and 'end_date' in tasks_df.columns and tasks_df['start_date'].notna().all() and tasks_df['end_date'].notna().all():
        gantt_df_source = tasks_df.copy() # Trabaja con una copia

        # Asegura columnas y formatea para hover
        gantt_df_source['macro'] = gantt_df_source['macro'].fillna("Sin Fase").astype(str)
        gantt_df_source['phase_color'] = gantt_df_source['macro'].apply(lambda m: st.session_state.macrotasks.get(m, "#CCCCCC"))
        gantt_df_source['assignments_display'] = gantt_df_source['assignments'].apply(format_assignments_display)
        gantt_df_source['dependencies_display'] = gantt_df_source['dependencies'].apply(
            lambda d: format_dependencies_display(d, st.session_state.tasks)
        )

        # Crea el mapa de colores para las macros/fases
        macro_colors = gantt_df_source.set_index('macro')['phase_color'].to_dict()

        # Prepara datos para Plotly con segmentación
        plotly_data = []
        exclude_weekends_gantt = st.session_state.config.get('exclude_weekends', True)

        for _, row in gantt_df_source.iterrows():
             # Verifica validez antes de segmentar
             if isinstance(row['start_date'], datetime.date) and isinstance(row['duration'], (int, float)) and row['duration'] > 0:
                 segments = get_working_segments(row['start_date'], row['duration'], exclude_weekends_gantt)
                 for seg_start, seg_end in segments:
                      new_row = row.to_dict() # Copia la fila
                      new_row['plotly_start'] = seg_start
                      new_row['plotly_end'] = seg_end + datetime.timedelta(days=1) # Fin + 1
                      plotly_data.append(new_row)
             else:
                  logging.warning(f"Gantt: Skipping task ID {row['id']} ({row['name']}) due to invalid start_date or duration.")


        if plotly_data:
             segments_df = pd.DataFrame(plotly_data)
             segments_df = segments_df.sort_values(by='plotly_start') # Ordena

             # Crea la figura del Gantt
             fig = px.timeline(
                 segments_df,
                 x_start="plotly_start",
                 x_end="plotly_end",
                 y="name", # Usa el nombre completo
                 color="macro",
                 color_discrete_map=macro_colors,
                 title="Cronograma del Proyecto",
                 hover_name="name",
                 hover_data={ # Configura hover
                     "start_date": "|%Y-%m-%d", "end_date": "|%Y-%m-%d",
                     "duration": True, "assignments_display": True,
                     "dependencies_display": True, "status": True,
                     "cost": ":.2f€", "notes": True,
                     "plotly_start": False, "plotly_end": False, "macro": False,
                     "phase_color": False, "assignments": False, "dependencies": False,
                     "subtask": False
                 },
                 custom_data=["id"]
             )

             # Actualiza layout
             fig.update_layout(
                 xaxis_title="Fecha", yaxis_title="Tareas", legend_title_text="Macro/Fase",
                 yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
                 xaxis=dict(tickformat="%d-%b\n%Y"), title_x=0.5
             )

             # Muestra el gráfico
             st.plotly_chart(fig, use_container_width=True)
        else:
             st.info("No se pudieron generar segmentos de tareas para el Gantt (verifica fechas y duraciones).")

    elif not tasks_df.empty:
         st.warning("Faltan datos de fechas válidas en algunas tareas para generar el diagrama de Gantt.")
    else:
        st.info("Añade tareas al plan para visualizar el diagrama de Gantt.")


# --- Pestaña de Dependencias ---
with tab_deps:
    st.header("🔗 Visualización de Dependencias (Grafo)")
    if not tasks_df.empty:
        try:
            # Crea un objeto Digraph de Graphviz
            dot = graphviz.Digraph(comment='Diagrama de Dependencias del Proyecto')
            dot.attr(rankdir='LR') # Orientación LR

            task_list_for_graph = st.session_state.tasks # Usa lista del estado
            # Colores para nodos
            status_colors_graph = {
                "Pendiente": "lightblue", "En Progreso": "orange",
                "Completada": "lightgreen", "Bloqueada": "lightcoral",
                "Pendiente (Error Dep?)": "lightgrey"
            }
            # IDs válidos
            valid_ids_for_graph = {task['id'] for task in task_list_for_graph}

            # Añade nodos (tareas)
            for task in task_list_for_graph:
                assign_display = format_assignments_display(task.get('assignments', []))
                # Etiqueta HTML-like
                node_label = f'''<{task.get('name', 'Nombre Desconocido')}<BR/>
                                <FONT POINT-SIZE="10">ID: {task.get('id', '?')}<BR/>
                                Dur: {task.get('duration', '?')}d | Estado: {task.get('status', 'N/A')}<BR/>
                                Asig: {assign_display}</FONT>>'''
                node_color = status_colors_graph.get(task.get('status', 'Pendiente'), 'lightgrey')
                dot.node(
                    str(task['id']), label=node_label, shape='box',
                    style='filled', fillcolor=node_color
                 )

            # Añade arcos (dependencias)
            for task in task_list_for_graph:
                dependencies = parse_dependencies(task.get('dependencies', '[]'))
                for dep_id in dependencies:
                    if dep_id in valid_ids_for_graph:
                        dot.edge(str(dep_id), str(task['id'])) # Crea arco
                    else:
                        logging.warning(f"Graph Dep Warning: Dependency ID {dep_id} not found for edge to task {task['id']} ({task.get('name')})")

            # Muestra el gráfico
            st.graphviz_chart(dot, use_container_width=True)

        except ImportError:
             st.error("La librería 'graphviz' no está instalada o configurada. No se puede mostrar el grafo.")
             st.code("pip install graphviz")
             st.info("Puede que necesites instalar Graphviz en tu sistema: https://graphviz.org/download/")
        except Exception as e:
            st.error(f"Error inesperado al generar el gráfico de dependencias: {e}")
            logging.error(f"Dependency graph error: {e}", exc_info=True)
    else:
        st.info("Añade tareas y define dependencias para visualizar el grafo.")


# --- Pestaña de Recursos ---
with tab_resources:
    st.header("👥 Carga de Trabajo por Recurso")

    # Verifica si hay tareas y fechas válidas en el DataFrame calculado
    if (not tasks_df.empty and 'end_date' in tasks_df.columns and
            tasks_df['start_date'].notna().all() and
            tasks_df['end_date'].notna().all()):

        min_date = tasks_df['start_date'].min() # Fecha más temprana
        max_date = tasks_df['end_date'].max()   # Fecha más tardía

        # Continúa solo si las fechas son válidas
        if isinstance(min_date, datetime.date) and isinstance(max_date, datetime.date) and min_date <= max_date:
            load_data = [] # Lista para carga diaria
            # Itera sobre cada tarea
            for _, task in tasks_df.iterrows():
                start = task['start_date']
                end = task['end_date']
                assignments = parse_assignments(task.get('assignments', [])) # Parsea

                # Procesa solo si hay fechas y asignaciones válidas
                if isinstance(start, datetime.date) and isinstance(end, datetime.date) and start <= end and assignments:
                    try:
                        task_dates = pd.date_range(start, end, freq='D')
                    except ValueError as e:
                         logging.error(f"Resource Load: Error creating date range for task {task['id']} ({task['name']}) from {start} to {end}: {e}")
                         continue # Salta tarea

                    # Itera sobre cada día de la tarea
                    for date in task_dates:
                        day_name = date.strftime("%A")
                        daily_hours_capacity = st.session_state.config['working_hours'].get(day_name, 0.0)
                        is_weekend = date.weekday() >= 5

                        # Salta si finde excluido o capacidad 0
                        if (st.session_state.config.get('exclude_weekends', True) and is_weekend) or daily_hours_capacity <= 0:
                            continue

                        # Itera sobre asignaciones
                        for assign in assignments:
                            role = assign.get('role')
                            allocation = assign.get('allocation')
                            if role and allocation is not None and allocation > 0:
                                load = daily_hours_capacity * (allocation / 100.0)
                                load_data.append({
                                    'Fecha': date, 'Rol': role, 'Carga (h)': load,
                                    'Tarea': task['name'], 'ID Tarea': task['id']
                                })

            # Si se generaron datos de carga
            if load_data:
                load_df = pd.DataFrame(load_data)
                # Agrupa por fecha y rol
                load_summary = load_df.groupby(['Fecha', 'Rol'])['Carga (h)'].sum().reset_index()
                load_summary = load_summary.sort_values(by=['Fecha', 'Rol']) # Ordena

                # --- Gráfico de Carga Diaria ---
                st.subheader("📈 Carga Diaria Estimada por Rol vs Capacidad")

                # Calcula capacidad máxima diaria
                dates_range_capacity = pd.date_range(min_date, max_date, freq='D')
                capacity_list = []
                for d in dates_range_capacity:
                    day_name = d.strftime("%A")
                    max_hours = st.session_state.config['working_hours'].get(day_name, 0.0)
                    if st.session_state.config.get('exclude_weekends', True) and d.weekday() >= 5:
                         max_hours = 0.0
                    capacity_list.append({"Fecha": d, "Capacidad (h)": max_hours})
                capacity_df = pd.DataFrame(capacity_list)
                capacity_df_filtered = capacity_df[capacity_df['Capacidad (h)'] > 0] # Filtra días sin capacidad

                # Crea gráfico de barras
                fig_load = px.bar(
                    load_summary, x='Fecha', y='Carga (h)', color='Rol',
                    title='Carga de Trabajo Estimada por Rol (Horas Diarias)',
                    labels={'Carga (h)': 'Horas de Trabajo Estimadas'},
                    hover_name='Rol', hover_data={'Fecha': '|%Y-%m-%d', 'Carga (h)': ':.1f h'}
                )

                # Añade línea de capacidad
                if not capacity_df_filtered.empty:
                    fig_load.add_scatter(
                        x=capacity_df_filtered['Fecha'], y=capacity_df_filtered['Capacidad (h)'],
                        mode='lines', name='Capacidad Máxima Diaria',
                        line=dict(dash='dash', color='red', width=2), hoverinfo='skip'
                    )

                # Ajusta layout
                fig_load.update_layout(
                    xaxis_title="Fecha", yaxis_title="Horas de Trabajo",
                    legend_title="Rol / Capacidad", barmode='stack', title_x=0.5
                )
                fig_load.update_xaxes(tickformat="%d-%b\n%Y")

                # Muestra gráfico
                st.plotly_chart(fig_load, use_container_width=True)

                # --- Resumen Carga Total ---
                st.subheader("📊 Resumen Carga Total (Horas-Persona Estimadas)")
                total_hours_summary = load_df.groupby('Rol')['Carga (h)'].sum().reset_index()
                total_hours_summary.rename(columns={'Carga (h)': 'Horas Estimadas Totales'}, inplace=True)
                total_hours_summary = total_hours_summary.sort_values(by='Horas Estimadas Totales', ascending=False)

                # Muestra tabla resumen
                st.dataframe(
                    total_hours_summary.style.format({'Horas Estimadas Totales': '{:,.1f} h'}),
                    use_container_width=True, hide_index=True
                )
            else:
                st.info("No se generaron datos de carga de trabajo (verifica asignaciones, fechas, horas/día).")
        else:
            st.warning("No se pueden determinar las fechas de inicio/fin del proyecto para calcular la carga.")
    elif not tasks_df.empty:
         st.warning("Faltan datos de fechas válidas en algunas tareas para calcular la carga.")
    else:
        st.info("Añade tareas válidas con asignaciones para visualizar la carga de trabajo.")


# --- Pestaña de Costes ---
with tab_costs:
    st.header("💰 Resumen de Costes Estimados")

    # Verifica si hay tareas y costes calculados en el DataFrame principal
    if not tasks_df.empty and 'cost' in tasks_df.columns and tasks_df['cost'].notna().any():

        # --- Cálculo y Muestra de Costes Totales ---
        total_gross_cost = tasks_df['cost'].sum() # Coste bruto total
        profit_margin_percent = st.session_state.config.get('profit_margin_percent', 0.0) # Obtiene margen
        profit_amount = total_gross_cost * (profit_margin_percent / 100.0) # Calcula beneficio
        total_selling_price = total_gross_cost + profit_amount # Calcula precio venta

        st.subheader("Resumen Financiero General")
        cost_cols = st.columns(4) # Columnas para métricas
        with cost_cols[0]:
            st.metric(label="Coste Bruto Total Estimado", value=f"€ {total_gross_cost:,.2f}")
        with cost_cols[1]:
            st.metric(label="Margen de Beneficio", value=f"{profit_margin_percent:.2f} %")
        with cost_cols[2]:
            st.metric(label="Beneficio Estimado", value=f"€ {profit_amount:,.2f}")
        with cost_cols[3]:
            st.metric(label="Precio Venta Estimado", value=f"€ {total_selling_price:,.2f}")

        st.divider()

        # --- Desglose de Costes por Rol ---
        st.subheader("Desglose Costes por Rol")
        cost_by_role_data = []
        # Recalcula coste por rol (necesario si tarifas cambiaron)
        for _, task in tasks_df.iterrows():
            assignments = parse_assignments(task.get('assignments', []))
            start = task['start_date']
            end = task['end_date']
            if isinstance(start, datetime.date) and isinstance(end, datetime.date) and start <= end and assignments:
                task_hours = compute_task_working_hours(
                    start, end, st.session_state.config['working_hours'], st.session_state.config['exclude_weekends']
                )
                for assign in assignments:
                    role = assign.get('role')
                    allocation = assign.get('allocation')
                    if role and allocation is not None and allocation > 0:
                        hourly_rate = get_role_rate(role) # Usa tarifa actual del estado
                        role_hours = task_hours * (allocation / 100.0)
                        role_cost = role_hours * hourly_rate
                        cost_by_role_data.append({'Rol': role, 'Coste (€)': role_cost})

        if cost_by_role_data:
            cost_by_role_df = pd.DataFrame(cost_by_role_data)
            cost_by_role_summary = cost_by_role_df.groupby('Rol')['Coste (€)'].sum().reset_index()
            cost_by_role_summary = cost_by_role_summary.sort_values(by='Coste (€)', ascending=False) # Ordena

            # Muestra tabla y gráfico
            col_cost_table, col_cost_chart = st.columns([0.6, 0.4])
            with col_cost_table:
                 st.write("**Coste Total por Rol**")
                 st.dataframe(
                    cost_by_role_summary.style.format({'Coste (€)': '€ {:,.2f}'}),
                    use_container_width=True, hide_index=True
                 )
            with col_cost_chart:
                if not cost_by_role_summary.empty and cost_by_role_summary['Coste (€)'].sum() > 0:
                    fig_pie = px.pie(
                        cost_by_role_summary, values='Coste (€)', names='Rol',
                        title='Distribución Coste por Rol', hole=0.3
                    )
                    fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                    fig_pie.update_layout(showlegend=False, title_x=0.5, margin=dict(l=0, r=0, t=30, b=0))
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.info("No hay costes positivos para mostrar gráfico.")
        else:
            st.info("No se pudo calcular desglose costes por rol.")

        st.divider()

        # --- Desglose de Costes por Tarea ---
        st.subheader("Desglose de Costes por Tarea")

        # Prepara DataFrame base con costes recalculados por si acaso
        cost_by_task_df = tasks_df[['id', 'macro', 'subtask', 'cost']].copy()
        cost_by_task_df.rename(columns={'cost': 'Coste Estimado (€)'}, inplace=True)

        # --- Filtros ---
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            unique_macros = sorted(cost_by_task_df['macro'].unique())
            selected_macros = st.multiselect(
                "Filtrar por Macro/Fase:", options=unique_macros, default=[], key="filter_macro_cost"
            )
        with filter_col2:
             unique_subtasks = sorted(cost_by_task_df['subtask'].unique())
             selected_subtasks = st.multiselect(
                 "Filtrar por Subtarea:", options=unique_subtasks, default=[], key="filter_subtask_cost"
             )

        # Aplica filtros
        filtered_cost_df = cost_by_task_df.copy()
        if selected_macros:
            filtered_cost_df = filtered_cost_df[filtered_cost_df['macro'].isin(selected_macros)]
        if selected_subtasks:
            filtered_cost_df = filtered_cost_df[filtered_cost_df['subtask'].isin(selected_subtasks)]

        # Ordena y muestra tabla filtrada
        filtered_cost_df = filtered_cost_df.sort_values(by='Coste Estimado (€)', ascending=False)
        st.dataframe(
            filtered_cost_df[['macro', 'subtask', 'Coste Estimado (€)']].style.format({'Coste Estimado (€)': '€ {:,.2f}'}),
            use_container_width=True, hide_index=True,
            column_config={"macro": "Macro/Fase", "subtask": "Subtarea"}
        )

        # Muestra coste total filtrado
        total_filtered_cost = filtered_cost_df['Coste Estimado (€)'].sum()
        st.info(f"**Coste Total de Tareas Filtradas:** € {total_filtered_cost:,.2f}")


    elif not tasks_df.empty:
         st.warning("No se pudieron calcular los costes (verifica tareas, asignaciones, tarifas, fechas).")
    else:
        st.info("Añade tareas con asignaciones y define tarifas para los roles para ver costes.")

