# CLAUDE.md — Solver d'horaris (HorarisSolver)

Solver de generació d'horaris. Python + OR-Tools (CP-SAT) + FastAPI. Rep les dades de
l'editor (`C:\Git\Horaris`, vegeu-hi el seu `CLAUDE.md`) i retorna un horari.

Aquest fitxer recull el que **no és obvi llegint el codi**: arquitectura, el model de
restriccions i decisions/paranys coneguts.

## Arquitectura i fitxers clau

- **`api/index.py`** — API FastAPI. Import: `HorariData` (de `horari_solver.py`) +
  `HorariSolver`/`genera_json_solucio_compatible` (de `Solver.py`). Feines asíncrones
  a `/api/jobs` (registre **en memòria del procés** → un sol procés, sense `--workers`).
- **`Solver.py`** — el motor CP-SAT (creació de variables, restriccions, objectiu,
  extracció de la solució). És el fitxer gran i on viu la lògica de resolució.
- **`horari_solver.py`** — model de dades (`HorariData`): carrega/valida professors,
  cursos, mòduls, aules, especialitats, `projectes`, `horaris_projectes`.
- `exportar_html.py`, `switch2.py` — utilitats/exportació.
- Docs: **`DOC_API_SOLVER.md`**, `API_REST.md`, `openapi.json`, `DESPLEGAMENT.md`,
  `HORES_FIXADES.md`. Versió API actual: **1.7.1**.
  - `openapi.json` i `dades_solver_processades.json` són fitxers "daurats" verificats
    per tests: regenera'ls amb `scripts/exporta_openapi.py` i carregant
    `BuitRestriccions.json` a `HorariData().exporta_dades_processades(...)` quan canviïn
    l'API o `genera_dades_processades`.

Format d'entrada = el mateix export de l'editor (`professors/cursos/moduls/aules/
especialitats/horari` + `moduls_coordinats/projectes/horaris_projectes`). L'editor
també envia `config` (`horesSetmana`, `horaIniciTarda`); el solver en dedueix el
nombre de franges (`hores_per_dia`) i el minut d'inici de cada una (`hores_inici_min`,
per al descans entre dies). Sense `config` fa servir uns valors per defecte de 11
franges (compatibilitat amb dades antigues).

## Model de restriccions (el que cal saber)

- Les variables de decisió són `vars_assignacio[(modul, professor, dia, hora, aula, subgrup)]`.
  Les restriccions **sumen sobre totes les aules** per a cada `(m,p,d,h,s)`, així que
  **una assignació pot tenir diverses aules candidates** (el disseny ja ho preveu; el
  bloqueig a una sola aula era una restricció afegida després).

- **Aula preferida = preferència suau (canvi clau, commit `0ab9f0d`).**
  A la creació de variables (`Solver.py`, ~línia 200):
  - Si el mòdul té `aules_possibles` → candidates = aquest conjunt; l'aula de
    l'assignació (`aula`) és només una **preferència** i es penalitza suaument a
    l'objectiu col·locar-la fora (llista `self.penalitzacio_aula`, `peso_aula=1`).
    Serveix perquè una aula saturada (p. ex. tots els desdoblaments a la 3.01) no
    condemni el solver: pot reubicar el mínim d'hores.
  - Si `aules_possibles` és buit → es fixa a l'aula preferida (comportament clàssic;
    no fa créixer el model). ⚠️ **Obrir totes les aules per defecte feia esclatar el
    model** (el dataset real quedava `UNKNOWN`); per això és opt-in via `aules_possibles`.

- **`horari_disponible` (mòdul i curs) = restricció DURA de franja.** Els slots fora de
  la llista se salten (`slots_modul`, `Solver.py:~178`). S'interseca amb el del curs.
  Buit = qualsevol hora.

- **Restriccions d'aula**: `nomes_subgrups` (no admet grup sencer, `subgrup==3`),
  `nomes_tardes` (només `hora>=6`).

