import json
import datetime
import pandas as pd
from collections import defaultdict
import logging
import math
import numpy as np
import argparse

# Configurar logging básico - Cambiar a DEBUG para ver más detalle
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Funciones Auxiliares ---

def parse_input_data(json_str):
    """Parsea el JSON, valida y prepara datos. Mantiene duration original."""
    try:
        data = json.loads(json_str)
        # Validaciones básicas
        if not all(k in data for k in ['roles', 'tasks', 'config', 'macrotasks']): raise ValueError("Faltan claves principales.")
        if not isinstance(data['roles'], dict) or not isinstance(data['tasks'], list) or not isinstance(data['config'], dict): raise ValueError("Tipos incorrectos.")
        if 'working_hours' not in data['config'] or not isinstance(data['config']['working_hours'], dict): raise ValueError("Falta/incorrecto 'working_hours'.")

        # Procesar tareas
        for task in data['tasks']:
            # Dependencias
            dep_str = task.get('dependencies', '[]')
            if isinstance(dep_str, str):
                try: deps = json.loads(dep_str); task['dependencies'] = [int(d) for d in deps if isinstance(d, (int, str)) and str(d).isdigit()]
                except (json.JSONDecodeError, ValueError): task['dependencies'] = []
            elif isinstance(dep_str, list): task['dependencies'] = [int(d) for d in dep_str if isinstance(d, (int, str)) and str(d).isdigit()]
            else: task['dependencies'] = []

            # Asignaciones
            assign_input = task.get('assignments', []); valid_assignments = []
            assignments_list = []
            if isinstance(assign_input, str) and assign_input.strip():
                try: assignments_list = json.loads(assign_input)
                except (json.JSONDecodeError, TypeError): pass
            elif isinstance(assign_input, list): assignments_list = assign_input
            if isinstance(assignments_list, list):
                for assign in assignments_list:
                    if isinstance(assign, dict) and 'role' in assign and 'allocation' in assign:
                        try:
                            allocation = float(assign['allocation'])
                            if 0 <= allocation <= 100: valid_assignments.append({'role': assign['role'], 'allocation': allocation})
                        except (ValueError, TypeError): continue
            task['assignments'] = valid_assignments

            # Mantener duration original (días laborables)
            task['duration'] = int(task.get('duration', 1))
            if task['duration'] <= 0: task['duration'] = 1

            task['start_date'] = None; task['end_date'] = None
        return data
    except json.JSONDecodeError as e: logging.error(f"Error JSON en línea {e.lineno} col {e.colno}: {e.msg}"); raise
    except ValueError as e: logging.error(f"Error estructura JSON: {e}"); raise
    except Exception as e: logging.error(f"Error parseando datos: {e}"); raise

def calculate_end_date_variable_hours(start_date, duration_days, working_hours_config):
    """Calcula la fecha de fin sumando N días laborables."""
    if not isinstance(start_date, datetime.date) or not isinstance(duration_days, (int, float)) or duration_days <= 0: return None
    days_to_add = int(duration_days); current_date = start_date; work_days_counted = 0
    sim_days = 0; max_sim = duration_days * 7
    while work_days_counted < days_to_add and sim_days < max_sim:
        sim_days+=1
        day_name = current_date.strftime("%A")
        if working_hours_config.get(day_name, 0) > 0: work_days_counted += 1
        if work_days_counted == days_to_add: break
        current_date += datetime.timedelta(days=1)
    if work_days_counted < days_to_add:
        logging.error(f"Timeout calculando fecha fin: start={start_date}, duration={duration_days}")
        return None # No se pudo calcular en un tiempo razonable
    return current_date

