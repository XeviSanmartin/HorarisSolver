"""API REST del solver d'horaris Switch2.

Exposa el pipeline complet (preprocessament + solver CP-SAT) com a servei HTTP.
Desplegable a Vercel (funcions serverless Python) i executable en local amb uvicorn:

    uvicorn api.index:app --reload

La documentació canònica de l'API és l'especificació OpenAPI generada per
FastAPI: interactiva a /docs (Swagger UI) i /redoc, i en JSON a /openapi.json.
Una còpia estàtica es manté al repositori (openapi.json) via
scripts/exporta_openapi.py.
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
from pydantic import BaseModel, ConfigDict, Field

from horari_solver import HorariData
from Solver import HorariSolver, genera_json_solucio_compatible

# Límit dur del temps de resolució. A Vercel la funció té un maxDuration
# (configurat a vercel.json); deixem marge per al preprocessament i la resposta.
MAX_TEMPS_SOLVER = float(os.environ.get('MAX_TEMPS_SOLVER', 280))

VERSIO_API = '1.2.0'

DESCRIPCIO = """
Servei de generació d'horaris escolars amb [OR-Tools CP-SAT](https://developers.google.com/optimization).

## Flux

1. **`POST /api/validate`** — valida el JSON d'entrada (ràpid, sense resoldre).
2. **`POST /api/solve`** — preprocessa, construeix el model CP-SAT i resol.
3. Opcionalment, **`POST /api/preprocess`** per inspeccionar el JSON intermedi (debug).

## Format d'entrada

El camp `dades` de totes les peticions és el contingut complet d'un fitxer
**`Solver.json`** de l'editor Switch2: professors (amb desiderata i assignacions
de mòduls), catàleg de mòduls, cursos, aules, especialitats i restriccions
opcionals (mòduls coordinats, projectes). Cada camp està descrit a l'apartat
**Schemas** d'aquesta especificació.

Convencions temporals: `dia` 0–4 (dilluns–divendres), `hora` 0–12 (08:00–20:00;
el solver només programa les hores 0–10), `subgrup` 1 (1r mig grup), 2 (2n mig
grup) o 3 (grup sencer).

## Estats del resultat de `/api/solve`

| `estat` | Significat | `solucio` |
|---|---|---|
| `OPTIMAL` | Solució òptima demostrada | present |
| `FEASIBLE` | Solució vàlida dins del temps límit (pot no ser òptima) | present |
| `INFEASIBLE` | Les restriccions es contradiuen: cap horari possible | absent |
| `UNKNOWN` | Temps esgotat sense conclusió: reintenteu amb més temps | absent |
| `MODEL_INVALID` | Error intern construint el model | absent |

`INFEASIBLE` i `UNKNOWN` **no** són errors HTTP: retornen `200`.

## Funció objectiu

El solver minimitza `10 × hores_mortes + 20 × preferències_no_respectades`
(desiderata `tipus: 1`). El valor final és a `solucio.stats.objectiu`.
"""

TAGS_OPENAPI = [
    {'name': 'Servei', 'description': 'Estat i metadades del servei.'},
    {'name': 'Validació', 'description': 'Validació i preprocessament de dades sense resoldre.'},
    {'name': 'Resolució', 'description': 'Construcció i resolució del model CP-SAT.'},
]

app = FastAPI(
    title="API Solver d'horaris Switch2",
    description=DESCRIPCIO,
    version=VERSIO_API,
    openapi_tags=TAGS_OPENAPI,
    contact={'name': 'HorarisSolver', 'url': 'https://github.com/XeviSanmartin/HorarisSolver'},
)


# ---------------------------------------------------------------------------
# Models d'entrada: format Solver.json
#
# Els models admeten camps extra (extra='allow') perquè versions noves de
# l'editor puguin afegir camps sense trencar l'API. Els camps marcats com a
# obligatoris són els que el preprocessador necessita realment.
# ---------------------------------------------------------------------------

class Desiderata(BaseModel):
    """Preferència horària d'un professor."""
    model_config = ConfigDict(extra='allow')

    dia: int = Field(ge=0, le=4, description='0=dilluns … 4=divendres.')
    hora: int = Field(ge=0, le=12, description='0=08:00 … 12=20:00. El solver només programa les hores 0–10.')
    tipus: int = Field(ge=1, le=2, description='1 = prefereix que no (soft), 2 = NO disponible (hard).')


