# ----------------------------------------------------------------------------
# Validador exhaustiu d'una graella d'horari.
#
# Font ÚNICA de veritat de la validació de propostes: enumera TOTS els
# incompliments d'una graella (un període de `dades.horari`) contra les
# restriccions del solver, amb missatges llegibles i localitzats. L'editor
# (frontend) el consumeix via `POST /api/validate-horari` i el mode
# `millorar_horari` l'utilitza com a porta abans d'optimitzar.
#
# Opera sobre les DADES PROCESSADES (`HorariData.genera_dades_processades()`),
# que ja porten els flags calculats (es_tutoria, es_fol/es_angles,
# primera_ultima_hora, restriccions no_disponible/prefereix_no, subgrups per
# curs, aula_gran, necessita_aula_gran, horari_disponible...), de manera que la
# validació queda alineada amb el que el solver realment imposa.
#
# Gravetat:
#   - 'dura'  → restricció dura: bloqueja la millora (HORARI_INVALID).
#   - 'tova'  → preferència ("prefereix no", desiderata tipus 1): només informa.
#
# Referència de restriccions: DOC_API_SOLVER.md, Part 5. Cada bloc porta la
# semàntica exacta del solver (Solver.py, afegir_restriccions).
# ----------------------------------------------------------------------------
from collections import defaultdict

DIES = ['dilluns', 'dimarts', 'dimecres', 'dijous', 'divendres']

# Codi de regla → nom llegible (per agrupar l'informe a l'editor).
REGLES = {
    'hores': 'Hores per assignació',
    'aula': "Ocupació d'aula",
    'curs': 'Solapament de curs',
    'forats': 'Hores mortes',
    'tutoria': 'Tutoria als extrems',
    'primera_ultima': 'Primera/última hora',
    'desiderata_dura': 'Hora no disponible',
    'max_diari': "Màxim d'hores diàries",
    'horari_disponible': 'Franja del curs',
    'aula_gran': 'Aula gran',
    'nomes_tardes': 'Aula només tardes',
    'simultani': 'Classe simultània',
    'coordinats': 'Mòduls coordinats',
    'desiderata_tova': 'Preferència del professor',
}


