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
import time
import uuid
import threading
import contextlib
import traceback

# El codi del solver viu a l'arrel del repositori
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from ortools.sat.python import cp_model
from pydantic import BaseModel, ConfigDict, Field

from horari_solver import HorariData
from Solver import HorariSolver, genera_json_solucio_compatible, calcula_gap

# Límit dur del temps de resolució. A Vercel la funció té un maxDuration
# (configurat a vercel.json); deixem marge per al preprocessament i la resposta.
# En un desplegament propi (Docker/uvicorn) es pot apujar tant com calgui.
MAX_TEMPS_SOLVER = float(os.environ.get('MAX_TEMPS_SOLVER', 280))

# Orígens permesos per a peticions des del navegador (separats per comes).
# Per defecte s'accepta qualsevol origen: l'API no té estat ni credencials.
CORS_ORIGINS = [o.strip() for o in os.environ.get('CORS_ORIGINS', '*').split(',') if o.strip()]

VERSIO_API = '1.8.0'

DESCRIPCIO = """
Servei de generació d'horaris escolars amb [OR-Tools CP-SAT](https://developers.google.com/optimization).

## Flux

1. **`POST /api/validate`** — valida el JSON d'entrada (ràpid, sense resoldre).
2. **`POST /api/solve`** — preprocessa, construeix el model CP-SAT i resol (síncron).
3. Opcionalment, **`POST /api/preprocess`** per inspeccionar el JSON intermedi (debug).

En desplegaments persistents (uvicorn local, Docker en un servidor propi) hi ha
també el mode **asíncron**: `POST /api/jobs` llança la resolució en segon pla,
`GET /api/jobs/{id}` n'informa del progrés i `DELETE /api/jobs/{id}` l'atura
conservant la millor solució trobada fins al moment.

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
    {'name': 'Feines', 'description':
        'Resolució asíncrona: llançar el solver en segon pla, consultar-ne el progrés '
        'i aturar-lo conservant la millor solució trobada. **Requereix un desplegament '
        'persistent** (uvicorn/Docker): a Vercel cada petició pot anar a una instància '
        'diferent i el registre de feines (en memòria) no es comparteix.'},
]

app = FastAPI(
    title="API Solver d'horaris Switch2",
    description=DESCRIPCIO,
    version=VERSIO_API,
    openapi_tags=TAGS_OPENAPI,
    contact={'name': 'HorarisSolver', 'url': 'https://github.com/XeviSanmartin/HorarisSolver'},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=['*'],
    allow_headers=['*'],
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
    lliureRestriccions: bool = Field(default=False,
                                     description="Si és cert, el professor és lliure de restriccions de règim: no "
                                                 "se li exigeix classe dilluns/divendres i pot tenir diversos dies lliures.")
    desiderata: list[Desiderata] = Field(default_factory=list)
    moduls: list[ModulProfessor] = Field(default_factory=list)


class SlotHorari(BaseModel):
    """Franja horària concreta."""
    model_config = ConfigDict(extra='allow')

    dia: int = Field(ge=0, le=4)
    hora: int = Field(ge=0, le=12)


class ModulCataleg(BaseModel):
    """Entrada del catàleg de mòduls (les hores i professors van a `professors[].moduls`)."""
    model_config = ConfigDict(extra='allow')

    index: int = Field(description='Identificador únic del mòdul.')
    codi: str = Field(default='', description="Codi oficial. Prefixos especials: `AN-` (anglès), `SO-` (sostenibilitat), `DI-` (digitalització), `TUTORIA`.")
    nom: str = Field(default='', description="Nom complet. Es detecten automàticament: 'tutoria', 'anglès', 'FOL', 'sostenibilitat', 'digitalització'.")
    curs: int = Field(default=-1, description='Índex del curs a `cursos[]`.')
    especialitat: int = Field(default=-1, description='Índex a `especialitats[]` (2 = FOL, 3 = anglès).')
    horari_disponible: list[SlotHorari] = Field(
        default_factory=list,
        description="Slots on es pot impartir el mòdul (p. ex. només matins o només tardes). "
                    "Buit = qualsevol hora. S'interseca amb l'`horari_disponible` del curs.")
    aules_possibles: list[int] = Field(
        default_factory=list,
        description="Índexs de les aules on es pot impartir (requeriments d'espai/equipament). "
                    "Buit = qualsevol aula. Cada hora del mòdul anirà a una aula d'aquest conjunt. "
                    "Té prioritat sobre l'aula preferida de l'assignació del professor.")
    primera_ultima_hora: Optional[bool] = Field(
        default=None,
        description="Si és cert, el mòdul ha d'anar a un extrem del dia del grup (un bloc "
                    "d'hores sense cap classe del grup abans o després), perquè els alumnes "
                    "que no el cursen puguin arribar més tard o marxar abans. Si és null, es "
                    "dedueix automàticament (FOL i anglès s'hi apliquen per defecte).")


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
    ignora_hores_grogues: bool = Field(
        default=False,
        description="Si és cert, el solver ignora les preferències \"prefereix no\" dels "
                    "professors (les hores grogues, desiderata tipus 1): no les penalitza a "
                    "la funció objectiu. Les hores \"no disponible\" (vermelles, tipus 2) "
                    "continuen sent restriccions dures.")
    explicar_infeasible: bool = Field(
        default=False,
        description='Si és cert i el resultat és INFEASIBLE, la resposta inclou '
                    '`motiu_infeasible`: els grups de restriccions que formen el conflicte '
                    '(desiderates d\'un professor, hores fixades, FOL/anglès, tutoria, '
                    'mòduls coordinats...). Té un cost de rendiment (el model es resol amb '
                    'literals d\'assumpció), per això per defecte està desactivat.')


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
    motiu_infeasible: Optional[list[str]] = Field(
        default=None,
        description="Grups de restriccions que formen el conflicte quan l'estat és "
                    "INFEASIBLE (present només amb `opcions.explicar_infeasible`). "
                    "El conjunt és suficient per causar la infactibilitat, no "
                    "necessàriament mínim. Buit = el conflicte és a les restriccions "
                    "estructurals (hores exactes, solapaments, horaris disponibles).")


class DetallError(BaseModel):
    """Cos del camp `detail` en errors 422 (dades) i 500."""
    error: str = Field(description='Descripció general del problema.')
    detall: str = Field(description='Excepció concreta (tipus i missatge).')
    traca: str = Field(description='Traça reduïda per a depuració.')


class RespostaFeinaCreada(BaseModel):
    """Resposta de la creació d'una feina de resolució asíncrona."""
    id: str = Field(description='Identificador de la feina, per a GET/DELETE /api/jobs/{id}.')
    estat_feina: str = Field(examples=['en_curs'])