class ModulProfessor(BaseModel):
    """Assignació d'un mòdul a un professor: quantes hores i en quines condicions."""
    model_config = ConfigDict(extra='allow')

    index: int = Field(description="Índex del mòdul al catàleg `moduls[]` (camp `index`).")
    hores: int = Field(default=0, ge=0, description='Hores setmanals a programar.')
    aula: int = Field(default=-1, description="Índex de l'aula preferida (`aules[].index`). -1 = qualsevol.")
    subgrup: int = Field(default=3, ge=1, le=3, description='1 = 1r mig grup, 2 = 2n mig grup, 3 = grup sencer.')
    suport: bool = Field(default=False, description='És professor de suport (assisteix un altre professor).')
    simultani: bool = Field(default=False, description="La classe ha de coincidir en hora i aula amb una altra del mateix mòdul.")
    particio: list = Field(default_factory=list, description="(Experimental) Preferències de distribució d'hores en blocs.")


class Professor(BaseModel):
    """Professor amb les seves preferències i assignacions."""
    model_config = ConfigDict(extra='allow', populate_by_name=True)

    index: int = Field(description='Identificador únic del professor.')
    actiu: bool = Field(default=True, description="Si és fals, el solver l'ignora.")
    nom: str = Field(description='Nom complet.')
    nomCurt: str = Field(description="Nom curt per mostrar a l'horari.")
    especialitat: int = Field(description='Índex a `especialitats[]`.')
    comentaris: str = Field(default='')
    tutorCurs: int = Field(default=-1, description="Índex del curs del qual és tutor (-1 si no n'és).")
    hores7: bool = Field(default=False, alias='7hores',
                         description='Pot fer fins a 7 hores en un dia (per defecte, màxim 6).')
    DiesLliures: bool = Field(default=False, description='Pot tenir fins a 1 dia lliure entre dimarts i dijous.')
    controlable: bool = Field(default=False,
                              description="Si és fals, no s'apliquen les restriccions de dies lliures ni de dilluns/divendres.")
    desiderata: list[Desiderata] = Field(default_factory=list)
    moduls: list[ModulProfessor] = Field(default_factory=list)


class ModulCataleg(BaseModel):
    """Entrada del catàleg de mòduls (les hores i professors van a `professors[].moduls`)."""
    model_config = ConfigDict(extra='allow')

    index: int = Field(description='Identificador únic del mòdul.')
    codi: str = Field(default='', description="Codi oficial. Prefixos especials: `AN-` (anglès), `SO-` (sostenibilitat), `DI-` (digitalització), `TUTORIA`.")
    nom: str = Field(default='', description="Nom complet. Es detecten automàticament: 'tutoria', 'anglès', 'FOL', 'sostenibilitat', 'digitalització'.")
    curs: int = Field(default=-1, description='Índex del curs a `cursos[]`.')
    especialitat: int = Field(default=-1, description='Índex a `especialitats[]` (2 = FOL, 3 = anglès).')


class SlotHorari(BaseModel):
    """Franja horària concreta."""
    model_config = ConfigDict(extra='allow')

    dia: int = Field(ge=0, le=4)
    hora: int = Field(ge=0, le=12)


class Curs(BaseModel):
    """Grup d'alumnes (curs)."""
    model_config = ConfigDict(extra='allow')

    index: int = Field(description='Identificador únic del curs.')
    actiu: bool = Field(default=True, description="Si és fals, el solver l'ignora.")
    nom: str = Field(description='Nom del curs (p. ex. "ASIX1").')
    color: list = Field(default_factory=list, description='[R, G, B] 0–255, per a la UI.')
    aula: int = Field(default=-1, description="Índex de l'aula principal (`aules[].index`).")
    horari_disponible: list[SlotHorari] = Field(
        default_factory=list,
        description='Si és buit, el curs pot tenir classe a qualsevol hora. Si té elements, NOMÉS als slots indicats.')