def valida_graella(dades_processades, graella, hores_txt=None):
    """Retorna la llista d'incompliments de `graella` (matriu [dia][hora][profe]).

    `dades_processades` = sortida de `HorariData.genera_dades_processades()`.
    `graella` = un període del camp `horari` cru (llista [dia][hora][profe] on
    cada cel·la és None o {modul, curs, aula, subgrup, suport, simultani}).
    `hores_txt` (opcional) = franges com a text ("08:00", ...) per als missatges.

    Cada incompliment és un dict {regla, gravetat, missatge, dia, hora}, ordenat
    per (dia, hora).
    """
    dp = dades_processades or {}
    professors = dp.get('professors', [])
    cursos = dp.get('cursos', [])
    moduls = dp.get('moduls', [])
    aules = dp.get('aules', [])
    config = dp.get('configuracio', {})
    n_dies = config.get('dies_setmana', len(DIES))
    n_hores = config.get('hores_per_dia', 11)
    coordinats = (config.get('moduls_especials', {}) or {}).get('moduls_coordinats', []) or []

    profe_per_idx = {p['index']: p for p in professors}
    modul_per_idx = {m['index']: m for m in moduls}
    curs_per_idx = {c['index']: c for c in cursos}
    aula_per_idx = {a['index']: a for a in aules}

    # --- Consultes per a missatges ------------------------------------------
    def nom_prof(i):
        return profe_per_idx.get(i, {}).get('nom', f'professor {i}')

    def nom_curs(i):
        return curs_per_idx.get(i, {}).get('nom', f'curs {i}')

    def nom_aula(i):
        return aula_per_idx.get(i, {}).get('nom', f'aula {i}')

    def etiq_modul(i):
        m = modul_per_idx.get(i)
        if not m:
            return f'mòdul {i}'
        return m.get('codi') or m.get('nom') or f'mòdul {i}'

    def nom_dia(d):
        return DIES[d] if 0 <= d < len(DIES) else f'dia {d + 1}'

    def nom_hora(h):
        if hores_txt and 0 <= h < len(hores_txt):
            return hores_txt[h]
        return f'hora {h + 1}'

    def sub_curt(s):
        return ' (A)' if s == 1 else ' (B)' if s == 2 else ''

    def curs_de(cella, modul):
        c = cella.get('curs')
        if c is not None:
            return c
        return modul_per_idx.get(modul, {}).get('curs', -1)

    def es_tutoria(idx):
        return bool(modul_per_idx.get(idx, {}).get('es_tutoria'))

    def primera_ultima(idx):
        m = modul_per_idx.get(idx, {})
        ov = m.get('primera_ultima_hora')
        if ov is not None:
            return bool(ov)
        return bool(m.get('es_fol') or m.get('es_angles'))

    def restriccions(profe):
        return profe_per_idx.get(profe, {}).get('restriccions', {}) or {}

    # --- Recull totes les cel·les ocupades ----------------------------------
    oc = []
    for dia in range(n_dies):
        fila_dia = graella[dia] if dia < len(graella or []) else None
        if not fila_dia:
            continue
        for hora in range(n_hores):
            franja = fila_dia[hora] if hora < len(fila_dia) else None
            if not franja:
                continue
            for profe_pos, cella in enumerate(franja):
                if not cella:
                    continue
                # Dos formats: editor (indexat per professor, la posició ÉS l'índex
                # del professor) i pla (llista de cel·les amb camp 'profe'). El camp
                # 'profe' explícit mana; si no, s'usa la posició.
                profe = cella.get('profe', profe_pos)
                modul = cella.get('modul')
                oc.append({
                    'dia': dia, 'hora': hora, 'profe': profe,
                    'modul': modul, 'curs': curs_de(cella, modul),
                    'aula': cella.get('aula', -1), 'subgrup': cella.get('subgrup', 3),
                    'suport': bool(cella.get('suport')), 'simultani': bool(cella.get('simultani')),
                })

    incompliments = []

    def afegeix(regla, gravetat, missatge, dia=-1, hora=-1):
        incompliments.append({'regla': regla, 'gravetat': gravetat,
                              'missatge': missatge, 'dia': dia, 'hora': hora})

    def dur(regla, missatge, dia=-1, hora=-1):
        afegeix(regla, 'dura', missatge, dia, hora)

    def tou(regla, missatge, dia=-1, hora=-1):
        afegeix(regla, 'tova', missatge, dia, hora)

    # ========================================================================
    # 1 — Hores per assignació: cada assignació d'un professor ha de tenir
    #     col·locades exactament les hores previstes; cap cel·la òrfena.
    # ========================================================================
    for p in professors:
        p_idx = p['index']
        celles_profe = [c for c in oc if c['profe'] == p_idx]
        usades = [False] * len(celles_profe)
        for a in (p.get('moduls') or []):
            a_idx, a_sub, a_aula = a.get('index'), a.get('subgrup', 3), a.get('aula', -1)
            coincidents = [i for i, c in enumerate(celles_profe)
                           if not usades[i] and c['modul'] == a_idx
                           and c['subgrup'] == a_sub and c['aula'] == a_aula]
            for i in coincidents:
                usades[i] = True
            previstes, posades = a.get('hores', 0), len(coincidents)
            if posades != previstes:
                etiq = f"{nom_prof(p_idx)}: {etiq_modul(a_idx)}{sub_curt(a_sub)} a {nom_aula(a_aula)}"
                detall = f"(en falten {previstes - posades})" if posades < previstes else f"({posades - previstes} de més)"
                dur('hores', f"{etiq} — {posades} h col·locades de {previstes} {detall}")
        for i, c in enumerate(celles_profe):
            if not usades[i]:
                dur('hores',
                    f"{nom_prof(p_idx)}: hi ha una hora de {etiq_modul(c['modul'])}{sub_curt(c['subgrup'])} "
                    f"a {nom_aula(c['aula'])} ({nom_dia(c['dia'])} {nom_hora(c['hora'])}) que no correspon a cap assignació seva",
                    c['dia'], c['hora'])

    # ========================================================================
    # 2 — Ocupació d'aula: una aula no pot tenir dues classes alhora (excepte
    #     mòduls simultanis).
    # ========================================================================
    per_aula = defaultdict(list)
    for c in oc:
        per_aula[(c['dia'], c['hora'], c['aula'])].append(c)
    for (dia, hora, _aula), grup in per_aula.items():
        if len(grup) < 2 or all(c['simultani'] for c in grup):
            continue
        detall = ', '.join(f"{etiq_modul(c['modul'])}{sub_curt(c['subgrup'])} ({nom_prof(c['profe'])})" for c in grup)
        dur('aula', f"{nom_dia(dia)} {nom_hora(hora)} — {nom_aula(grup[0]['aula'])} té {len(grup)} classes alhora: {detall}",
            dia, hora)

    # ========================================================================
    # 3 — Solapament de curs: a la mateixa hora, un curs només pot tenir un grup
    #     sencer (subgrup 3) o els dos mig-grups complementaris (1 i 2).
    # ========================================================================
    per_curs_hora = defaultdict(list)
    for c in oc:
        per_curs_hora[(c['dia'], c['hora'], c['curs'])].append(c)
    for (dia, hora, curs), grup in per_curs_hora.items():
        if len(grup) < 2:
            continue
        if len(grup) == 2 and sorted(c['subgrup'] for c in grup) == [1, 2]:
            continue
        detall = ', '.join(f"{etiq_modul(c['modul'])}{sub_curt(c['subgrup'])}" for c in grup)
        dur('curs', f"{nom_dia(dia)} {nom_hora(hora)} — {nom_curs(curs)} té classes incompatibles alhora: {detall}",
            dia, hora)

    # ========================================================================
    # 4 — Hores mortes: les classes d'un curs (i de cada subgrup) han de ser
    #     consecutives dins del dia, sense forats.
    # ========================================================================
    vies_desdoblament = [('A', {1, 3}, ' (subgrup A)'), ('B', {2, 3}, ' (subgrup B)')]
    via_sencera = [('3', {3}, '')]
    reportats = set()
    for curs in cursos:
        curs_idx = curs['index']
        for dia in range(n_dies):
            te_desdoblament = any(c['curs'] == curs_idx and c['dia'] == dia and c['subgrup'] in (1, 2) for c in oc)
            for _nom, subgrups, etiq in (vies_desdoblament if te_desdoblament else via_sencera):
                hores = [c['hora'] for c in oc
                         if c['curs'] == curs_idx and c['dia'] == dia and c['subgrup'] in subgrups]
                if len(hores) < 2:
                    continue
                mn, mx = min(hores), max(hores)
                ocupades = set(hores)
                buides = [h for h in range(mn, mx + 1) if h not in ocupades]
                if not buides:
                    continue
                clau = (curs_idx, dia, tuple(buides))
                if clau in reportats:
                    continue
                reportats.add(clau)
                franges = ', '.join(nom_hora(h) for h in buides)
                dur('forats', f"{nom_curs(curs_idx)}{etiq} — {nom_dia(dia)}: hora morta a {franges}", dia, mn)

    # ========================================================================
    # 5 i 6 — Posició al dia. Totes dues es calculen sobre l'ocupació del GRUP
    # SENCER (qualsevol subgrup), igual que el solver (te_classe / grup_ocupat).
    # ========================================================================
    grup_hores = defaultdict(set)
    for c in oc:
        grup_hores[(c['curs'], c['dia'])].add(c['hora'])

    # 5 — Tutoria: NO pot ser a la primera ni l'última hora efectiva del grup.
    for c in oc:
        if not es_tutoria(c['modul']):
            continue
        hores = grup_hores.get((c['curs'], c['dia']))
        if not hores:
            continue
        if c['hora'] == min(hores) or c['hora'] == max(hores):
            dur('tutoria',
                f"{nom_curs(c['curs'])} — {nom_dia(c['dia'])} {nom_hora(c['hora'])}: la tutoria "
                f"({etiq_modul(c['modul'])}) no pot anar a la primera ni l'última hora del dia del grup",
                c['dia'], c['hora'])

    # 6 — Mòduls "primera/última hora": han d'anar a un extrem del dia del grup,
    # és a dir, sense cap classe del grup (que no sigui el mòdul) ABANS (inici) o
    # DESPRÉS (final) de les seves hores.
    per_modul_dia = defaultdict(list)
    for c in oc:
        if not primera_ultima(c['modul']):
            continue
        per_modul_dia[(c['modul'], c['curs'], c['dia'])].append(c)
    for (modul, curs, dia), grup in per_modul_dia.items():
        h_mod = {x['hora'] for x in grup}
        no_mod = grup_hores.get((curs, dia), set()) - h_mod
        max_mod, min_mod = max(h_mod), min(h_mod)
        inici = all(g >= max_mod for g in no_mod)   # res del grup abans de l'última hora del mòdul
        final = all(g <= min_mod for g in no_mod)   # res del grup després de la primera hora del mòdul
        if not (inici or final):
            dur('primera_ultima',
                f"{nom_curs(curs)} — {nom_dia(dia)}: {etiq_modul(modul)} ha d'anar a primera o "
                f"última hora del dia del grup, però queda entremig", dia, min_mod)

    # ========================================================================
    # 7 — Desiderata: no disponible (tipus 2) = dura; prefereix no (tipus 1) = tova.
    # ========================================================================
    for c in oc:
        restr = restriccions(c['profe'])
        slot = (c['dia'], c['hora'])
        # Les restriccions poden venir com a tuples o llistes; normalitzem.
        no_disp = {tuple(x) for x in restr.get('no_disponible', [])}
        pref_no = {tuple(x) for x in restr.get('prefereix_no', [])}
        if slot in no_disp:
            dur('desiderata_dura',
                f"{nom_prof(c['profe'])} té classe ({etiq_modul(c['modul'])}) {nom_dia(c['dia'])} {nom_hora(c['hora'])}, "
                f"una hora marcada com a NO disponible", c['dia'], c['hora'])
        elif slot in pref_no:
            tou('desiderata_tova',
                f"{nom_prof(c['profe'])} té classe {nom_dia(c['dia'])} {nom_hora(c['hora'])}, "
                f"una hora que prefereix no fer", c['dia'], c['hora'])

    # ========================================================================
    # 8 — Màxim d'hores diàries per professor (6, o 7 amb `7hores`).
    # ========================================================================
    for p in professors:
        p_idx = p['index']
        max_dia = 7 if p.get('7hores') else 6
        for dia in range(n_dies):
            n = sum(1 for c in oc if c['profe'] == p_idx and c['dia'] == dia)
            if n > max_dia:
                dur('max_diari', f"{nom_prof(p_idx)} té {n} hores {nom_dia(dia)} (màxim {max_dia})", dia, 0)

    # ========================================================================
    # 15 — Franja del curs (horari_disponible).
    # ========================================================================
    for curs in cursos:
        disp = curs.get('horari_disponible')
        if not disp:
            continue
        permesos = {(s.get('dia'), s.get('hora')) for s in disp}
        for c in oc:
            if c['curs'] != curs['index']:
                continue
            if (c['dia'], c['hora']) not in permesos:
                dur('horari_disponible',
                    f"{nom_curs(curs['index'])} té classe ({etiq_modul(c['modul'])}) {nom_dia(c['dia'])} {nom_hora(c['hora'])}, "
                    f"fora de la franja horària permesa del curs", c['dia'], c['hora'])

    # ========================================================================
    # 16 — Aula gran: grup sencer que necessita aula gran a una aula petita.
    # ========================================================================
    for c in oc:
        if c['subgrup'] != 3:
            continue
        aula = aula_per_idx.get(c['aula'])
        curs = curs_per_idx.get(c['curs'])
        necessita_gran = (curs.get('necessita_aula_gran', True) if curs else True) is not False
        if aula and aula.get('aula_gran') is False and necessita_gran:
            dur('aula_gran',
                f"{nom_curs(c['curs'])} (grup sencer) a {nom_aula(c['aula'])} {nom_dia(c['dia'])} {nom_hora(c['hora'])}: "
                f"és una aula petita i el grup necessita aula gran", c['dia'], c['hora'])

    # ========================================================================
    # 17 — Aula només tardes (el solver imposa hora < 6).
    # ========================================================================
    for c in oc:
        aula = aula_per_idx.get(c['aula'])
        if aula and aula.get('nomes_tardes') and c['hora'] < 6:
            dur('nomes_tardes',
                f"{nom_aula(c['aula'])} només és disponible a la tarda, però hi ha classe {nom_dia(c['dia'])} {nom_hora(c['hora'])} "
                f"({nom_curs(c['curs'])}, {etiq_modul(c['modul'])})", c['dia'], c['hora'])

    # ========================================================================
    # 12 — Classe simultània sense parella (mateix mòdul, hora i aula).
    # ========================================================================
    for c in oc:
        if not c['simultani']:
            continue
        te_parella = any(x is not c and x['simultani'] and x['modul'] == c['modul']
                         and x['dia'] == c['dia'] and x['hora'] == c['hora'] and x['aula'] == c['aula']
                         for x in oc)
        if not te_parella:
            dur('simultani',
                f"{etiq_modul(c['modul'])} està marcat com a simultani però no té cap classe parella "
                f"{nom_dia(c['dia'])} {nom_hora(c['hora'])} a {nom_aula(c['aula'])}", c['dia'], c['hora'])

    # ========================================================================
    # 13 — Mòduls coordinats: han d'ocupar els mateixos slots (dia, hora).
    # ========================================================================
    for grup in coordinats:
        slots_per_modul = {}
        for midx in (grup.get('moduls') or []):
            slots = {(c['dia'], c['hora']) for c in oc if c['modul'] == midx}
            if slots:
                slots_per_modul[midx] = slots
        placats = list(slots_per_modul.keys())
        if len(placats) < 2:
            continue
        referencia = slots_per_modul[placats[0]]
        if any(slots_per_modul[m] != referencia for m in placats):
            noms = ', '.join(etiq_modul(m) for m in placats)
            dur('coordinats',
                f"El grup coordinat «{grup.get('nom', 'sense nom')}» no està sincronitzat: {noms} no coincideixen d'hora")

    incompliments.sort(key=lambda v: (v['dia'], v['hora']))
    return incompliments
