"""API REST del solver d'horaris Switch2.

Exposa el pipeline complet (preprocessament + solver CP-SAT) com a servei HTTP.
Desplegable a Vercel (funcions serverless Python) i executable en local amb uvicorn:

    uvicorn api.index:app --reload
"""
import io
import os
import sys
import contextlib
import traceback

# El codi del solver viu a l'arrel del repositori
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from horari_solver import HorariData
from Solver import HorariSolver, genera_json_solucio_compatible

# Límit dur del temps de resolució. A Vercel la funció té un maxDuration
# (configurat a vercel.json); deixem marge per al preprocessament i la resposta.
MAX_TEMPS_SOLVER = float(os.environ.get('MAX_TEMPS_SOLVER', 280))

VERSIO_API = '1.0.0'

app = FastAPI(
    title='API Solver d\'horaris Switch2',
    description=(
        'Servei de generació d\'horaris escolars amb OR-Tools CP-SAT. '
        'Rep un JSON amb format `Solver.json` (professors, mòduls, cursos, aules, '
        'restriccions) i retorna l\'horari resolt. '
        'Consulteu `API_REST.md` i `DOC_API_SOLVER.md` per al detall dels formats.'
    ),
    version=VERSIO_API,
)


# ---------------------------------------------------------------------------
# Models de petició/resposta
# ---------------------------------------------------------------------------

class OpcionsSolve(BaseModel):
    """Opcions d'execució del solver."""
    max_time_seconds: float = Field(
        default=60, gt=0,
        description=f'Límit de temps del solver en segons (màxim {MAX_TEMPS_SOLVER:g}).'
    )
    num_workers: int = Field(
        default=4, ge=1, le=16,
        description='Threads de cerca paral·lela de CP-SAT.'
    )
    incloure_compatible: bool = Field(
        default=False,
        description='Si és cert, inclou la solució en format Solver.json (camp '
                    '`solucio_compatible`), reimportable a l\'editor.'
    )


class PeticioSolve(BaseModel):
    """Cos de la petició de /api/solve."""
    dades: dict[str, Any] = Field(description='Contingut complet amb format Solver.json.')
    opcions: OpcionsSolve = Field(default_factory=OpcionsSolve)


class PeticioValidate(BaseModel):
    """Cos de la petició de /api/validate i /api/preprocess."""
    dades: dict[str, Any] = Field(description='Contingut complet amb format Solver.json.')


class RespostaValidate(BaseModel):
    valid: bool
    advertiments: list[str]
    estadistiques: dict[str, Any]


class RespostaSolve(BaseModel):
    estat: str = Field(description="'OPTIMAL', 'FEASIBLE', 'INFEASIBLE', 'UNKNOWN' o 'MODEL_INVALID'.")
    solucio: Optional[dict[str, Any]] = Field(
        default=None,
        description='Horari resolt (vistes per curs, professor i aula + stats). '
                    'Null si no s\'ha trobat solució.'
    )
    solucio_compatible: Optional[dict[str, Any]] = Field(
        default=None,
        description='Solució en format Solver.json (només si s\'ha demanat).'
    )
    advertiments: list[str] = Field(
        default_factory=list,
        description='Advertiments del validador de dades (no bloquegen la resolució).'
    )


