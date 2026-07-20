# Documentació tècnica — Solver d'horaris Switch2

> Per al developer de frontend que necessita llegir/escriure els JSONs correctament.

---

## Visió general del flux

```
Solver.json
    │
    ▼
horari_solver.py        ← Preprocesador: enriqueix i valida les dades
    │
    ▼
dades_solver_processades.json
    │
    ▼
Solver.py               ← Solver OR-Tools: resol l'horari
    │
    ▼
solucio_horaris.json    ← Resultat final
    │
    ▼
solucio_horaris_compatible.hor  ← Resultat en format Solver.json (per reimportar)
```

---

## Part 1 — JSON d'entrada: `Solver.json`

Aquest és el fitxer que edita l'usuari (o l'app frontend) i que el preprocesador llegeix.

### Estructura de primer nivell

```json
{
  "app": "Editor d'horaris",
  "versioMajor": 4,
  "versioMenor": 59,
  "dataHora": 1753989240915,
  "autor": "",
  "comentaris": "",

  "professors":    [...],
  "cursos":        [...],
  "moduls":        [...],
  "aules":         [...],
  "especialitats": [...],
  "horari":        [...],

  "moduls_coordinats": { "grups": [...] },
  "projectes":         [...],
  "horaris_projectes": [...]
}
```

---

### `professors[]`

Cada element descriu un professor i les assignatures que imparteix.

```json
{
  "index":      0,
  "actiu":      true,
  "nom":        "Artur Juvé",
  "nomCurt":    "Artur",
  "especialitat": 0,
  "comentaris": "",
  "tutorCurs":  2,
  "7hores":     false,
  "DiesLliures": false,
  "controlable": false,

  "desiderata": [
    { "dia": 1, "hora": 5, "tipus": 2 },
    { "dia": 4, "hora": 9, "tipus": 1 }
  ],

  "moduls": [
    {
      "index":    27,
      "hores":    2,
      "aula":     7,
      "subgrup":  3,
      "suport":   false,
      "simultani": false,
      "particio": []
    }
  ]
}
```