def check_hourly_availability(task_id, task_name, task_start_date, task_duration, task_assignments, schedule_hours, max_available_hours_per_day, working_hours_config):
    """
    Verifica estrictamente si la suma de horas asignadas excede las horas disponibles del rol.
    """
    task_end_date = calculate_end_date_variable_hours(task_start_date, task_duration, working_hours_config)
    if task_end_date is None:
        logging.warning(f"[Check T{task_id}] Fecha fin inválida ({task_start_date}, {task_duration}).")
        return False # No se puede verificar si no hay fecha fin

    current_date = task_start_date
    logging.debug(f"[Check T{task_id} '{task_name}'] Disponibilidad? {task_start_date} -> {task_end_date} ({task_duration}d)")
    while current_date <= task_end_date:
        day_name = current_date.strftime("%A")
        daily_working_hours = working_hours_config.get(day_name, 0)

        if daily_working_hours > 0: # Solo verificar días laborables
            current_daily_scheduled_hours = schedule_hours.get(current_date, defaultdict(float))
            logging.debug(f"  [Check T{task_id} @ {current_date}] Horas ya asignadas: {dict(current_daily_scheduled_hours)}")

            # Verificar cada rol de la tarea que se intenta planificar
            for assignment in task_assignments:
                role = assignment['role']
                allocation_pct = assignment['allocation']
                if allocation_pct <= 0: continue # No consume recursos

                # Horas que la tarea añadiría para el rol en ese día
                task_hourly_load_role_day = daily_working_hours * (allocation_pct / 100.0)

                # Horas que ESTE rol ya tiene asignadas de OTRAS tareas en ESTE día
                current_role_hours_day = current_daily_scheduled_hours.get(role, 0.0)

                # Horas MÁXIMAS que ESTE rol puede trabajar en ESTE día
                max_role_hours_day = max_available_hours_per_day.get(role, {}).get(day_name, 0.0)

                # *** Comprobación Estricta ***
                new_total_hours = current_role_hours_day + task_hourly_load_role_day
                tolerance = 1e-9 # Pequeña tolerancia para floats

                logging.debug(f"    [Check T{task_id} @ {current_date} - {role}] Actual: {current_role_hours_day:.4f}h, Nueva Tarea: {task_hourly_load_role_day:.4f}h, Total Propuesto: {new_total_hours:.4f}h, Límite Rol: {max_role_hours_day:.4f}h")

                if new_total_hours > max_role_hours_day + tolerance:
                    logging.info(f"  [Check T{task_id} @ {current_date} - {role}] CONFLICT! Total Propuesto ({new_total_hours:.4f}) > Límite Rol ({max_role_hours_day:.4f})")
                    return False # Conflicto -> No hay disponibilidad

        current_date += datetime.timedelta(days=1)

    logging.debug(f"[Check T{task_id}] Disponibilidad OK para {task_start_date} -> {task_end_date}")
    return True # No se encontraron conflictos

def update_hourly_schedule(task_start_date, task_duration, task_assignments, schedule_hours, working_hours_config):
    """Actualiza el tracker `schedule_hours` sumando las horas específicas."""
    task_end_date = calculate_end_date_variable_hours(task_start_date, task_duration, working_hours_config)
    if task_end_date is None: return

    current_date = task_start_date
    logging.debug(f"[Update Sch T?] Actualizando carga {task_start_date} -> {task_end_date}")
    while current_date <= task_end_date:
        day_name = current_date.strftime("%A")
        daily_working_hours = working_hours_config.get(day_name, 0)
        if daily_working_hours > 0:
            if current_date not in schedule_hours: schedule_hours[current_date] = defaultdict(float)
            for assignment in task_assignments:
                role = assignment['role']; allocation_pct = assignment['allocation']
                task_hourly_load_role_day = daily_working_hours * (allocation_pct / 100.0)
                schedule_hours[current_date][role] += task_hourly_load_role_day
                logging.debug(f"  [Update Sch @ {current_date} - {role}] Añadido: {task_hourly_load_role_day:.2f}h -> Nueva Carga Total Día: {schedule_hours[current_date][role]:.2f}h")
        current_date += datetime.timedelta(days=1)

def get_next_working_day(input_date, working_hours_config):
    next_day = input_date
    while True:
        day_name = next_day.strftime("%A");
        if working_hours_config.get(day_name, 0) > 0: return next_day
        next_day += datetime.timedelta(days=1)