class Aula(BaseModel):
    """Aula del centre."""
    model_config = ConfigDict(extra='allow')

    index: int = Field(description="Identificador únic. Els índexs poden no ser correlatius: refereu-vos sempre a aquest camp.")
    actiu: bool = Field(default=True, description="Si és fals, el solver l'ignora.")
    nom: str = Field(description='Nom per mostrar.')
    nomes_subgrups: bool = Field(default=False, description='Si és cert, no admet classes de grup sencer (subgrup 3).')
    nomes_tardes: bool = Field(default=False, description="Si és cert, només disponible de l'hora 6 (14:00) en endavant.")


class Especialitat(BaseModel):
    """Especialitat docent."""
    model_config = ConfigDict(extra='allow')

    index: int
    actiu: bool = Field(default=True)
    codi: str = Field(description='Codi oficial (p. ex. "507").')
    nom: str


class GrupCoordinat(BaseModel):
    """Grup de mòduls que han d'impartir-se exactament a la mateixa hora."""
    model_config = ConfigDict(extra='allow')

    nom: str = Field(default='', description='Nom descriptiu del grup.')
    moduls: list[int] = Field(default_factory=list, description="Índexs dels mòduls coordinats.")


class ModulsCoordinats(BaseModel):
    model_config = ConfigDict(extra='allow')

    grups: list[GrupCoordinat] = Field(default_factory=list)


class DadesSolver(BaseModel):
    """Contingut complet d'un fitxer Solver.json de l'editor Switch2."""
    model_config = ConfigDict(
        extra='allow',
        json_schema_extra={
            'example': {
                'professors': [{
                    'index': 0, 'actiu': True, 'nom': 'Artur Juvé', 'nomCurt': 'Artur',
                    'especialitat': 0, 'tutorCurs': 2, '7hores': False,
                    'DiesLliures': False, 'controlable': True,
                    'desiderata': [{'dia': 1, 'hora': 5, 'tipus': 2}],
                    'moduls': [{'index': 27, 'hores': 2, 'aula': 7, 'subgrup': 3}],
                }],
                'moduls': [{'index': 27, 'codi': 'M6-0612', 'nom': 'Desenvolupament entorn client',
                            'curs': 6, 'especialitat': 1}],
                'cursos': [{'index': 6, 'actiu': True, 'nom': 'DAW2', 'aula': 1}],
                'aules': [{'index': 7, 'actiu': True, 'nom': 'Aula 3.10'}],
                'especialitats': [{'index': 1, 'actiu': True, 'codi': '627',
                                   'nom': 'Sistemes i aplicacions informàtiques'}],
                'moduls_coordinats': {'grups': [{'nom': 'FOL DAMW', 'moduls': [5, 12, 19]}]},
                'projectes': [37],
                'horaris_projectes': [{'dia': 3, 'hora': 8}],
            }
        },
    )

    professors: list[Professor] = Field(default_factory=list)
    moduls: list[ModulCataleg] = Field(default_factory=list, description='Catàleg de mòduls.')
    cursos: list[Curs] = Field(default_factory=list)
    aules: list[Aula] = Field(default_factory=list)
    especialitats: list[Especialitat] = Field(default_factory=list)
    moduls_coordinats: Optional[ModulsCoordinats] = Field(
        default=None, description="Grups de mòduls que han d'anar a la mateixa hora.")
    projectes: list[int] = Field(
        default_factory=list,
        description="Índexs de mòduls de projecte final: només poden anar als slots de `horaris_projectes`.")
    horaris_projectes: list[SlotHorari] = Field(
        default_factory=list, description='Slots permesos per als mòduls de projecte.')
    horari: list = Field(
        default_factory=list,
        description="Hores pre-assignades a l'editor, que el solver pot mantenir inamovibles "
                    "si s'activa `opcions.fixar_horari` a /api/solve. Format de l'editor: "
                    "`horari[periode][dia][hora]` → llista de cel·les (null als buits), on cada "
                    "cel·la és `{modul, curs, aula, subgrup, suport, simultani, profe}` amb "
                    "índexs de les entitats. També s'accepta el format pla `horari[dia][hora]` → "
                    "cel·la o null. Les cel·les incoherents (professor inexistent, mòdul no "
                    "assignat, slot fora de rang...) es descarten amb un advertiment.")

    def com_a_dict(self) -> dict:
        """Serialitza amb els noms de camp originals de Solver.json (p. ex. `7hores`).

        Amb exclude_unset per no injectar valors per defecte que l'entrada no
        tenia: el preprocessador ja aplica els seus propis valors per defecte, i
        la plantilla de `solucio_compatible` ha de ser fidel a l'original.
        """
        return self.model_dump(by_alias=True, exclude_unset=True)