| Camp | Tipus | Descripció |
|---|---|---|
| `index` | int | Identificador únic del professor |
| `actiu` | bool | Si `false`, el solver l'ignora |
| `nom` | string | Nom complet |
| `nomCurt` | string | Nom curt per mostrar a l'horari |
| `especialitat` | int | Índex a `especialitats[]` |
| `tutorCurs` | int | Índex del curs del qual és tutor (`-1` si no n'és) |
| `7hores` | bool | Pot fer fins a 7 hores en un dia (per defecte màx. 6) |
| `DiesLliures` | bool | Pot tenir fins a 1 dia lliure entre dimarts i dijous |
| `controlable` | bool | Si `false`, no s'apliquen restriccions de dies lliures ni dll/div |
| `desiderata` | array | Preferències horàries del professor |
| `moduls` | array | Assignatures que imparteix i en quines condicions |

#### `desiderata[]`

```json
{ "dia": 1, "hora": 5, "tipus": 2 }
```

| Camp | Valors | Descripció |
|---|---|---|
| `dia` | 0–4 | 0=dll, 1=dm, 2=dc, 3=dj, 4=dv |
| `hora` | 0–12 | 0=08h, 1=09h, 2=10h, ..., 10=18h, 11=19h, 12=20h |
| `tipus` | 1 o 2 | `1`=prefereix que no (soft), `2`=NO disponible (hard) |

#### `moduls[]` del professor

Cada entrada és una assignació concreta: el professor X imparteix el mòdul Y a un subgrup determinat.

```json
{
  "index":     27,
  "hores":     2,
  "aula":      7,
  "subgrup":   3,
  "suport":    false,
  "simultani": false,
  "particio":  []
}
```

| Camp | Tipus | Descripció |
|---|---|---|
| `index` | int | Índex del mòdul a `moduls[]` |
| `hores` | int | Hores setmanals a assignar |
| `aula` | int | Índex de l'aula **preferida** a `aules[]` (`-1` = qualsevol). Vegeu la nota d'aules a sota: si el mòdul té `aules_possibles`, aquesta aula és només una preferència suau i el solver pot reubicar-hi la classe; si no en té, l'hora es fixa en aquesta aula |
| `subgrup` | 1, 2 o 3 | `1`=1r mig grup, `2`=2n mig grup, `3`=grup sencer |
| `suport` | bool | És professor de suport (assisteix a un altre professor) |
| `simultani` | bool | Aquesta classe ha de passar simultàniament amb una altra del mateix mòdul |
| `particio` | array | (Experimental) Preferències de distribució d'hores en blocs |

> **Nota subgrups:** Si un mòdul té 3 entrades (una amb `subgrup:3`, una amb `subgrup:1` i una amb `subgrup:2`), significa que el professor fa el grup sencer I els dos mig-grups en franges separades.

---

### `moduls[]`

Catàleg de totes les assignatures. No inclou les hores ni els professors (això va a `professors[].moduls`).

```json
{
  "index":       0,
  "codi":        "M6-0612",
  "nom":         "Desenvolupament entorn client",
  "curs":        6,
  "especialitat": 1,
  "horari_disponible": [ { "dia": 0, "hora": 0 }, { "dia": 0, "hora": 1 } ],
  "aules_possibles":   [2, 7]
}
```

| Camp | Tipus | Descripció |
|---|---|---|
| `index` | int | Identificador únic |
| `codi` | string | Codi oficial del mòdul |
| `nom` | string | Nom complet |
| `curs` | int | Índex del curs a `cursos[]` |
| `especialitat` | int | Índex a `especialitats[]` |
| `horari_disponible` | array | Slots `{dia, hora}` on es pot impartir el mòdul (p. ex. només matins o només tardes). **Buit = qualsevol hora.** S'interseca amb l'`horari_disponible` del curs. L'editor omple aquest camp amb els commutadors Matí/Tarda o marcant slots concrets |
| `aules_possibles` | array | Índexs de les aules on es pot impartir, per requeriments d'espai o equipament. **Buit = comportament clàssic:** cada hora es fixa a l'aula preferida de la seva assignació (`professors[].moduls[].aula`), o a qualsevol si és `-1`. **No buit = restricció dura d'espai:** el solver pot triar qualsevol aula d'aquest conjunt per a cada hora, tot **prioritzant** (preferència suau) l'aula preferida de l'assignació. Així una aula saturada no condemna el solver (INFEASIBLE): pot reubicar la classe en una altra aula permesa. Un sol element = aula fixa |

> **Codis especials detectats automàticament pel preprocesador:**
> - `TUTORIA` al codi o nom → tutoria
> - `AN-` al codi, o `anglès` al nom, o `especialitat == 3` → anglès
> - `FOL` al nom, o `especialitat == 2` → FOL
> - `SO-` al codi, o `sostenibilitat` al nom → sostenibilitat
> - `DI-` al codi, o `digitalitzacio` al nom → digitalització

---

### `cursos[]`

```json
{
  "index":  0,
  "actiu":  true,
  "nom":    "ASIX1",
  "color":  [250, 200, 200],
  "aula":   1,
  "horari_disponible": [],
  "necessita_aula_gran": true
}
```

| Camp | Tipus | Descripció |
|---|---|---|
| `index` | int | Identificador únic |
| `actiu` | bool | Si `false`, el solver l'ignora |
| `nom` | string | Nom del curs |
| `color` | [R, G, B] | Color per mostrar a la UI (valors 0–255) |
| `aula` | int | Índex de l'aula principal a `aules[]` |
| `horari_disponible` | array | Si buit, el curs pot tenir classe a qualsevol hora. Si té elements, **només** pot tenir classe als slots indicats |
| `necessita_aula_gran` | bool | Per defecte `true`. Si `true`, les classes de **grup sencer** (`subgrup:3`) només poden anar a aules grans (`aula_gran:true`). Si `false`, el grup té pocs alumnes i hi cap a **qualsevol** aula, també sencer |

#### `horari_disponible[]` (opcional)

```json
[
  { "dia": 0, "hora": 0 },
  { "dia": 0, "hora": 1 }
]
```

Restricció de franges disponibles per al curs. Útil per a cursos de tarda o amb horari parcial.

**Cursos actuals (9):**

| Index | Nom |
|---|---|
| 0 | ASIX1 |
| 1 | ASIX2 |
| 2 | DAMW A |
| 3 | DAMW B |
| 4 | DAMW C |
| 5 | DAM2 |
| 6 | DAW2 |
| 7 | CIBER |
| 8 | DAMW D |

---

### `aules[]`

```json
{
  "index":         0,
  "actiu":         true,
  "nom":           "Aula 3.01",
  "aula_gran":      true,
  "nomes_tardes":   false
}
```

| Camp | Tipus | Descripció |
|---|---|---|
| `index` | int | Identificador únic |
| `actiu` | bool | Si `false`, el solver l'ignora |
| `nom` | string | Nom per mostrar |
| `aula_gran` | bool | Per defecte `true`: hi cap un grup sencer. Si `false`, és una aula **petita**: només hi caben desdoblaments (`subgrup:1`/`2`) o grups sencers amb `necessita_aula_gran:false`. Substitueix l'antic `nomes_subgrups` (`aula_gran = !nomes_subgrups`) |
| `nomes_tardes` | bool | Si `true`, l'aula **només** disponible des de l'hora 6 (14:00) en endavant |

**Aules actuals (8):**

| Index | Nom | Restricció |
|---|---|---|
| 0 | Aula 3.01 | — |
| 1 | Aula 3.02 | — |
| 2 | Aula 3.03 | — |
| 5 | Aula 3.04 | — |
| 6 | Aula 3.05 | — |
| 7 | Aula 3.10 | — |
| 8 | Aula 3.02 (grup B) | — |
| 9 | Aula 3.02 (grup C) | — |

> **Atenció:** Els índexs no són correlatius (va de l'2 al 5 directament). Usa sempre el camp `index` per referenciar.

---

### `especialitats[]`

```json
{
  "index": 0,
  "actiu": true,
  "codi":  "507",
  "nom":   "Informàtica"
}
```

**Especialitats actuals (4):**

| Index | Codi | Nom |
|---|---|---|
| 0 | 507 | Informàtica |
| 1 | 627 | Sistemes i aplicacions informàtiques |
| 2 | 505 | Formació i orientació laboral |
| 3 | ANG | Anglès |

---

### `horari[]` (pre-assignat)

Hores que ja estan col·locades a l'editor abans que el solver comenci. Si a
`/api/solve` s'activa `opcions.fixar_horari`, el solver **no les pot moure**:
les manté a la seva posició exacta i només col·loca la resta d'hores. Això
redueix l'espai de cerca i el temps de resolució.

**Format de l'editor (el que exporta l'app):** matriu de 4 nivells
`horari[periode][dia][hora]` → llista de cel·les (una posició per professor,
amb `null` als buits). L'editor exporta 5 períodes; el solver només fa servir
el que indica `opcions.periode` (per defecte el 0).

Cada cel·la ocupada és (usa índexs, no noms):

```json
{
  "modul":    61,
  "curs":     4,
  "aula":     6,
  "subgrup":  3,
  "suport":   false,
  "simultani": false,
  "profe":    1
}
```

També s'accepta el format pla `horari[dia][hora]` → cel·la (objecte o `null`).

**Validació:** el preprocessador descarta amb un advertiment les cel·les
incoherents (professor inexistent o inactiu, mòdul no assignat al professor amb
aquell subgrup, slot fora del rang 0–10 o fora de l'horari disponible del curs,
aula incompatible). Si l'aula de la cel·la no coincideix amb l'aula preferida de
l'assignació, es fixa l'hora amb l'aula preferida. Si es fixen **més hores que
les que té l'assignació**, s'avisa que l'horari serà infactible.

> **Atenció:** les hores fixades han de complir totes les restriccions dures
> (FOL/anglès a primera o última hora, tutoria mai a primera ni última, sense
> forats al curs...). Si les contradiuen, el resultat serà `INFEASIBLE`.

---

### Camps opcionals de primer nivell

#### `moduls_coordinats`

Grups de mòduls que **han d'anar a la mateixa hora** (ex: FOL dels 3 cursos DAMW a la vegada).

```json
{
  "grups": [
    {
      "nom": "FOL DAMW",
      "moduls": [5, 12, 19]
    }
  ]
}
```

#### `projectes`

Array d'índexs de mòduls que són "projecte final" i **només** poden anar als slots de `horaris_projectes`.

```json
[37, 52, 65]
```

#### `horaris_projectes`

Slots permesos per als mòduls de projecte.

```json
[
  { "dia": 3, "hora": 8 },
  { "dia": 3, "hora": 9 },
  { "dia": 4, "hora": 8 }
]
```

---

## Part 2 — JSON processat: `dades_solver_processades.json`

Generat per `horari_solver.py`. El frontend **no necessita escriure aquest fitxer** però pot ser útil llegir-lo per debug.

Transforma el `Solver.json` afegint camps calculats que el solver OR-Tools necessita.

```json
{
  "professors":    [...],
  "moduls":        [...],
  "cursos":        [...],
  "aules":         [...],
  "especialitats": [...],
  "agrupacions":   [...],
  "horari_fixat":  [...],
  "configuracio":  { ... }
}
```

### Diferències respecte a `Solver.json`

**professors** — afegeix:
- `nom_curt` en lloc de `nomCurt`
- `tutor_curs` en lloc de `tutorCurs`
- `restriccions: { no_disponible: [[dia,hora],...], prefereix_no: [[dia,hora],...] }` — desiderata ja separats per tipus
- `moduls[]` ja inclou el camp `simultani` i `particio`

**moduls** — afegeix:
- `es_tutoria`: bool
- `es_fol`: bool
- `es_angles`: bool
- `es_sostenibilitat`: bool
- `es_digitalizacio`: bool
- `professors_assignats`: [idx, idx, ...]

**cursos** — afegeix:
- `aula_principal` en lloc de `aula`
- `moduls`: [idx, idx, ...] — llista d'índexs de mòduls del curs
- `subgrups`: [1, 2, 3] — quins subgrups té aquest curs
- `tutor_professor`: int — índex del professor tutor
- `necessita_aula_gran`: bool — es propaga tal com entra (per defecte `true`)

**aules** — igual però afegeix `aula_gran` i `nomes_tardes` si no hi eren.

**agrupacions** — parelles de mòduls fusionables (sostenibilitat/digitalització):

```json
[[modul_idx_1, modul_idx_2], ...]
```

**horari_fixat** — hores pre-assignades del camp `horari` de l'entrada, ja
normalitzades i validades (les cel·les incoherents s'han descartat amb un
advertiment). `aula: -1` vol dir "qualsevol de les possibles":

```json
[
  { "professor": 1, "modul": 61, "subgrup": 3, "dia": 0, "hora": 0, "aula": 6 },
  ...
]
```

**configuracio**:

```json
{
  "dies_setmana": 5,
  "hores_per_dia": 11,
  "moduls_especials": {
    "fol":             [idx, ...],
    "angles":          [idx, ...],
    "sostenibilitat":  [idx, ...],
    "digitalizacio":   [idx, ...],
    "suport":          [idx, ...],
    "simultaneos":     [idx, ...],
    "moduls_coordinats": [ { "nom": "...", "moduls": [idx,...] } ],
    "projectes":       [idx, ...]
  },
  "horaris_projectes": [ { "dia": 3, "hora": 8 }, ... ]
}
```

---

## Part 3 — JSON de sortida: `solucio_horaris.json`

Generat per `Solver.py`. **Aquest és el que llegeix el frontend per mostrar els horaris.**

```json
{
  "horari":     [...],
  "professors": [...],
  "aules":      [...],
  "stats":      { ... }
}
```

---

### `horari` — Vista per curs

**Estructura:** array 4D `[curs][dia][hora]` → array de classes en aquella franja.

```
horari[0]       → ASIX1
horari[0][0]    → ASIX1, Dilluns
horari[0][0][3] → ASIX1, Dilluns, 11:00-12:00
```

**Indexació:**
- Dimensió 1 (`curs`): índex del curs (0–8, seguint l'ordre de `cursos[]`)
- Dimensió 2 (`dia`): 0=Dilluns, 1=Dimarts, 2=Dimecres, 3=Dijous, 4=Divendres
- Dimensió 3 (`hora`): 0=08:00, 1=09:00, ..., 10=18:00 *(11 slots, índex 0 a 10)*

**Cada cel·la** és un array (pot tenir 0, 1 o 2 elements):
- `[]` → hora lliure
- `[{...}]` → una classe (grup sencer, `subgrup:3`)
- `[{...}, {...}]` → desdoblament (subgrup 1 i subgrup 2 simultàniament)

**Objecte classe:**

```json
{
  "modul":           "Planificació i administració de xarxes",
  "modul_index":     18,
  "professor":       "Núria Galí",
  "professor_index": 3,
  "aula":            "Aula 3.04",
  "aula_index":      5,
  "subgrup":         1
}
```

| Camp | Tipus | Descripció |
|---|---|---|
| `modul` | string | Nom del mòdul (per mostrar) |
| `modul_index` | int | Índex del mòdul (per referenciar) |
| `professor` | string | Nom del professor (per mostrar) |
| `professor_index` | int | Índex del professor |
| `aula` | string | Nom de l'aula (per mostrar) |
| `aula_index` | int | Índex de l'aula |
| `subgrup` | 1, 2 o 3 | `1`=1r mig grup, `2`=2n mig grup, `3`=grup sencer |

**Exemple de lectura (curs 0, dilluns, 11:00):**

```json
// horari[0][0][3] → ASIX1, Dilluns, 11:00-12:00

[
  {
    "modul": "Planificació i administració de xarxes",
    "modul_index": 18,
    "professor": "Núria Galí",
    "professor_index": 3,
    "aula": "Aula 3.04",
    "aula_index": 5,
    "subgrup": 1
  },
  {
    "modul": "Gestió de bases de dades",
    "modul_index": 20,
    "professor": "Narcís Falgueras",
    "professor_index": 9,
    "aula": "Aula 3.01",
    "aula_index": 0,
    "subgrup": 2
  }
]
```

> Aquí el grup ASIX1 está desdoblit: el subgrup 1 va a PAX amb Núria i el subgrup 2 va a GBD amb Narcís, **a la mateixa hora**.

---

### `professors` — Vista per professor

**Estructura:** array 3D `[professor][dia][hora]` → array de classes.

```
professors[3]       → Núria Galí
professors[3][0]    → Núria Galí, Dilluns
professors[3][0][3] → Núria Galí, Dilluns, 11:00-12:00
```

Igual que `horari` però des del punt de vista del professor. L'objecte classe afegeix el curs i **no inclou el professor** (ja se sap):

```json
{
  "modul":       "Planificació i administració de xarxes",
  "modul_index": 18,
  "curs":        "ASIX1",
  "curs_index":  0,
  "aula":        "Aula 3.04",
  "aula_index":  5,
  "subgrup":     1
}
```

> **Ús típic:** Per generar la graella d'un professor concret, itera `professors[professor_index][dia][hora]`.

---

### `aules` — Vista per aula

**Estructura:** array 3D `[aula][dia][hora]` → array de classes.

> **Atenció:** L'índex de la dimensió 0 **no és el `aula_index`** del JSON original, sinó la posició dins l'array `aules[]` de la sortida (que segueix l'ordre del `Solver.json`). Usa el camp `aula_index` de cada classe per identificar de quina aula es tracta.

L'objecte classe inclou professor, mòdul i curs, però **no l'aula** (ja se sap):

```json
{
  "modul":           "Gestió de bases de dades",
  "modul_index":     20,
  "professor":       "Narcís Falgueras",
  "professor_index": 9,
  "curs":            "ASIX1",
  "curs_index":      0,
  "subgrup":         2
}
```

> **Ús típic:** Per saber si una aula està ocupada a una hora concreta, comprova si `aules[aula_pos][dia][hora]` és un array no buit.

---

### `stats`

```json
{
  "temps_resolucio": 1285.5,
  "conflictes":      54196,
  "branques":        370795
}
```

| Camp | Descripció |
|---|---|
| `temps_resolucio` | Segons que ha trigat el solver |
| `conflictes` | Conflictes explorats per OR-Tools (mesura de dificultat) |
| `branques` | Branques explorades per OR-Tools |

---

## Part 4 — Taules de referència per al frontend

### Mapeig dia → nom

| Valor | Nom curt | Nom complet |
|---|---|---|
| 0 | Dll | Dilluns |
| 1 | Dm | Dimarts |
| 2 | Dc | Dimecres |
| 3 | Dj | Dijous |
| 4 | Dv | Divendres |

### Mapeig hora → franja

| Valor | Franja |
|---|---|
| 0 | 08:00–09:00 |
| 1 | 09:00–10:00 |
| 2 | 10:00–11:00 |
| 3 | 11:00–12:00 |
| 4 | 12:00–13:00 |
| 5 | 13:00–14:00 |
| 6 | 14:00–15:00 |
| 7 | 15:00–16:00 |
| 8 | 16:00–17:00 |
| 9 | 17:00–18:00 |
| 10 | 18:00–19:00 |

### Mapeig subgrup

| Valor | Significat | Quan passa |
|---|---|---|
| 1 | 1r mig grup | Classe desdoblada, primera meitat |
| 2 | 2n mig grup | Classe desdoblada, segona meitat |
| 3 | Grup sencer | Tots els alumnes del curs junts |

> Si a `horari[c][d][h]` hi ha dos elements amb `subgrup:1` i `subgrup:2`, mostra'ls com dues files paral·leles a la mateixa franja horària.

---

## Part 5 — Restriccions implementades al solver

Útil per entendre per què el resultat és com és.

| # | Restricció | Tipus |
|---|---|---|
| 1 | Cada professor té exactament les hores assignades | Hard |
| 2 | Un professor no pot estar en dos llocs alhora | Hard |
| 3 | Una aula no pot tenir dues classes simultànies (excepte `simultani:true`) | Hard |
| 4 | Un curs no pot tenir dos mòduls a la mateixa hora (excepte desdoblaments del mateix mòdul) | Hard |
| 4.1 | Les classes d'un curs han de ser consecutives (sense forats) | Hard |
| 4.2 | Els subgrups tampoc poden tenir hores mortes | Hard |
| 5 | Tutoria: mai a primera ni última hora del dia | Hard |
| 6 | FOL i Anglès: sempre a primera o última hora del dia | Hard |
| 7 | Desiderata `tipus:2`: professor NO disponible | Hard |
| 7s | Desiderata `tipus:1`: professor prefereix que no | Soft |
| 8 | Màxim 6 hores diàries per professor (`7hores:true` → màx. 7) | Hard |
| 9 | Un professor no pot tenir classe a 1a hora si el dia anterior en tenia a l'última | Hard |
| 10 | Professor `controlable:true`: ha de tenir classe dll i div | Hard |
| 10b | Professor `controlable:true` + `DiesLliures:false`: ha de tenir classe tots els dies | Hard |
| 11 | Subgrup1 + grup sencer ≤ 4 hores per dia per professor i curs | Hard |
| 12 | Mòduls `simultani:true`: s'han d'impartir exactament a la mateixa hora i aula | Hard |
| 13 | Mòduls coordinats (`moduls_coordinats`): han d'anar a la mateixa hora | Hard |
| 14 | Mòduls de projecte (`projectes`): només als slots de `horaris_projectes` | Hard |
| 15 | `horari_disponible` del curs: el curs no pot tenir classe fora d'aquells slots | Hard |
| 16 | Aula `aula_gran:false` (petita): no admet grup sencer (`subgrup:3`) d'un grup amb `necessita_aula_gran:true` | Hard |
| 17 | Aula `nomes_tardes:true`: no disponible abans de l'hora 6 (14:00) | Hard |
| 18 | Hores pre-assignades (`horari` + `opcions.fixar_horari`): es mantenen al seu slot exacte | Hard (opcional) |
| 19 | `horari_disponible` del mòdul: el mòdul només es pot impartir en aquells slots | Hard |
| 20 | `aules_possibles` del mòdul: cada hora del mòdul va a una aula del conjunt | Hard |

---

## Part 6 — Errors comuns a evitar

### Al llegir `solucio_horaris.json`

- **No assumeixis que `horari[c][d][h]` té exactament 1 element.** Pot ser buit `[]` o tenir 2 elements si hi ha desdoblament.
- **`professor_index` no és la posició a l'array `professors[]`.** És el camp `index` del professor al `Solver.json`. Usa el `professor_index` per mostrar el nom, però per accedir a `solucio.professors[]` usa la posició a l'array.
- **Les hores van de 0 a 10** (11 slots), no de 0 a 12. L'hora 11 (19:00) i 12 (20:00) del `Solver.json` no apareixen a la solució.

### Al escriure `Solver.json`

- **`moduls[].curs` ha de ser un índex vàlid de `cursos[]`.** Si un mòdul apunta a un curs inexistent, el preprocesador el deixarà sense curs i no s'assignarà.
- **El camp `aula` a `professors[].moduls[]` ha de ser un índex vàlid de `aules[].index`**, no la posició a l'array (que pot diferir).
- **Si un professor imparteix el mateix mòdul en subgrup 1 i subgrup 2**, han d'aparèixer com a **dues entrades separades** a `professors[i].moduls[]` amb `subgrup:1` i `subgrup:2` respectivament.
- **`simultani:true` requereix que el mateix `modul.index` aparegui en almenys dos professors** (o subgrups) amb `simultani:true`. Si només en té un, el solver no aplicarà la restricció.