# --- Lógica Principal de Replanificación (Planifica 1 tarea por iteración) ---
def replan_project(data):
    """Replanifica proyecto nivelando recursos basado en carga horaria diaria, priorizando por ID."""
    tasks = data['tasks']; roles_config = data['roles']; working_hours_config = data['config']['working_hours']
    max_role_availability_pct = {role: info['availability_percent'] for role, info in roles_config.items()}

    # Calcular horas máximas disponibles por rol y día
    max_available_hours_per_day = defaultdict(dict)
    for role, info in roles_config.items():
        for day, hours in working_hours_config.items():
            max_available_hours_per_day[role][day] = hours * (info['availability_percent'] / 100.0) if hours > 0 else 0

    # Orden inicial por ID (para priorización)
    tasks.sort(key=lambda t: t['id'])

    scheduled_tasks = []; task_end_dates = {}; resource_schedule_hours = {} # Tracker de HORAS
    unscheduled_task_ids = [t['id'] for t in tasks]; task_map = {t['id']: t for t in tasks}
    project_start_date = datetime.date.today()
    logging.info(f"Iniciando replanificación (Nivelación Horaria Estricta v4). Inicio: {project_start_date}"); logging.info(f"Disponibilidad Max (Horas): {max_available_hours_per_day}"); logging.info(f"Horas Laborables: {working_hours_config}")

    MAX_ITERATIONS = len(tasks) * 10 # Aumentar límite por si la nivelación requiere muchos reintentos
    current_iteration = 0

    # Bucle principal: Planifica UNA tarea por iteración
    while unscheduled_task_ids and current_iteration < MAX_ITERATIONS:
        current_iteration += 1; scheduled_in_iteration = False
        ready_to_schedule_ids = []
        # Identificar TODAS las tareas listas en este punto
        for task_id in unscheduled_task_ids:
            task = task_map[task_id]; dependencies = task['dependencies']
            if all(dep_id in task_end_dates for dep_id in dependencies):
                ready_to_schedule_ids.append(task_id)

        if not ready_to_schedule_ids:
             if unscheduled_task_ids: logging.error(f"Ciclo o error en iter {current_iteration}. IDs pendientes: {unscheduled_task_ids}")
             break # Salir si no hay nada que planificar

        # Priorizar tareas listas por ID (orden original)
        ready_to_schedule_ids.sort()
        task_id_to_schedule = ready_to_schedule_ids[0] # Intentar planificar la primera

        task = task_map[task_id_to_schedule]; dependencies = task['dependencies']; duration = task['duration']; assignments = task['assignments']
        latest_dependency_finish = None
        if dependencies:
            try: latest_dependency_finish = max(task_end_dates[dep_id] for dep_id in dependencies)
            except KeyError as e: logging.error(f"Error crítico: Falta fecha fin dep {e} Tarea {task_id_to_schedule}. Abortando."); break
        earliest_start_dep = project_start_date
        if latest_dependency_finish: earliest_start_dep = latest_dependency_finish + datetime.timedelta(days=1)
        current_check_date = get_next_working_day(earliest_start_dep, working_hours_config)

        # Encontrar primer hueco basado en disponibilidad HORARIA
        found_slot = False; attempts = 0; MAX_ATTEMPTS_SLOT = 365 * 5 # Límite búsqueda 5 años
        logging.debug(f"Iter {current_iteration}: Intentando planificar Tarea {task_id_to_schedule} ('{task['name']}')")
        while not found_slot and attempts < MAX_ATTEMPTS_SLOT:
            attempts += 1
            # Usar la función de chequeo horario
            if check_hourly_availability(task_id_to_schedule, task['name'], current_check_date, duration, assignments, resource_schedule_hours, max_available_hours_per_day, working_hours_config):
                # Slot encontrado
                task['start_date'] = current_check_date
                task['end_date'] = calculate_end_date_variable_hours(current_check_date, duration, working_hours_config)
                if task['end_date'] is None: logging.error(f"Error calculando fecha fin Tarea {task_id_to_schedule}"); task['end_date'] = current_check_date # Fallback
                # Actualizar el schedule CON LAS HORAS de esta tarea
                update_hourly_schedule(task['start_date'], duration, assignments, resource_schedule_hours, working_hours_config)
                task_end_dates[task_id_to_schedule] = task['end_date'] # Guardar fin para dependencias
                scheduled_tasks.append(task)
                unscheduled_task_ids.remove(task_id_to_schedule) # Quitar de pendientes
                logging.info(f"Iter {current_iteration}: PLANIFICADA Tarea {task_id_to_schedule} ({task['name']}) | Inicio: {task['start_date']} | Fin: {task['end_date']} | Duración: {duration}d")
                found_slot = True; scheduled_in_iteration = True
            else:
                # No disponible, probar siguiente día laborable
                logging.debug(f"  Slot no encontrado en {current_check_date} para T{task_id_to_schedule}. Probando siguiente día.")
                current_check_date = get_next_working_day(current_check_date + datetime.timedelta(days=1), working_hours_config)
        if not found_slot:
             logging.error(f"Iter {current_iteration}: NO SE ENCONTRÓ slot Tarea {task_id_to_schedule} ({task['name']}) tras {attempts} intentos. Se reintentará.")
             # La tarea permanece en unscheduled_task_ids y se reintentará en la siguiente iteración

        if not scheduled_in_iteration and not unscheduled_task_ids: break # Si no quedan tareas, salir
        elif not scheduled_in_iteration and current_iteration >= MAX_ITERATIONS: logging.warning(f"Se alcanzó el límite de iteraciones sin planificar la tarea {task_id_to_schedule}.")


    if unscheduled_task_ids: logging.warning(f"Replanificación finalizada con {len(unscheduled_task_ids)} tareas no planificadas: {unscheduled_task_ids}")
    else: logging.info("Replanificación completada.")
    scheduled_tasks.sort(key=lambda t: t['start_date']) # Ordenar salida final
    output_data = data.copy(); output_data['tasks'] = []; max_end_date = None; project_start_output = None
    for task in scheduled_tasks:
         task_copy = task.copy(); task_copy['start_date'] = task_copy['start_date'].isoformat() if task_copy['start_date'] else None
         task_copy.pop('end_date', None);
         task_copy['duration'] = task['duration'] # Mantener duración original en días
         task_copy['dependencies'] = json.dumps(task_copy['dependencies'])
         if not isinstance(task_copy['assignments'], list): task_copy['assignments'] = []
         output_data['tasks'].append(task_copy)
         task_start_dt = datetime.date.fromisoformat(task_copy['start_date']) if task_copy['start_date'] else None; task_end_dt = task_end_dates.get(task['id'])
         if task_start_dt:
              if project_start_output is None or task_start_dt < project_start_output: project_start_output = task_start_dt
         if task_end_dt:
              if max_end_date is None or task_end_dt > max_end_date: max_end_date = task_end_dt
    duration_days_calendar = (max_end_date - project_start_output).days + 1 if max_end_date and project_start_output else 0; work_days_total = 0
    if project_start_output and max_end_date:
         d = project_start_output
         while d <= max_end_date:
              day_name_calc = d.strftime("%A");
              if working_hours_config.get(day_name_calc, 0) > 0: work_days_total += 1
              d += datetime.timedelta(days=1)
    # Actualizar nota final
    output_data['schedule_notes'] = f"PLAN REPLANIFICADO (NIVELACIÓN HORARIA ESTRICTA v4) ({datetime.date.today()}): Respetando LT {max_role_availability_pct.get('Lider Tecnico', 0)}% / IA {max_role_availability_pct.get('Ingeniero IA', 0)}% MÁXIMO diario y horario laboral variable. Prioridad por ID. Duración total: {work_days_total} días laborables ({duration_days_calendar} días naturales)."
    return output_data

