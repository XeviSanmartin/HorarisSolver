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

# Límit dur de temps de resolució (segons); en un servidor propi pot ser alt
ENV MAX_TEMPS_SOLVER=7200
# Orígens permesos per a peticions des del navegador (separats per comes)
ENV CORS_ORIGINS=*

EXPOSE 8000

CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