# ---------------------------------------------------------------------------
# Models de petició i resposta
# ---------------------------------------------------------------------------

class OpcionsSolve(BaseModel):
    """Opcions d'execució del solver."""
    max_time_seconds: float = Field(
        default=60, gt=0,
        description='Límit de temps de cerca en segons. El servidor l\'acota al valor '
                    '`max_temps_solver` que informa `GET /api/health`.')
    num_workers: int = Field(
        default=4, ge=1, le=16,
        description='Threads de cerca paral·lela de CP-SAT.')
    incloure_compatible: bool = Field(
        default=False,
        description='Si és cert, la resposta inclou `solucio_compatible`: la solució en '
                    'format Solver.json, reimportable a l\'editor Switch2.')
    fixar_horari: bool = Field(
        default=False,
        description='Si és cert, les hores pre-assignades del camp `dades.horari` queden '
                    'inamovibles: el solver les manté a la seva posició exacta i només '
                    'col·loca la resta. Redueix l\'espai de cerca i el temps de resolució. '
                    'Si les hores fixades contradiuen alguna restricció dura, el resultat '
                    'serà INFEASIBLE.')
    periode: int = Field(
        default=0, ge=0,
        description='Període del camp `dades.horari` del qual s\'extreuen les hores '
                    'pre-assignades (l\'editor n\'exporta 5; per defecte el 0).')


class PeticioSolve(BaseModel):
    """Cos de la petició de /api/solve."""
    dades: DadesSolver
    opcions: OpcionsSolve = Field(default_factory=OpcionsSolve)


class PeticioValidate(BaseModel):
    """Cos de la petició de /api/validate i /api/preprocess."""
    dades: DadesSolver
    periode: int = Field(
        default=0, ge=0,
        description='Període del camp `dades.horari` del qual s\'extreuen les hores '
                    'pre-assignades (l\'editor n\'exporta 5; per defecte el 0).')


class RespostaHealth(BaseModel):
    estat: str = Field(examples=['ok'])
    versio_api: str = Field(examples=[VERSIO_API])
    max_temps_solver: float = Field(
        description='Límit dur (segons) aplicat a `opcions.max_time_seconds` de /api/solve.',
        examples=[280.0])
    python: str = Field(examples=['3.12.13'])


class RespostaValidate(BaseModel):
    valid: bool = Field(description='Cert si no hi ha cap advertiment.')
    advertiments: list[str] = Field(
        description='Incoherències detectades. NO bloquegen /api/solve.',
        examples=[['Mòdul Repàs no té cap professor assignat', 'Cursos sense tutoria: DAMW D']])
    estadistiques: dict[str, Any] = Field(
        description='Recompte d\'entitats i mòduls especials detectats.',
        examples=[{
            'total_professors': 18, 'total_moduls': 76, 'total_cursos': 9, 'total_aules': 8,
            'moduls_fol': 5, 'moduls_angles': 4, 'moduls_sostenibilitat': 3,
            'moduls_digitalizacio': 3, 'moduls_suport': 0, 'moduls_simultaneos': 2,
            'tutories': 9, 'subgrups_per_curs': {'0': [1, 2, 3]},
        }])


