# API REST — Solver d'horaris Switch2

Servei HTTP que exposa el pipeline complet del solver d'horaris (preprocessament +
resolució amb OR-Tools CP-SAT). Construït amb FastAPI i desplegable a Vercel.

> **La documentació canònica de l'API és l'especificació OpenAPI**, que descriu
> cada endpoint, cada camp (amb tipus, rangs i descripcions) i cada resposta:
>
> - **Swagger UI (interactiva):** `https://<desplegament>/docs`
> - **ReDoc (lectura):** `https://<desplegament>/redoc`
> - **Espec JSON:** `https://<desplegament>/openapi.json` — còpia estàtica al
>   repositori: [`openapi.json`](openapi.json) (regenerable amb
>   `python scripts/exporta_openapi.py`; un test vigila que no quedi desfasada)
>
> Aquest document és una guia ràpida d'ús. L'estructura detallada dels JSON
> d'entrada (`Solver.json`) i de sortida (`solucio`) també està documentada a
> [`DOC_API_SOLVER.md`](DOC_API_SOLVER.md).

---

## Índex

1. [Execució en local](#execució-en-local)
2. [Documentació interactiva (Swagger)](#documentació-interactiva)
3. [Endpoints](#endpoints)
   - [`GET /api/health`](#get-apihealth)
   - [`POST /api/validate`](#post-apivalidate)
   - [`POST /api/preprocess`](#post-apipreprocess)
   - [`POST /api/solve`](#post-apisolve)
4. [Codis d'error](#codis-derror)
5. [Límits i consideracions de desplegament](#límits-i-consideracions-de-desplegament)
6. [Tests](#tests)

---

## Execució en local

```bash
# Requisits: Python 3.12
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # Linux/Mac

.venv/Scripts/uvicorn api.index:app --reload --port 8000
```

L'API queda disponible a `http://localhost:8000`.

## Documentació interactiva

Amb el servei en marxa:

| URL | Contingut |
|---|---|
| `/docs` | Swagger UI (provar els endpoints des del navegador) |
| `/redoc` | ReDoc (documentació de lectura) |
| `/openapi.json` | Especificació OpenAPI 3 completa |

L'arrel `/` redirigeix a `/docs`.

---

## Endpoints

### `GET /api/health`

Comprova que el servei i OR-Tools estan operatius.

**Resposta `200`:**

```json
{
  "estat": "ok",
  "versio_api": "1.0.0",
  "max_temps_solver": 280.0,
  "python": "3.12.4"
}
```

| Camp | Descripció |
|---|---|
| `max_temps_solver` | Límit dur (segons) que el servidor aplica a `max_time_seconds` |

```bash
curl https://<desplegament>/api/health
```

---

### `POST /api/validate`

Valida un `Solver.json` **sense construir ni resoldre el model**. És ràpid
(mil·lisegons) i serveix per detectar problemes abans de llançar una resolució.

**Cos de la petició:**

```json
{
  "dades": { ...contingut complet de Solver.json... },
  "periode": 0
}
```

`periode` (opcional, per defecte `0`) indica de quin període del camp
`dades.horari` s'extreuen les hores pre-assignades.

**Resposta `200`:**

```json
{
  "valid": false,
  "advertiments": [
    "Mòdul Projecte DAW no té cap professor assignat",
    "Cursos sense tutoria: CIBER"
  ],
  "estadistiques": {
    "total_professors": 18,
    "total_moduls": 76,
    "total_cursos": 9,
    "total_aules": 8,
    "moduls_fol": 3,
    "moduls_angles": 4,
    "moduls_sostenibilitat": 2,
    "moduls_digitalizacio": 2,
    "moduls_suport": 1,
    "moduls_simultaneos": 2,
    "tutories": 8,
    "subgrups_per_curs": { "0": [1, 2, 3], "1": [3] },
    "hores_fixades": 238
  }
}
```

> **Nota:** els `advertiments` **no bloquegen** la resolució: `/api/solve`
> s'executarà igualment i els inclourà a la resposta. Un JSON estructuralment
> incorrecte (camps obligatoris absents), en canvi, retorna `422`.

```bash
curl -X POST https://<desplegament>/api/validate \
  -H "Content-Type: application/json" \
  -d '{"dades": '"$(cat Solver.json)"'}'
```

---

### `POST /api/preprocess`

Retorna el JSON intermedi (`dades_solver_processades`) que el preprocessador
genera i el solver consumeix. Pensat per a **depuració**: permet veure què ha
entès el sistema (mòduls especials detectats, tutories, agrupacions
sostenibilitat/digitalització, subgrups per curs...).

**Cos de la petició:** igual que `/api/validate`.

**Resposta `200`:**

```json
{
  "dades_processades": {
    "professors": [...],
    "moduls": [...],
    "cursos": [...],
    "aules": [...],
    "especialitats": [...],
    "agrupacions": [[3, 17], ...],
    "configuracio": { "dies_setmana": 5, "hores_per_dia": 11, "moduls_especials": {...} }
  },
  "advertiments": [...]
}
```

L'estructura detallada està a `DOC_API_SOLVER.md`, part 2.

---

### `POST /api/solve`

Executa el pipeline complet: preprocessament → construcció del model CP-SAT →
resolució → formatació de la solució.

**Cos de la petició:**

```json
{
  "dades": { ...contingut complet de Solver.json... },
  "opcions": {
    "max_time_seconds": 60,
    "num_workers": 4,
    "incloure_compatible": false
  }
}
```

| Opció | Tipus | Per defecte | Descripció |
|---|---|---|---|
| `max_time_seconds` | float > 0 | `60` | Temps màxim de cerca. El servidor l'acota al seu límit (`max_temps_solver` de `/api/health`) |
| `num_workers` | int 1–16 | `4` | Threads de cerca paral·lela de CP-SAT |
| `incloure_compatible` | bool | `false` | Afegeix `solucio_compatible`: la solució en format `Solver.json`, reimportable a l'editor Switch2 |
| `fixar_horari` | bool | `false` | Manté **inamovibles** les hores pre-assignades del camp `dades.horari` (les col·locades a mà a l'editor): el solver només col·loca la resta. Redueix l'espai de cerca i el temps de resolució |
| `periode` | int ≥ 0 | `0` | Període del camp `dades.horari` del qual s'extreuen les hores pre-assignades (l'editor n'exporta 5) |

El camp `opcions` és opcional (s'apliquen els valors per defecte).

**Resposta `200` (sempre que les dades siguin vàlides, hi hagi solució o no):**

```json
{
  "estat": "FEASIBLE",
  "solucio": {
    "horari":     [...],
    "professors": [...],
    "aules":      [...],
    "stats": {
      "temps_resolucio": 58.3,
      "conflictes": 41205,
      "branques": 250034,
      "estat": "FEASIBLE",
      "objectiu": 4140.0
    }
  },
  "solucio_compatible": { ... },
  "advertiments": []
}
```

| `estat` | Significat | `solucio` |
|---|---|---|
| `OPTIMAL` | Solució òptima demostrada | ✔ |
| `FEASIBLE` | Solució vàlida trobada dins del temps (pot no ser l'òptima) | ✔ |
| `INFEASIBLE` | Les restriccions es contradiuen: **cap** horari possible | `null` |
| `UNKNOWN` | Temps esgotat sense conclusió — proveu amb més `max_time_seconds` | `null` |
| `MODEL_INVALID` | Error intern construint el model | `null` |

**Sobre `stats.objectiu`:** el solver minimitza
`10 × hores_mortes + 20 × preferències_no_respectades` (desiderata `tipus: 1`).
Com més baix, millor és l'horari.

**Sobre `fixar_horari` (hores inamovibles):** el flux recomanat és col·locar a
mà a l'editor les hores que voleu garantir, exportar l'horari i cridar
`/api/solve` amb `"fixar_horari": true`. El solver manté aquelles hores al seu
slot exacte (professor, mòdul, subgrup i aula) i resol la resta al voltant.
Consideracions:

- Les cel·les incoherents (professor inexistent, mòdul no assignat, slot fora
  de rang...) **es descarten amb un advertiment**, no bloquegen la resolució.
  `/api/validate` les llista i informa d'`hores_fixades` a les estadístiques.
- Les hores fixades han de complir les restriccions dures del solver (FOL i
  anglès a primera/última hora, tutoria mai a primera ni última, cursos sense
  forats...). Si les contradiuen, el resultat és `INFEASIBLE`.
- Sense `fixar_horari` (per defecte), el camp `horari` s'ignora i la resposta
  ho recorda amb un advertiment. Això manté el comportament històric amb
  fitxers que porten l'horari sencer fet a mà.

La guia completa (format, validacions, decisions de disseny) és a
[`HORES_FIXADES.md`](HORES_FIXADES.md).

**Estructura de `solucio`:** vegeu `DOC_API_SOLVER.md`, part 3
(`horari[curs][dia][hora]`, `professors[prof][dia][hora]`, `aules[aula][dia][hora]`).

```bash
# Amb el JSON en un fitxer de petició:
python - <<'EOF'
import json
peticio = {
    "dades": json.load(open("Solver.json", encoding="utf-8")),
    "opcions": {"max_time_seconds": 120, "incloure_compatible": True},
}
json.dump(peticio, open("peticio.json", "w", encoding="utf-8"), ensure_ascii=False)
EOF

curl -X POST https://<desplegament>/api/solve \
  -H "Content-Type: application/json" \
  --data @peticio.json \
  -o resposta.json
```

**Exemple en Python:**

```python
import json, requests

with open("Solver.json", encoding="utf-8") as f:
    dades = json.load(f)

resposta = requests.post(
    "https://<desplegament>/api/solve",
    json={"dades": dades, "opcions": {"max_time_seconds": 120}},
    timeout=300,
)
resultat = resposta.json()

if resultat["estat"] in ("OPTIMAL", "FEASIBLE"):
    horari = resultat["solucio"]["horari"]   # [curs][dia][hora] -> [classes]
else:
    print("Sense solució:", resultat["estat"], resultat["advertiments"])
```

---

## Codis d'error

| Codi | Quan | Cos |
|---|---|---|
| `422` | Cos de petició mal format (falta `dades`, `max_time_seconds` ≤ 0...) | Error de validació Pydantic estàndard |
| `422` | `dades` no té l'estructura mínima de `Solver.json` (p. ex. professor sense `index`) | `{"detail": {"error": "...", "detall": "KeyError: 'index'", "traca": "..."}}` |
| `500` | Error intern construint o resolent el model | `{"detail": {"error": "...", "detall": "...", "traca": "..."}}` |

Els estats `INFEASIBLE` i `UNKNOWN` **no són errors HTTP**: retornen `200` amb
`solucio: null`, perquè són resultats vàlids del solver.

---

## Límits i consideracions de desplegament

L'API està desplegada a **Vercel** com a funció serverless Python.

| Límit | Valor | Origen |
|---|---|---|
| Temps màxim de resolució | 280 s (configurable amb l'env `MAX_TEMPS_SOLVER`) | `maxDuration` de Vercel és 300 s al pla Hobby; es deixa marge per al preprocessament i la serialització |
| Mida màxima del cos de petició | 4,5 MB | Límit fix de Vercel (un `Solver.json` típic ocupa ~150 KB) |
| CPU | Compartida (Fluid Compute) | El mateix problema es resol més lentament que en local; ajusteu `max_time_seconds` |

**Recomanacions:**

- Comenceu amb `max_time_seconds: 60`. Amb les dades reals del centre, el solver
  troba una solució `FEASIBLE` en ~60 s en un portàtil modern; a Vercel pot
  necessitar més temps.
- Si rebeu `UNKNOWN`, torneu a provar amb més temps (fins a 280 s) o reduïu
  restriccions.
- Per a resolucions llargues (els 900 s del pipeline CLI original), executeu
  l'API en local o en un servidor propi: el codi és el mateix
  (`uvicorn api.index:app`).
- L'estat és **stateless**: cada petició és independent i no es guarda res al
  servidor.

**Fitxers de desplegament:**

| Fitxer | Funció |
|---|---|
| `api/index.py` | Aplicació FastAPI (entrada de Vercel) |
| `vercel.json` | Configuració de rutes i `maxDuration` |
| `requirements.txt` | Dependències de producció |
| `.vercelignore` | Fitxers exclosos del desplegament (dades locals, tests...) |

---

## Tests

La suite (`tests/test_api.py`, 32 tests) cobreix:

- **Endpoints**: health, redirecció a docs, OpenAPI, validate (dades reals,
  buides, invàlides), preprocess (estructura + **test de regressió** contra la
  sortida del pipeline CLI original).
- **Hores pre-assignades**: extracció i normalització d'`horari_fixat`,
  paràmetre `periode`, descarts amb advertiment, resolució amb `fixar_horari`
  (cada hora fixada apareix exactament al seu slot), fixació impossible (→
  `INFEASIBLE`) i comportament per defecte (s'ignoren amb avís).
- **Casos límit de solve**: opcions invàlides, dades infactibles (→
  `INFEASIBLE`), temps de resolució d'1 segon.
- **Invariants de la solució real** (es resol `Solver.json` de debò i es
  verifica cada restricció): cap professor a dos llocs alhora, hores exactes per
  professor, desiderata hard respectada, cursos sense forats, màxim 2 classes
  per slot (desdoblaments), aules sense solapaments, tutoria mai a primera ni
  última hora.
- **Format compatible**: estructura reimportable a l'editor.

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

> La suite triga ~2,5 min perquè resol el problema real dues vegades (una amb
> límit de 90 s compartida entre els tests d'invariants).
