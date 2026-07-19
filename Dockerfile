# API del solver d'horaris per a desplegament persistent (Proxmox, servidor propi).
#
#   docker build -t horaris-solver .
#   docker run -d -p 8000:8000 -e MAX_TEMPS_SOLVER=7200 horaris-solver
#
# IMPORTANT: un sol procés (sense --workers): el registre de feines asíncrones
# (/api/jobs) viu en memòria i no es comparteix entre processos.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt uvicorn

COPY horari_solver.py Solver.py exportar_html.py ./
COPY api/ api/

# Barrera dura absoluta de temps (segons). L'app tria el temps real via
# opcions.max_time_seconds ("Temps màxim"); el servidor aplica min(app, això).
# Amb 24 h de backstop el valor de l'app sempre mana (només és una protecció).
ENV MAX_TEMPS_SOLVER=86400
# Orígens permesos per a peticions des del navegador (separats per comes)
ENV CORS_ORIGINS=*

EXPOSE 8000

CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