class RespostaPreprocess(BaseModel):
    dades_processades: dict[str, Any] = Field(
        description='JSON intermedi que consumeix el solver: professors amb restriccions '
                    'normalitzades, mòduls amb marques `es_fol`/`es_angles`/…, cursos amb '
                    'subgrups i tutor, agrupacions i configuració global.')
    advertiments: list[str]


class StatsSolucio(BaseModel):
    model_config = ConfigDict(extra='allow')

    temps_resolucio: float = Field(description='Segons de cerca del solver.', examples=[120.3])
    conflictes: int = Field(description='Conflictes explorats per CP-SAT (mesura de dificultat).')
    branques: int = Field(description='Branques explorades per CP-SAT.')
    estat: str = Field(examples=['FEASIBLE'])
    objectiu: float = Field(
        description='Valor de la funció objectiu (com més baix, millor): '
                    '10×hores_mortes + 20×preferències_no_respectades.',
        examples=[3550.0])


class Solucio(BaseModel):
    """Horari resolt, en tres vistes indexades per [dia 0–4][hora 0–10]."""
    model_config = ConfigDict(extra='allow')

    horari: list = Field(
        description='Vista per curs: `horari[curs][dia][hora]` → llista de classes (0 = lliure, '
                    '1 = classe, 2 = desdoblament de subgrups). Cada classe: '
                    '`{modul, modul_index, professor, professor_index, aula, aula_index, subgrup}`.')
    professors: list = Field(
        description='Vista per professor: `professors[professor_index][dia][hora]` → llista de '
                    'classes `{modul, modul_index, curs, curs_index, aula, aula_index, subgrup}`.')
    aules: list = Field(
        description='Vista per aula (indexada per posició, no per `aula_index`): '
                    '`aules[pos][dia][hora]` → llista de classes '
                    '`{modul, modul_index, professor, professor_index, curs, curs_index, subgrup}`.')
    stats: StatsSolucio


class RespostaSolve(BaseModel):
    estat: str = Field(
        description="Resultat de la resolució: 'OPTIMAL', 'FEASIBLE', 'INFEASIBLE', "
                    "'UNKNOWN' o 'MODEL_INVALID'.",
        examples=['FEASIBLE'])
    solucio: Optional[Solucio] = Field(
        default=None, description="Present només si l'estat és OPTIMAL o FEASIBLE.")
    solucio_compatible: Optional[dict[str, Any]] = Field(
        default=None,
        description='Solució en format Solver.json, reimportable a l\'editor '
                    '(present només si s\'ha demanat amb `opcions.incloure_compatible`).')
    advertiments: list[str] = Field(
        default_factory=list,
        description='Advertiments del validador de dades (informatius).')


class DetallError(BaseModel):
    """Cos del camp `detail` en errors 422 (dades) i 500."""
    error: str = Field(description='Descripció general del problema.')
    detall: str = Field(description='Excepció concreta (tipus i missatge).')
    traca: str = Field(description='Traça reduïda per a depuració.')


RESPOSTES_DADES = {
    422: {
        'description': 'El cos de la petició no compleix l\'esquema (error Pydantic estàndard) '
                       'o les dades tenen una incoherència estructural profunda (cos `DetallError`).',
    },
    500: {
        'description': 'Error intern construint o resolent el model.',
        'model': DetallError,
    },
}


