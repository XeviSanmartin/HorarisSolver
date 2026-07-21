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
- **`validador_graella.py`** — validador EXHAUSTIU d'una graella (`valida_graella`): font
  ÚNICA de veritat de la validació de propostes. ⚠️ Si l'afegeixes/mous, recorda copiar-lo
  al `Dockerfile` (línia `COPY ... validador_graella.py ./`).
- `exportar_html.py`, `switch2.py` — utilitats/exportació.
- Docs: **`DOC_API_SOLVER.md`**, `API_REST.md`, `openapi.json`, `DESPLEGAMENT.md`,
  `HORES_FIXADES.md`. Versió API actual: **1.10.0**.
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

- **Restriccions d'aula**: `aula_gran` (per defecte `True`; si és `False` és una aula
  petita i no admet grup sencer `subgrup==3` **d'un grup que necessiti aula gran** —
  vegeu més avall), `nomes_tardes` (només `hora>=6`). ⚠️ `aula_gran` **substitueix**
  l'antic `nomes_subgrups` (`aula_gran = not nomes_subgrups`); el codi encara llegeix
  `nomes_subgrups` com a fallback per a dades antigues.

- **`necessita_aula_gran` (grup/curs) = per defecte `True`.** Un grup sencer (`subgrup==3`)
  només es prohibeix a una aula petita (`aula_gran=False`) si el grup necessita aula gran.
  Els grups amb pocs alumnes (`necessita_aula_gran=False`) hi caben a **qualsevol** aula,
  també sencers. Filtre a la creació de variables (`Solver.py`, ~línia 320):
  `not es_aula_gran and subgrup==3 and curs_necessita_gran`.

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