- **`projectes` / `horaris_projectes`**: els mòduls a `moduls_projectes` es limiten a
  `slots_projectes` (`Solver.py:~1029`). ⚠️ **Parany**: si `projectes` no és buit i
  `horaris_projectes` és buit, aquests mòduls queden **sense cap slot** → INFEASIBLE
  ("el projecte només pot anar als slots d'horari de projectes"). L'editor ja **no**
  fa servir aquesta funció (les franges de projecte es posen amb `horari_disponible`);
  mantenir tots dos camps buits.

- **Descans de 12 h entre dies** (restricció 10, reescrita). Es prohibeix qualsevol
  parella de classes d'un professor en dies consecutius separades per **< 12 h**
  (durada de classe assumida: 60 min; usa `hores_inici_min`). Substitueix la regla
  antiga "primera hora si última ahir". Aplica a TOTS els professors (els "lliures"
  NO se la salten).

- **Règim de dies** (restricció 12): exigeix classe dilluns i divendres i limita els
  dies lliures. Només s'aplica si `controlable` **i no** `lliureRestriccions`. El camp
  **`lliureRestriccions`** (casella "Lliure de restriccions" de l'editor) allibera el
  professor d'aquest règim (per a professorat amb hores fora del departament o molt
  poques). No afecta màx hores, descans ni desiderates.

- **Regles de posició** (per mòdul/curs): FOL/anglès sempre a **primera o última** hora
  del curs (restr. 6) i tutoria **mai** a primera/última (restr. 5).

- **Hores fixades exemptes** (`fixar_horari` + `horari_fixat`). Les hores posades a mà
  compten com a **context** però **no es validen** contra cap regla de política (ni
  entre elles ni contra les seves desiderates). Helpers:
  - `self.slots_fixats_per_prof` + `self._es_fixat(p,d,h)` → exempció **per professor**:
    màx hores/dia (límit = `max(base, fixades)`), descans 12 h (parelles totes-fixades),
    `no_disponible` (slots fixats) i penalització `prefereix_no` (slots fixats).
  - `self.slots_fixats_per_modul` + `self._modul_dia_fixat(m,dia)` → exempció de les
    **regles de posició** (restr. 5 i 6): si el mòdul té una hora fixada aquell dia, no
    se li valida la posició (FOL/anglès/tutoria).
  - Les impossibilitats físiques (professor/aula/curs a dos llocs alhora) NO s'eximeixen
    mai. Tot plegat només actua amb `fixar_horari` actiu.

- **`ignora_hores_grogues`** (opció de `/api/solve`, API 1.6.0): quan és certa, no
  s'afegeix la penalització de `prefereix_no` (desiderata tipus 1, grogues). Les
  vermelles (tipus 2, `no_disponible`) segueixen sent dures.

- **Objectiu** (`Solver.py:~1236`, es minimitza):
  `10·hores_mortes + 20·preferències_no_respectades + 1·aules_no_preferides`.

## Desenvolupament

- **Tests**: `.venv/Scripts/python.exe -m pytest tests/test_api.py -q`
  (42 tests; triga ~7–8 min perquè resol el dataset real). Sempre passar-los després
  de tocar `Solver.py`.
- Entorn: `.venv` local. Dependències a `requirements.txt` / `requirements-dev.txt`.

## Desplegament (vegeu `DESPLEGAMENT.md`)

- **Docker local**: `docker compose up -d --build` → `http://localhost:8000`
  (docs a `/docs`, salut a `/api/health`). Contenidor `horaris-solver`,
  `restart: unless-stopped`.
- **Proxmox VM**: mateixa comanda; l'API a `http://<ip-vm>:8000`.
- L'editor s'hi connecta posant la URL a **vista Solver → Execució del solver**.
- Config per variables d'entorn a `docker-compose.yml`: `MAX_TEMPS_SOLVER` (defecte 7200s),
  `CORS_ORIGINS` (defecte `*`). Un sol procés (les feines async viuen en memòria).

## Git

Commits directes a `master`. Cap push a remot si no es demana. (Remot GitHub:
`XeviSanmartin/HorarisSolver`.)