# ---------------------------------------------------------------------------
# Utilitats internes
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _silenci():
    """Captura els prints del solver perquè no inundin els logs del servidor."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def _preprocessa(dades: DadesSolver, periode: int = 0) -> tuple[HorariData, list[str]]:
    """Carrega i preprocessa dades amb format Solver.json.

    Retorna (HorariData, advertiments). Llança HTTPException 422 si les dades
    tenen una incoherència que l'esquema no captura.
    """
    try:
        hd = HorariData()
        with _silenci():
            hd.carrega_dades(dades.com_a_dict(), periode=periode)
        advertiments = hd.valida_dades()
        return hd, advertiments
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
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


@app.get('/api/health', response_model=RespostaHealth, tags=['Servei'],
         summary='Estat del servei')
def health() -> RespostaHealth:
    """Comprova que el servei i OR-Tools estan operatius.

    Retorna també `max_temps_solver`, el límit dur que el servidor aplica a
    `opcions.max_time_seconds` de `/api/solve`.
    """
    from ortools.sat.python import cp_model
    cp_model.CpModel()  # instanciació mínima per verificar la llibreria
    return RespostaHealth(
        estat='ok',
        versio_api=VERSIO_API,
        max_temps_solver=MAX_TEMPS_SOLVER,
        python=sys.version.split()[0],
    )


@app.post('/api/validate', response_model=RespostaValidate, tags=['Validació'],
          summary='Valida un Solver.json sense resoldre',
          responses={k: v for k, v in RESPOSTES_DADES.items() if k != 500})
def validate(peticio: PeticioValidate) -> RespostaValidate:
    """Carrega les dades, executa les validacions i retorna advertiments i estadístiques.

    No construeix ni resol el model: triga mil·lisegons i serveix per detectar
    problemes abans de llançar una resolució. Els `advertiments` són informatius
    i **no** impedeixen cridar `/api/solve`.
    """
    hd, advertiments = _preprocessa(peticio.dades, periode=peticio.periode)
    stats = hd.get_estadistiques()
    # subgrups_per_curs conté sets; fer-ho serialitzable
    stats['subgrups_per_curs'] = {str(k): sorted(v) for k, v in stats['subgrups_per_curs'].items()}
    return RespostaValidate(
        valid=len(advertiments) == 0,
        advertiments=advertiments,
        estadistiques=stats,
    )


@app.post('/api/preprocess', response_model=RespostaPreprocess, tags=['Validació'],
          summary='Retorna les dades preprocessades (debug)',
          responses={k: v for k, v in RESPOSTES_DADES.items() if k != 500})
def preprocess(peticio: PeticioValidate) -> RespostaPreprocess:
    """Retorna el JSON intermedi (`dades_solver_processades`) que consumeix el solver.

    Útil per depurar què ha entès el preprocessador: mòduls especials detectats
    (FOL, anglès, sostenibilitat, digitalització), tutories, agrupacions
    fusionables i subgrups per curs.
    """
    hd, advertiments = _preprocessa(peticio.dades, periode=peticio.periode)
    return RespostaPreprocess(
        dades_processades=hd.genera_dades_processades(),
        advertiments=advertiments,
    )


@app.post('/api/solve', response_model=RespostaSolve, response_model_exclude_none=True,
          tags=['Resolució'], summary="Resol l'horari", responses=RESPOSTES_DADES)
def solve(peticio: PeticioSolve) -> RespostaSolve:
    """Executa el pipeline complet: preprocessament → model CP-SAT → solució.

    Interpreteu el camp `estat` de la resposta (vegeu la descripció general de
    l'API). Amb dades reals d'un centre, un `max_time_seconds` de 60–120 s sol
    donar una solució `FEASIBLE`; si rebeu `UNKNOWN`, reintenteu amb més temps.
    """
    opcions = peticio.opcions
    max_time = min(opcions.max_time_seconds, MAX_TEMPS_SOLVER)

    hd, advertiments = _preprocessa(peticio.dades, periode=opcions.periode)
    dades_processades = hd.genera_dades_processades()

    hores_fixades = len(dades_processades.get('horari_fixat', []))
    if hores_fixades and opcions.fixar_horari:
        advertiments.append(
            f"S'han fixat {hores_fixades} hores pre-assignades del període {opcions.periode}: "
            f"el solver les manté inamovibles.")
    elif hores_fixades:
        advertiments.append(
            f"S'han detectat {hores_fixades} hores pre-assignades (període {opcions.periode}) "
            f"però `opcions.fixar_horari` és fals: el solver les ignora.")

    try:
        with _silenci():
            solver = HorariSolver(dades_processades)
            solucio = solver.executar(
                fixar_horari=opcions.fixar_horari,
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
                solucio, template=peticio.dades.com_a_dict(), output_path=None,
            )

    return RespostaSolve(
        estat=solucio['stats']['estat'],
        solucio=solucio,
        solucio_compatible=solucio_compatible,
        advertiments=advertiments,
    )