- **Regles de posició** (per mòdul/curs):
  - **Primera/última hora del grup (restr. 6, reescrita).** Un mòdul marcat va a un
    **extrem del dia REAL del grup**: un bloc d'hores (de **qualsevol durada**, 1/2/3…)
    sense cap classe **d'aquell mateix curs** abans (si va al principi) o després (si va
    al final) — perquè els alumnes que no el cursen puguin arribar més tard o marxar
    abans. Es codifica amb l'ocupació del curs `grup_ocupat[(curs,dia,hora)]` i dues
    booleanes `inici`/`final` (només compta `grup[g] AND NOT fol[g]`, o sigui una ALTRA
    assignatura del grup; altres grups no hi compten). Aplica si `modul['primera_ultima_hora']`
    és `True`; si és `None`, es dedueix com abans (FOL/anglès pel nom/especialitat).
    ⚠️ La versió antiga forçava posició **absoluta** (hora 0 o l'última del dia) i **màx
    2 h**, cosa que per a 1r (matí) clavava el FOL a l'hora 0 i prohibia 3 h seguides.
  - Tutoria **mai** a primera/última (restr. 5).

- **Co-docència i ocupació (restr. 3 i 4).** Per als ALUMNES, un curs té una sola classe
  per slot: com a molt un mòdul de grup sencer, o un desdoblament (subgrup 1 + subgrup 2).
  Diversos professors del **MATEIX mòdul i subgrup** es tracten com UNA sola classe: les
  restr. 3 (aula) i 4 (curs) no els limiten (compten mòduls distints, no assignacions);
  només es prohibeix barrejar mòduls DIFERENTS no simultanis.

- **Co-docència explícita: cal `suport` per compartir slot (restr. 4b).** Sobre l'anterior,
  s'afegeix que **com a molt un professor NO-suport** per `(mòdul, subgrup, dia, hora)`.
  Així dos professors independents del mateix mòdul (p. ex. els que es reparteixen un
  **projecte intermodular**) **es col·loquen en franges diferents** (cadascú fa les seves
  hores); només comparteixen slot si algú està marcat com a **suport** (titular + suport,
  reunions). Els desdoblaments (subgrups A/B) segueixen en paral·lel. Sense això, el solver
  apilava professors del mateix mòdul al mateix slot i l'editor ho marcava com a conflicte.

- **Suport = acompanya el titular** (bloc a `afegir_restriccions`, prop dels projectes).
  Un professor de suport (`moduls[].suport=true`) només imparteix el mòdul a les hores en
  què un **titular** del mateix mòdul també el fa (`self.assig_es_suport` + literal
  d'assumpció `suport_p{p}`). Cas d'ús: una **reunió** es modela amb un titular i la resta
  de professors com a suport → tots segueixen el titular a la mateixa hora. També s'aplica
  als **mòduls simultanis** (`moduls_simultanis`), que ja tenien la seva pròpia coincidència.

- **Els flags `suport`/`simultani` es CONSERVEN a la sortida.** L'extracció de la solució i
  `genera_json_solucio_compatible` propaguen `suport`/`simultani` des de l'assignació
  (`_flags_assignacio` amb fallback per aula). ⚠️ Abans es forçaven a `False`, així que en
  carregar la solució a l'editor es perdia la co-docència i les reunions tornaven a donar
  conflicte. Afecta TOTS els mòduls amb suport/simultani.

- **`particio` = llista de particions PERMESES (disjunció).** Cada partició és una llista de
  longituds de blocs d'hores consecutives (un bloc per dia). Amb diverses, el solver crea
  un selector booleà per partició (exactament una activa) i **en realitza una**; amb una de
  sola es força; buit `[]` = repartiment lliure. ⚠️ Abans només s'aplicava `particio[0]`.
  L'editor ho desa com a **cadenes** ("2+1") i ho converteix a `[[int]]` en enviar-ho.

- **Hores fixades exemptes** (`fixar_horari` + `horari_fixat`). Les hores posades a mà
  compten com a **context** però **no es validen** contra cap regla de política (ni
  entre elles ni contra les seves desiderates). Helpers:
  - `self.slots_fixats_per_prof` + `self._es_fixat(p,d,h)` → exempció **per professor**:
    màx hores/dia (límit = `max(base, fixades)`), descans 12 h (parelles totes-fixades),
    `no_disponible` (slots fixats) i penalització `prefereix_no` (slots fixats).
  - `self.slots_fixats_per_modul` + `self._modul_dia_fixat(m,dia)` → exempció de les
    **regles de posició** (restr. 5 i 6): si el mòdul té una hora fixada aquell dia, no
    se li valida la posició (FOL/anglès/tutoria).
  - `self.fixats_mpdhs` + `self._var_es_fixada(m,p,d,h,s)` → exempció de l'**ocupació
    d'aula (restr. 3) i de curs (restr. 4)** entre hores fixades. Cas clau: una
    **reunió** amb molts professors al mateix mòdul/slot/aula (abans donava INFEASIBLE
    "hores fixades a mà de tothom"). Les fixades actuen com a context: el solver no hi
    pot posar un mòdul diferent que xoqui, però **sí afegir-s'hi** (mateix mòdul i
    subgrup → professor que s'incorpora a la reunió o suport que acompanya el titular).
  - Només queda dura entre fixades la restricció "un professor no pot ser a dos llocs
    alhora" (l'editor tampoc no permet dues classes del mateix professor en una cel·la).
    Tot plegat només actua amb `fixar_horari` actiu.

- **`ignora_hores_grogues`** (opció de `/api/solve`, API 1.6.0): quan és certa, no
  s'afegeix la penalització de `prefereix_no` (desiderata tipus 1, grogues). Les
  vermelles (tipus 2, `no_disponible`) segueixen sent dures.

- **Objectiu** (`Solver.py:~1236`, es minimitza):
  `10·hores_mortes + 20·preferències_no_respectades + 1·aules_no_preferides`.

- **Progrés en viu de les feines async.** El callback `_CallbackProgres` desa a cada
  solució millorada (màx 1/s) la millor **solució intermèdia** i **mètriques per entitat**
  (`_metriques`: hores mortes per professor i per curs, desiderata grogues incomplertes
  per professor). L'estat (`GET /api/jobs/{id}`) exposa `objectiu_actual`, `cota`
  (`BestObjectiveBound`), `gap` (`calcula_gap`), `te_solucio` i `metriques`. Nou endpoint
  **`GET /api/jobs/{id}/solucio`**: millor solució trobada fins ara en format compatible
  (`.hor`), descarregable mentre la feina encara corre. ⚠️ El càlcul de mètriques va dins
  un `try/except` a `resoldre` (i al callback): **mai** ha de tombar una resolució vàlida.

## Validar i millorar una proposta (`millorar_horari`, API 1.10.0)

- **Validació = font ÚNICA de veritat a `validador_graella.py`.** `valida_graella(dades_
  processades, graella, hores_txt)` enumera TOTS els incompliments d'una graella (un període
  del camp `horari` cru) amb missatges llegibles: `{regla, gravetat: dura|tova, missatge,
  dia, hora}`. Opera sobre les **dades processades** (flags `es_tutoria`, `primera_ultima_
  hora`, `restriccions.no_disponible/prefereix_no`, `aula_gran`, `subgrups`...), així queda
  alineada amb el que el solver imposa. ⚠️ **Les regles de POSICIÓ es calculen sobre
  l'ocupació del GRUP SENCER** (qualsevol subgrup), no per subgrup: tutoria mai a la
  primera/última hora efectiva; mòduls `primera_ultima_hora` a un extrem (sense classe del
  grup que no sigui el mòdul abans o després). Oracle de no-regressió: sobre una solució
  òptima del propi solver (obj 0) ha de donar **0 durs**.
- **Endpoint `POST /api/validate-horari`** → `{valid, total_durs, total_tous, incompliments,
  regles}`. És instantani (no resol res). L'editor el pinta al validador.
- **Porta de `millorar_horari` = la MATEIXA `valida_graella`** (a `_resol_solver`). Si hi ha
  incompliments **durs** → `HORARI_INVALID` amb els motius exhaustius. Ja **no** es fa servir
  la validació CP-SAT `valida_horari` com a porta (era lenta i menys granular; el mètode encara
  hi és però no s'usa per gating).
- **Garantia "mai pitjor"** (`Solver.resol_grid_fixat`). El *warm start* (`AddHint`) és
  best-effort: amb temps curt CP-SAT pot retornar una solució PITJOR que la proposta. Per
  evitar-ho, `_resol_solver` calcula l'objectiu de referència resolent la graella **fixada**
  i, si la millora no el supera, retorna la **proposta original** + advertiment. Si la graella
  fixada resulta infactible (rar; una global no coberta pel validador determinista), es retorna
  la millora tal qual.
- ⚠️ **Format editor al preprocessador.** `_carrega_horari_fixat`/`_afegeix_hora_fixada`
  accepten la graella de l'editor (`[dia][hora]` = **llista indexada per l'índex del
  professor**, cel·les SENSE camp `profe`): la POSICIÓ és el professor. Abans es llegia
  `cella.get('profe')` i es descartaven TOTES les cel·les de l'editor (warm start buit). El
  camp `profe`/`professor` explícit (format pla / `.hor`) segueix manant.

## Desenvolupament

- **Tests**: `.venv/Scripts/python.exe -m pytest tests/test_api.py -q`
  (54 tests; triga ~7–8 min perquè resol el dataset real). Sempre passar-los després
  de tocar `Solver.py`. Cobreixen, entre d'altres: co-docència (suport separa/ajunta),
  particions (buit/una/disjunció), primera/última hora (bloc a l'extrem, flag), gap i
  mètriques, i la solució intermèdia descarregable.
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
