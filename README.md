# 🚀 Project Planner - Advanced Resource & Task Scheduler

![Banner](https://img.shields.io/badge/status-active-brightgreen)
![Made with Python](https://img.shields.io/badge/Made%20with-Python-blue)
![Streamlit App](https://img.shields.io/badge/Frontend-Streamlit-ff69b4)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

> **Visual project planning, resource leveling, cost estimation and Gantt charts — all in one app.**

---

## 🧠 Overview

**Project Planner** is a powerful Streamlit-based application designed for project managers, engineers, and team leaders. It simplifies complex project planning by integrating:
- Task scheduling with dependency tracking
- Role-based resource assignments
- Hour-by-hour resource leveling
- Interactive Gantt charts
- Cost estimation per task and role

---

## 🌟 Features

### ✅ Task Management
- Add, edit, and organize tasks into macro-phases
- Define dependencies between tasks
- Assign resources and effort (% allocation)

### 📅 Intelligent Scheduling
- Auto-reschedules tasks respecting role availability and daily working hours
- Takes into account dependencies and avoids weekends (if configured)

### 📈 Interactive Gantt Charts
- Timeline view with colored macro-tasks
- Hover tooltips show duration, assignments, cost, and dependencies

### 🔗 Dependency Graph
- Visualizes task dependencies as a flow chart using Graphviz

### 👥 Resource Load Visualization
- See daily hour load per role and total work-hour estimates

### 💰 Cost Analysis
- Real-time cost breakdown by role and by task
- Define hourly rates per role

---

## 🛠️ How It Works

This app is built around two core components:

- **Streamlit UI** (`project_planner.py`)  
  The main interface where users create, import/export, and visualize the project.

- **Back-End Scheduler** (`gant_generator.py`)  
  A sophisticated algorithm that parses JSON data and reschedules tasks using daily availability and dependency resolution.

---

## 🖼️ Screenshots

### 📊 Gantt Chart View  
> Colored by macro-phase, shows start/end, assignments, and dependencies.

### 🔗 Task Dependency Diagram  
> Auto-generated flowchart of task order and requirements.

### 💼 Resource Allocation View  
> Daily workload distribution for each role.

### 💰 Cost Breakdown  
> Understand where your budget is going in a simple and clear format.

---

## 🧩 JSON Structure

Your project plan can be saved/exported as a JSON file. It includes:
- `roles`: Name, availability (%) and hourly rate (€)
- `tasks`: ID, name, duration, dependencies, and assignments
- `config`: Working hours per weekday
- `macrotasks`: Macro-phase names and their colors

Example:
```json
{
  "roles": {
    "AI Engineer": {
      "availability_percent": 100,
      "rate_eur_hr": 40
    }
  },
  "tasks": [
    {
      "id": 1,
      "name": "Research",
      "duration": 5,
      "dependencies": [],
      "assignments": [{"role": "AI Engineer", "allocation": 100}]
    }
  ]
}
```



## ▶️ How to Run

1. **Clone the repository**  
   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
   cd YOUR_REPO
   ```

2. **Install dependencies**  
   Make sure Python 3.8+ and pip are installed.
   ```bash
   pip install -r requirements.txt
   ```

3. **Launch the app**
   ```bash
   streamlit run project_planner.py
   ```

4. (Optional) **Run the scheduling engine manually**  
   ```bash
   python gant_generator.py -i input_project.json -o output_project.json
   ```



## 🧪 Demo Templates

Includes a sample AI project template with:
- 2 roles: "Lider Tecnico", "Ingeniero IA"
- Dependencies, assignments, and cost structures

Quickly load it from the app interface and start customizing!



## 📂 Folder Structure


.
├── project_planner.py       # Streamlit interface
├── gant_generator.py        # Scheduling logic
├── requirements.txt         # Dependencies
└── README.md                # This file
```



## 📝 License

This project is licensed under the [MIT License](LICENSE).


## ❤️ Credits

If you found this useful, please ⭐ the repo and share with others!

---