class RespostaFeina(BaseModel):
    """Estat d'una feina de resolució asíncrona."""
    id: str
    estat_feina: str = Field(
        description="'en_curs' (el solver treballa), 'acabada' (hi ha `resultat`) o "
                    "'error' (hi ha `error`).",
        examples=['en_curs'])
    aturada_demanada: bool = Field(
        description="Cert si s'ha demanat aturar la feina amb DELETE. La feina acabarà "
                    "poc després amb la millor solució trobada (o UNKNOWN si no n'hi havia).")
    temps_transcorregut: float = Field(
        description='Segons des que la feina ha començat a resoldre.')
    solucions_intermedies: int = Field(
        description='Nombre de solucions millorades que CP-SAT ha trobat fins ara.')
    objectiu_actual: Optional[float] = Field(
        default=None,
        description="Valor de la funció objectiu de l'última solució trobada (com més "
                    "baix, millor). `null` mentre no hi hagi cap solució.")
    cota: Optional[float] = Field(
        default=None,
        description="Cota inferior de l'objectiu que CP-SAT ha demostrat. Amb "
                    "`objectiu_actual` permet calcular la distància a l'òptim.")
    gap: Optional[float] = Field(
        default=None,
        description="Gap d'optimalitat relatiu (0.0 = òptim demostrat). "
                    "`(objectiu_actual - cota) / |objectiu_actual|`.")
    te_solucio: bool = Field(
        default=False,
        description='Cert si ja hi ha una solució intermèdia descarregable '
                    '(GET /api/jobs/{id}/solucio).')
    metriques: Optional[dict] = Field(
        default=None,
        description="Mètriques de qualitat de la millor solució: hores mortes per "
                    "professor i per curs, i desiderata grogues incomplertes per professor.")
    advertiments: list[str] = Field(default_factory=list)
    resultat: Optional[RespostaSolve] = Field(
        default=None, description="Present quan `estat_feina` és 'acabada'.")
    error: Optional[DetallError] = Field(
        default=None, description="Present quan `estat_feina` és 'error'.")


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