# --- Main Execution Block ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replanifica proyecto JSON con nivelación horaria estricta.")
    parser.add_argument("-i", "--input_file", help="Ruta al archivo JSON de entrada.")
    parser.add_argument("-o", "--output_file", default="replanned_project_hourly_strict_v4.json",
                        help="Ruta al archivo JSON de salida (default: replanned_project_hourly_strict_v4.json).")
    args = parser.parse_args()
    logging.info(f"Leyendo archivo de entrada: {args.input_file}")
    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            input_json_str_from_file = f.read()
        input_data = parse_input_data(input_json_str_from_file)
        replanned_data = replan_project(input_data)
        output_json_str = json.dumps(replanned_data, indent=2, ensure_ascii=False)
        print("\n--- Plan Replanificado (Nivelación Horaria Estricta v4) ---")
        print(output_json_str)
        print("-----------------------------------------------------------\n")
        output_file_path = args.output_file
        with open(output_file_path, "w", encoding="utf-8") as f:
            json.dump(replanned_data, f, indent=2, ensure_ascii=False)
        logging.info(f"Plan replanificado guardado en: '{output_file_path}'")
    except FileNotFoundError: logging.error(f"Error: No se encontró el archivo '{args.input_file}'")
    except json.JSONDecodeError as e: logging.error(f"Error de formato JSON en archivo entrada.")
    except ValueError as e: logging.error(f"Error en datos de entrada: {e}")
    except Exception as e: logging.exception(f"Falló la replanificación: {e}")