# ---------------------------------------------------------------------------
# Utilitats internes
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _silenci():
    """Captura els prints del solver perquè no inundin els logs del servidor."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def _preprocessa(dades: dict) -> tuple[HorariData, list[str]]:
    """Carrega i preprocessa dades amb format Solver.json.

    Retorna (HorariData, advertiments). Llança HTTPException 422 si les dades
    no tenen l'estructura mínima esperada.
    """
    try:
        hd = HorariData()
        with _silenci():
            hd.carrega_dades(dades)
        advertiments = hd.valida_dades()
        return hd, advertiments
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise HTTPException(
            status_code=422,
            detail={
                'error': 'Dades amb format invàlid (esperat format Solver.json).',
                'detall': f'{type(e).__name__}: {e}',
                'traca': traceback.format_exc(limit=3),
            },
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get('/', include_in_schema=False)
def arrel():
    """Redirigeix a la documentació interactiva."""
    return RedirectResponse(url='/docs')


@app.get('/api/health', summary='Estat del servei')
def health() -> dict:
    """Comprova que el servei i OR-Tools estan operatius."""
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()  # instanciació mínima per verificar la llibreria
    return {
        'estat': 'ok',
        'versio_api': VERSIO_API,
        'max_temps_solver': MAX_TEMPS_SOLVER,
        'python': sys.version.split()[0],
    }


@app.post('/api/validate', response_model=RespostaValidate,
          summary='Valida un Solver.json sense resoldre')
def validate(peticio: PeticioValidate) -> RespostaValidate:
    """Carrega les dades, executa les validacions i retorna advertiments i estadístiques.

    No construeix ni resol el model: és ràpid i serveix per detectar errors
    d'estructura (422) o incoherències de dades (llista `advertiments`).
    """
    hd, advertiments = _preprocessa(peticio.dades)
    stats = hd.get_estadistiques()
    # subgrups_per_curs conté sets; fer-ho serialitzable
    stats['subgrups_per_curs'] = {str(k): sorted(v) for k, v in stats['subgrups_per_curs'].items()}
    return RespostaValidate(
        valid=len(advertiments) == 0,
        advertiments=advertiments,
        estadistiques=stats,
    )


@app.post('/api/preprocess', summary='Retorna les dades preprocessades (debug)')
def preprocess(peticio: PeticioValidate) -> dict:
    """Retorna el JSON intermedi `dades_solver_processades` que consumeix el solver.

    Útil per depurar què ha entès el preprocessador (mòduls especials detectats,
    tutories, agrupacions...).
    """
    hd, advertiments = _preprocessa(peticio.dades)
    return {
        'dades_processades': hd.genera_dades_processades(),
        'advertiments': advertiments,
    }


@app.post('/api/solve', response_model=RespostaSolve, response_model_exclude_none=True,
          summary='Resol l\'horari')
def solve(peticio: PeticioSolve) -> RespostaSolve:
    """Executa el pipeline complet: preprocessament → model CP-SAT → solució.

    L'estat de la resposta indica el resultat:
    - `OPTIMAL` / `FEASIBLE`: hi ha solució al camp `solucio`.
    - `INFEASIBLE`: les restriccions es contradiuen; cap solució possible.
    - `UNKNOWN`: s'ha esgotat el temps sense trobar solució (proveu més temps).
    - `MODEL_INVALID`: error intern construint el model.
    """
    opcions = peticio.opcions
    max_time = min(opcions.max_time_seconds, MAX_TEMPS_SOLVER)

    hd, advertiments = _preprocessa(peticio.dades)
    dades_processades = hd.genera_dades_processades()

    try:
        with _silenci():
            solver = HorariSolver(dades_processades)
            solucio = solver.executar(
                max_time_seconds=max_time,
                num_workers=opcions.num_workers,
                log_search_progress=False,
                output_path=None,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                'error': 'Error intern construint o resolent el model.',
                'detall': f'{type(e).__name__}: {e}',
                'traca': traceback.format_exc(limit=5),
            },
        )

    if solucio is None:
        return RespostaSolve(estat=solver.ultim_estat or 'UNKNOWN', advertiments=advertiments)

    solucio_compatible = None
    if opcions.incloure_compatible:
        with _silenci():
            solucio_compatible = genera_json_solucio_compatible(
                solucio, template=peticio.dades, output_path=None,
            )

    return RespostaSolve(
        estat=solucio['stats']['estat'],
        solucio=solucio,
        solucio_compatible=solucio_compatible,
        advertiments=advertiments,
    )