def _afegeix_advertiments_fixacio(advertiments: list[str], dades_processades: dict,
                                  opcions: 'OpcionsSolve') -> None:
    """Informa de si les hores pre-assignades detectades es fixaran o no."""
    hores_fixades = len(dades_processades.get('horari_fixat', []))
    if hores_fixades and opcions.fixar_horari:
        advertiments.append(
            f"S'han fixat {hores_fixades} hores pre-assignades del període {opcions.periode}: "
            f"el solver les manté inamovibles.")
    elif hores_fixades:
        advertiments.append(
            f"S'han detectat {hores_fixades} hores pre-assignades (període {opcions.periode}) "
            f"però `opcions.fixar_horari` és fals: el solver les ignora.")


def _construeix_resposta_solve(solver: HorariSolver, solucio: Optional[dict],
                               opcions: 'OpcionsSolve', template: dict,
                               advertiments: list[str]) -> RespostaSolve:
    """Converteix el resultat del solver en una RespostaSolve."""
    if solucio is None:
        motiu = None
        if solver.ultim_estat == 'INFEASIBLE' and solver.explicar_infeasible:
            motiu = solver.motiu_infeasible
        return RespostaSolve(estat=solver.ultim_estat or 'UNKNOWN',
                             advertiments=advertiments, motiu_infeasible=motiu)

    solucio_compatible = None
    if opcions.incloure_compatible:
        with _silenci():
            solucio_compatible = genera_json_solucio_compatible(
                solucio, template=template, output_path=None,
            )

    return RespostaSolve(
        estat=solucio['stats']['estat'],
        solucio=solucio,
        solucio_compatible=solucio_compatible,
        advertiments=advertiments,
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
    _afegeix_advertiments_fixacio(advertiments, dades_processades, opcions)

    try:
        with _silenci():
            solver = HorariSolver(dades_processades)
            solucio = solver.executar(
                fixar_horari=opcions.fixar_horari,
                explicar_infeasible=opcions.explicar_infeasible,
                ignora_hores_grogues=opcions.ignora_hores_grogues,
                max_time_seconds=max_time,
                num_workers=opcions.num_workers,
                log_search_progress=False,
                output_path=None,
            )
            return _construeix_resposta_solve(
                solver, solucio, opcions, peticio.dades.com_a_dict(), advertiments)
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


# ---------------------------------------------------------------------------
# Feines asíncrones (resolució en segon pla amb aturada)
#
# El registre viu en memòria del procés: funciona en desplegaments persistents
# (uvicorn local, Docker al servidor propi) executats amb UN SOL procés. A
# Vercel cada petició pot anar a una instància diferent i les feines no s'hi
# comparteixen: allà useu el POST /api/solve síncron.
# ---------------------------------------------------------------------------

FEINES: dict[str, dict] = {}
FEINES_LOCK = threading.Lock()
FEINES_TTL = 3600.0  # les feines finalitzades s'esborren al cap d'una hora


class _CallbackProgres(cp_model.CpSolverSolutionCallback):
    """A cada solució millorada anota el progrés a la feina (objectiu, cota, gap
    i mètriques) i hi desa la millor solució intermèdia perquè es pugui
    descarregar mentre la cerca continua. També aplica l'aturada demanada."""

    def __init__(self, feina: dict, solver_horari):
        super().__init__()
        self._feina = feina
        self._solver_horari = solver_horari

    def on_solution_callback(self):
        f = self._feina
        f['solucions_intermedies'] += 1
        objectiu = self.ObjectiveValue()
        cota = self.BestObjectiveBound()
        f['objectiu_actual'] = objectiu
        f['cota'] = cota
        f['gap'] = calcula_gap(objectiu, cota)

        # Construir la instantània completa és car; com a molt un cop per segon.
        ara = self.WallTime()
        if ara - f.get('_ultima_instantania', -1e9) >= 1.0:
            f['_ultima_instantania'] = ara
            try:
                stats = {
                    'temps_resolucio': ara,
                    'conflictes': self.NumConflicts(),
                    'branques': self.NumBranches(),
                    'estat': 'FEASIBLE',
                    'objectiu': objectiu, 'cota': cota, 'gap': f['gap'],
                }
                solucio = self._solver_horari._construeix_solucio(self.Value, stats)
                solucio['metriques'] = self._solver_horari._metriques(solucio)
                f['millor_solucio'] = solucio
                f['metriques'] = solucio['metriques']
            except Exception:
                pass  # no bloquejar la cerca per un error construint mètriques

        if f['aturada_demanada']:
            self.StopSearch()


def _neteja_feines():
    """Esborra del registre les feines finalitzades fa més de FEINES_TTL segons."""
    ara = time.time()
    with FEINES_LOCK:
        antigues = [fid for fid, f in FEINES.items()
                    if f['estat_feina'] != 'en_curs' and ara - f['creada'] > FEINES_TTL]
        for fid in antigues:
            del FEINES[fid]


def _executa_feina(feina: dict, dades_processades: dict, opcions: OpcionsSolve,
                   template: dict, max_time: float):
    """Cos del fil de treball d'una feina."""
    try:
        with _silenci():
            solver = HorariSolver(dades_processades)
            feina['_solver'] = solver
            feina['_template'] = template
            # Cobreix l'aturada demanada abans que el solver existís
            if feina['aturada_demanada']:
                solver.atura()
            solucio = solver.executar(
                fixar_horari=opcions.fixar_horari,
                explicar_infeasible=opcions.explicar_infeasible,
                ignora_hores_grogues=opcions.ignora_hores_grogues,
                max_time_seconds=max_time,
                num_workers=opcions.num_workers,
                log_search_progress=False,
                output_path=None,
                solution_callback=_CallbackProgres(feina, solver),
            )
            feina['resultat'] = _construeix_resposta_solve(
                solver, solucio, opcions, template, feina['advertiments'])
        feina['estat_feina'] = 'acabada'
    except Exception as e:
        feina['error'] = DetallError(
            error='Error intern construint o resolent el model.',
            detall=f'{type(e).__name__}: {e}',
            traca=traceback.format_exc(limit=5),
        )
        feina['estat_feina'] = 'error'
    finally:
        feina['finalitzada'] = time.time()


def _vista_feina(feina: dict) -> RespostaFeina:
    fi = feina['finalitzada'] or time.time()
    return RespostaFeina(
        id=feina['id'],
        estat_feina=feina['estat_feina'],
        aturada_demanada=feina['aturada_demanada'],
        temps_transcorregut=round(max(0.0, fi - feina['iniciada']), 1),
        solucions_intermedies=feina['solucions_intermedies'],
        objectiu_actual=feina['objectiu_actual'],
        cota=feina.get('cota'),
        gap=feina.get('gap'),
        te_solucio=feina.get('millor_solucio') is not None,
        metriques=feina.get('metriques'),
        advertiments=feina['advertiments'],
        resultat=feina['resultat'],
        error=feina['error'],
    )


def _busca_feina(feina_id: str) -> dict:
    feina = FEINES.get(feina_id)
    if feina is None:
        raise HTTPException(
            status_code=404,
            detail=f"Feina '{feina_id}' desconeguda: pot haver caducat (TTL "
                   f"{FEINES_TTL / 60:.0f} min) o el servidor pot haver-se reiniciat.",
        )
    return feina


@app.post('/api/jobs', response_model=RespostaFeinaCreada, status_code=202, tags=['Feines'],
          summary='Llança una resolució en segon pla', responses=RESPOSTES_DADES)
def crea_feina(peticio: PeticioSolve) -> RespostaFeinaCreada:
    """Valida i preprocessa les dades, llança el solver en un fil i retorna a l'instant.

    Mateix cos de petició que `POST /api/solve`. Consulteu el progrés amb
    `GET /api/jobs/{id}` i atureu-la (conservant la millor solució trobada)
    amb `DELETE /api/jobs/{id}`.

    **Només fiable en desplegaments persistents d'un sol procés** (uvicorn,
    Docker): el registre de feines viu en memòria.
    """
    _neteja_feines()
    opcions = peticio.opcions
    max_time = min(opcions.max_time_seconds, MAX_TEMPS_SOLVER)

    hd, advertiments = _preprocessa(peticio.dades, periode=opcions.periode)
    dades_processades = hd.genera_dades_processades()
    _afegeix_advertiments_fixacio(advertiments, dades_processades, opcions)

    ara = time.time()
    feina = {
        'id': uuid.uuid4().hex[:12],
        'estat_feina': 'en_curs',
        'aturada_demanada': False,
        'creada': ara,
        'iniciada': ara,
        'finalitzada': None,
        'solucions_intermedies': 0,
        'objectiu_actual': None,
        'cota': None,
        'gap': None,
        'metriques': None,
        'millor_solucio': None,
        'advertiments': advertiments,
        'resultat': None,
        'error': None,
        '_solver': None,
        '_template': None,
        '_ultima_instantania': -1e9,
    }
    with FEINES_LOCK:
        FEINES[feina['id']] = feina

    threading.Thread(
        target=_executa_feina,
        args=(feina, dades_processades, opcions, peticio.dades.com_a_dict(), max_time),
        daemon=True,
    ).start()

    return RespostaFeinaCreada(id=feina['id'], estat_feina='en_curs')


@app.get('/api/jobs/{feina_id}', response_model=RespostaFeina,
         response_model_exclude_none=True, tags=['Feines'],
         summary="Estat i progrés d'una feina",
         responses={404: {'description': 'Feina desconeguda o caducada.'}})
def consulta_feina(feina_id: str) -> RespostaFeina:
    """Retorna l'estat de la feina: progrés mentre `estat_feina` és `en_curs`
    (temps, solucions trobades, objectiu actual), i el `resultat` complet
    (mateix format que `POST /api/solve`) quan és `acabada`.
    """
    return _vista_feina(_busca_feina(feina_id))


@app.delete('/api/jobs/{feina_id}', response_model=RespostaFeina,
            response_model_exclude_none=True, tags=['Feines'],
            summary='Atura una feina en curs',
            responses={404: {'description': 'Feina desconeguda o caducada.'}})
def atura_feina(feina_id: str) -> RespostaFeina:
    """Demana aturar la cerca. CP-SAT s'atura de manera neta: la feina acaba poc
    després amb la **millor solució trobada fins al moment** (`FEASIBLE`) o
    `UNKNOWN` si encara no n'hi havia cap. Útil quan, a mig càlcul, t'adones
    que calia fixar alguna hora més.
    """
    feina = _busca_feina(feina_id)
    if feina['estat_feina'] == 'en_curs':
        feina['aturada_demanada'] = True
        if feina['_solver'] is not None:
            feina['_solver'].atura()
    return _vista_feina(feina)


@app.get('/api/jobs/{feina_id}/solucio', tags=['Feines'],
         summary='Millor solució trobada fins ara (descarregable)',
         responses={404: {'description': 'Feina desconeguda, caducada o encara sense solució.'}})
def solucio_feina(feina_id: str) -> dict:
    """Retorna la millor solució trobada fins al moment en format compatible amb
    l'editor (mateix format que `solucio_compatible` de `/api/solve`), perquè es
    pugui **desar a un fitxer** sense esperar que la feina acabi. Funciona tant
    amb la feina en curs com acabada.
    """
    feina = _busca_feina(feina_id)
    solucio = feina.get('millor_solucio')
    if solucio is None:
        raise HTTPException(
            status_code=404,
            detail='Encara no hi ha cap solució intermèdia per a aquesta feina.')
    with _silenci():
        compatible = genera_json_solucio_compatible(
            solucio, template=feina.get('_template'), output_path=None)
    return {
        'estat': solucio['stats'].get('estat', 'FEASIBLE'),
        'objectiu': solucio['stats'].get('objectiu'),
        'gap': solucio['stats'].get('gap'),
        'metriques': solucio.get('metriques'),
        'solucio_compatible': compatible,
    }
