# Hores pre-assignades (fixació d'horari)

> Funcionalitat afegida el juliol de 2026 (API 1.2.0). Permet partir d'unes
> quantes hores col·locades a mà que el solver ha de mantenir **inamovibles**,
> i que resolgui només la resta. Això redueix l'espai de cerca i el temps de
> resolució.

---

## Com s'utilitza

1. Col·loca a mà a l'editor Switch2 les hores que vols garantir.
2. Exporta l'horari (fitxer `.hor` / JSON): les hores queden al camp `horari`.
3. Crida `/api/solve` amb l'opció `fixar_horari`:

```json
POST /api/solve
{
  "dades": { ...export de l'editor... },
  "opcions": { "fixar_horari": true, "max_time_seconds": 120 }
}
```

El solver manté cada hora fixada al seu slot exacte (professor, mòdul, subgrup
i aula) i només col·loca la resta d'hores.

Abans de resoldre, pots comprovar amb `POST /api/validate` que les hores s'han
llegit bé: les estadístiques inclouen `hores_fixades` i els `advertiments`
llisten les cel·les descartades i el motiu.

## Opcions relacionades

| Opció | On | Per defecte | Descripció |
|---|---|---|---|
| `fixar_horari` | `opcions` de `/api/solve` | `false` | Activa la fixació de les hores del camp `dades.horari` |
| `periode` | `opcions` de `/api/solve` i primer nivell de `/api/validate` i `/api/preprocess` | `0` | Període del camp `horari` del qual s'extreuen les hores (l'editor n'exporta 5) |

## Decisions de disseny

- **És opt-in** (`fixar_horari` per defecte fals). Els fitxers existents ja
  porten l'horari sencer fet a mà al camp `horari` (el `Solver.json` de proves
  té 238 cel·les al període 0); fixar-ho tot per defecte hauria tornat
  infactibles o trivials totes les crides existents. Sense el flag, la resposta
  inclou un advertiment recordant que s'han detectat hores pre-assignades però
  que s'ignoren.
- **Validació amb advertiments, mai bloqueig.** Les cel·les incoherents es
  descarten amb un advertiment i la resolució continua:
  - professor inexistent o inactiu,
  - mòdul inexistent o no assignat al professor amb aquell subgrup,
  - slot fora del rang del solver (5 dies × 11 hores) o fora de l'horari
    disponible del curs,
  - aula incompatible amb el slot (només subgrups / només tardes),
  - hora repetida per al mateix professor al mateix slot.
- **Aula preferida.** Si l'aula de la cel·la no coincideix amb l'aula preferida
  de l'assignació del professor, es fixa l'hora amb l'aula preferida (el solver
  només crea variables per a l'aula preferida quan n'hi ha una) i s'avisa.
- **Excés d'hores.** Si es fixen més hores d'una assignació que les que té
  (`hores` del mòdul del professor), s'avisa que l'horari serà infactible, però
  no es bloqueja: el solver retornarà `INFEASIBLE`.
- **Les hores fixades han de complir les restriccions dures** del solver
  (FOL/anglès sempre a primera o última hora, tutoria mai a primera ni última,
  cursos sense forats, desiderata `tipus 2`...). Si l'horari fet a mà les
  contradiu, el resultat és `INFEASIBLE`.

## Format de les dades

**Entrada** (camp `horari` del Solver.json, format de l'editor): matriu
`horari[periode][dia][hora]` → llista de cel·les (una posició per professor,
`null` als buits). Cada cel·la ocupada:

```json
{ "modul": 61, "curs": 4, "aula": 6, "subgrup": 3,
  "suport": false, "simultani": false, "profe": 1 }
```

També s'accepta el format pla `horari[dia][hora]` → cel·la o `null`.

**Dades processades** (`/api/preprocess`): les cel·les vàlides es normalitzen a
la clau `horari_fixat` (`aula: -1` vol dir "qualsevol de les possibles"):

```json
[ { "professor": 1, "modul": 61, "subgrup": 3, "dia": 0, "hora": 0, "aula": 6 } ]
```

## Implementació

| Fitxer | Canvi |
|---|---|
| `horari_solver.py` | `_carrega_horari_fixat()` + `_afegeix_hora_fixada()`: llegeix el camp `horari` (tots dos formats), valida i normalitza a `horari_fixat`; advertiments a `valida_dades()`; `hores_fixades` a les estadístiques; paràmetre `periode` a `carrega_dades()` |
| `Solver.py` | `afegir_restriccions_horari_fixat()`: força `var == 1` per a cada slot fixat (variable exacta si l'aula és concreta; suma de variables per aula `== 1` si no); paràmetre `fixar_horari` a `executar()` |
| `api/index.py` | Camp `horari` tipat a `DadesSolver`; opcions `fixar_horari` i `periode` a `OpcionsSolve`; `periode` a `PeticioValidate`; advertiments informatius a `/api/solve`; versió API 1.2.0 |
| `tests/test_api.py` | 7 tests nous (32 en total): extracció i normalització, període buit, cel·les invàlides, resolució amb fixació (cel·la a cel·la), fixació impossible → `INFEASIBLE`, comportament per defecte |
| `DOC_API_SOLVER.md` | Secció `horari[]` reescrita amb el format real; `horari_fixat` a la part 2; restricció 18 a la taula |
| `API_REST.md` | Opcions noves, paràmetre `periode`, secció d'ús de `fixar_horari` |
| `openapi.json` | Regenerat (`scripts/exporta_openapi.py`) |
| `dades_solver_processades.json` | Regenerat (`python horari_solver.py`): ara inclou `horari_fixat` |

## Verificació

Suite completa en verd (32/32, ~3,5 min). El test clau, `test_solve_fixar_horari`,
fixa la solució real sencera i comprova **cel·la a cel·la** que el solver la
retorna idèntica; `test_solve_fixacio_impossible` comprova que un excés d'hores
fixades dona `INFEASIBLE` amb l'advertiment corresponent.

## Integració amb l'editor (C:\Git\Horaris)

La vista «Solver» de l'editor té un apartat **Execució del solver** que fa
servir aquesta funcionalitat de cap a cap: configura la URL del servidor
(localhost, VM del Proxmox o Vercel), el temps màxim, els fils de cerca i la
fixació de les hores ja col·locades al període actiu; llança la resolució en
segon pla (`POST /api/jobs`), en mostra el progrés, permet aturar-la conservant
la millor solució, i carrega la `solucio_compatible` al període actiu de
l'editor (amb punt de control a l'historial: es pot desfer amb Ctrl+Z).
