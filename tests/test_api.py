"""Tests de l'API REST del solver d'horaris.

Execució:  .venv/Scripts/python.exe -m pytest tests/ -v

Els tests de /api/solve usen les dades reals (Solver.json) amb un límit de
temps curt; poden trigar 1-2 minuts en total.
"""
import copy
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

ARREL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ARREL)

from api.index import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope='session')
def dades_reals() -> dict:
    with open(os.path.join(ARREL, 'Solver.json'), encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='session')
def solucio_real(dades_reals):
    """Resol una vegada les dades reals i comparteix la solució entre tests."""
    resposta = client.post('/api/solve', json={
        'dades': dades_reals,
        'opcions': {'max_time_seconds': 90, 'num_workers': 8, 'incloure_compatible': True},
    })
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health():
    r = client.get('/api/health')
    assert r.status_code == 200
    cos = r.json()
    assert cos['estat'] == 'ok'
    assert cos['max_temps_solver'] > 0


def test_arrel_redirigeix_a_docs():
    r = client.get('/', follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers['location'] == '/docs'


def test_openapi_disponible():
    r = client.get('/openapi.json')
    assert r.status_code == 200
    rutes = r.json()['paths']
    for ruta in ('/api/health', '/api/validate', '/api/preprocess', '/api/solve'):
        assert ruta in rutes


def test_openapi_esquemes_documentats():
    """L'especificació ha de documentar els models d'entrada i sortida amb detall."""
    spec = client.get('/openapi.json').json()
    esquemes = spec['components']['schemas']
    for model in ('DadesSolver', 'Professor', 'ModulProfessor', 'Desiderata',
                  'ModulCataleg', 'Curs', 'Aula', 'Especialitat', 'OpcionsSolve',
                  'RespostaSolve', 'Solucio', 'StatsSolucio', 'DetallError',
                  'RespostaFeina', 'RespostaFeinaCreada'):
        assert model in esquemes, f'falta l\'esquema {model}'
    # Camps amb descripció i restriccions de rang
    desiderata = esquemes['Desiderata']['properties']
    assert desiderata['dia']['maximum'] == 4
    assert desiderata['tipus']['minimum'] == 1
    assert 'description' in desiderata['tipus']
    # Exemple complet de dades d'entrada
    assert 'example' in esquemes['DadesSolver']
    # Els endpoints documenten respostes d'error
    solve = spec['paths']['/api/solve']['post']
    assert '422' in solve['responses']
    assert '500' in solve['responses']


def test_openapi_estatic_actualitzat():
    """openapi.json del repositori ha d'estar sincronitzat amb l'app
    (regenerar amb scripts/exporta_openapi.py)."""
    from api.index import app
    with open(os.path.join(ARREL, 'openapi.json'), encoding='utf-8') as f:
        estatic = json.load(f)
    assert estatic == app.openapi()


# ---------------------------------------------------------------------------
# /api/validate
# ---------------------------------------------------------------------------

def test_validate_dades_reals(dades_reals):
    r = client.post('/api/validate', json={'dades': dades_reals})
    assert r.status_code == 200
    cos = r.json()
    assert isinstance(cos['valid'], bool)
    assert isinstance(cos['advertiments'], list)
    est = cos['estadistiques']
    assert est['total_professors'] > 0
    assert est['total_moduls'] > 0
    assert est['total_cursos'] > 0
    assert est['total_aules'] > 0


def test_validate_dades_buides():
    """Un JSON buit no és un error d'estructura: retorna advertiments."""
    r = client.post('/api/validate', json={'dades': {}})
    assert r.status_code == 200
    assert r.json()['valid'] is False


def test_validate_estructura_invalida():
    """Un professor sense camps obligatoris ha de donar 422 (validació d'esquema)."""
    r = client.post('/api/validate', json={'dades': {'professors': [{'nom': 'x'}]}})
    assert r.status_code == 422
    detall = r.json()['detail']
    # Error Pydantic estàndard: llista amb la ubicació dels camps que falten
    camps_absents = {e['loc'][-1] for e in detall if e['type'] == 'missing'}
    assert {'index', 'nomCurt', 'especialitat'} <= camps_absents


def test_validate_rangs_invalids():
    """Valors fora de rang (dia 7, tipus 5) han de donar 422."""
    dades = {'professors': [{
        'index': 0, 'actiu': True, 'nom': 'X', 'nomCurt': 'X', 'especialitat': 0,
        'desiderata': [{'dia': 7, 'hora': 3, 'tipus': 5}],
    }]}
    r = client.post('/api/validate', json={'dades': dades})
    assert r.status_code == 422


def test_validate_sense_cos():
    r = client.post('/api/validate', json={})
    assert r.status_code == 422  # falta el camp 'dades' (validació Pydantic)


# ---------------------------------------------------------------------------
# /api/preprocess
# ---------------------------------------------------------------------------

def test_preprocess_estructura(dades_reals):
    r = client.post('/api/preprocess', json={'dades': dades_reals})
    assert r.status_code == 200
    dp = r.json()['dades_processades']
    for clau in ('professors', 'moduls', 'cursos', 'aules', 'especialitats',
                 'agrupacions', 'configuracio'):
        assert clau in dp, f'falta la clau {clau}'
    config = dp['configuracio']
    assert config['dies_setmana'] == 5
    assert config['hores_per_dia'] == 11
    assert 'moduls_especials' in config


def _normalitza_ints(valor):
    """Converteix strings enters ("-1") a int, recursivament.

    Les dades antigues tenen alguns camps numèrics guardats com a string
    (p. ex. tutorCurs: "-1"); l'esquema Pydantic de l'API els coerciona a int
    deliberadament (això corregeix advertiments falsos com "tutoria de curs
    inexistent: -1"). Per comparar amb la sortida del pipeline CLI antic cal
    normalitzar totes dues bandes.
    """
    if isinstance(valor, dict):
        return {k: _normalitza_ints(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_normalitza_ints(v) for v in valor]
    if isinstance(valor, str):
        try:
            return int(valor)
        except ValueError:
            return valor
    return valor


def test_preprocess_regressio():
    """La sortida del preprocessador via API ha de ser idèntica a la generada
    pel pipeline CLI original (dades_solver_processades.json prové de
    BuitRestriccions.json), llevat de la coerció de tipus documentada a
    _normalitza_ints."""
    with open(os.path.join(ARREL, 'BuitRestriccions.json'), encoding='utf-8') as f:
        entrada = json.load(f)
    with open(os.path.join(ARREL, 'dades_solver_processades.json'), encoding='utf-8') as f:
        esperat = json.load(f)

    r = client.post('/api/preprocess', json={'dades': entrada})
    assert r.status_code == 200
    assert _normalitza_ints(r.json()['dades_processades']) == _normalitza_ints(esperat)


# ---------------------------------------------------------------------------
# Horari pre-assignat (hores fixades)
# ---------------------------------------------------------------------------

def _matriu_horari_buida():
    """Matriu d'un període en format editor: [dia][hora] -> llista de cel·les."""
    return [[[] for _ in range(11)] for _ in range(5)]


def test_preprocess_horari_fixat(dades_reals):
    """El camp horari del Solver.json real (període 0) s'ha de normalitzar
    a horari_fixat amb una entrada per cel·la ocupada vàlida."""
    r = client.post('/api/preprocess', json={'dades': dades_reals})
    assert r.status_code == 200
    fixat = r.json()['dades_processades']['horari_fixat']
    assert len(fixat) > 0
    for fix in fixat:
        assert set(fix) == {'professor', 'modul', 'subgrup', 'dia', 'hora', 'aula'}
        assert 0 <= fix['dia'] <= 4
        assert 0 <= fix['hora'] <= 10
        assert fix['subgrup'] in (1, 2, 3)


def test_preprocess_horari_fixat_periode_buit(dades_reals):
    """Els períodes sense contingut no aporten cap hora fixada."""
    r = client.post('/api/preprocess', json={'dades': dades_reals, 'periode': 1})
    assert r.status_code == 200
    assert r.json()['dades_processades']['horari_fixat'] == []


def test_validate_estadistiques_hores_fixades(dades_reals):
    r = client.post('/api/validate', json={'dades': dades_reals})
    assert r.status_code == 200
    est = r.json()['estadistiques']
    assert est['hores_fixades'] > 0


def test_validate_horari_fixat_invalid(dades_reals):
    """Cel·les incoherents es descarten amb advertiment, sense error 422."""
    dades = copy.deepcopy(dades_reals)
    matriu = _matriu_horari_buida()
    matriu[0][0] = [{'modul': 99999, 'curs': 0, 'aula': -1, 'subgrup': 3,
                     'suport': False, 'simultani': False, 'profe': 99999}]
    dades['horari'] = [matriu]
    r = client.post('/api/validate', json={'dades': dades})
    assert r.status_code == 200
    cos = r.json()
    assert cos['estadistiques']['hores_fixades'] == 0
    assert any('Horari fixat' in a for a in cos['advertiments'])


def test_solve_fixar_horari(solucio_real, dades_reals):
    """Amb fixar_horari, cada hora pre-assignada apareix exactament al seu slot.

    Es fixa la solució real completa: el solver només ha de verificar-la, i el
    resultat ha de coincidir cel·la a cel·la amb l'horari fixat."""
    matriu = _matriu_horari_buida()
    for c_idx, curs in enumerate(solucio_real['solucio']['horari']):
        for d, dia in enumerate(curs):
            for h, classes in enumerate(dia):
                for classe in classes:
                    matriu[d][h].append({
                        'modul': classe['modul_index'], 'curs': c_idx,
                        'aula': classe['aula_index'], 'subgrup': classe['subgrup'],
                        'suport': False, 'simultani': False,
                        'profe': classe['professor_index'],
                    })
    dades = copy.deepcopy(dades_reals)
    dades['horari'] = [matriu]

    r = client.post('/api/solve', json={
        'dades': dades,
        'opcions': {'max_time_seconds': 60, 'num_workers': 8, 'fixar_horari': True},
    })
    assert r.status_code == 200, r.text
    cos = r.json()
    assert cos['estat'] in ('OPTIMAL', 'FEASIBLE'), cos['estat']
    assert any('fixat' in a or 'fixades' in a for a in cos['advertiments'])

    # Cada classe fixada és exactament al mateix lloc que a la solució original
    for c_idx, curs in enumerate(solucio_real['solucio']['horari']):
        for d, dia in enumerate(curs):
            for h, classes in enumerate(dia):
                esperades = {(c['modul_index'], c['professor_index'], c['subgrup'], c['aula_index'])
                             for c in classes}
                obtingudes = {(c['modul_index'], c['professor_index'], c['subgrup'], c['aula_index'])
                              for c in cos['solucio']['horari'][c_idx][d][h]}
                assert esperades == obtingudes, (
                    f'Curs {c_idx} dia {d} hora {h}: fixat {esperades}, obtingut {obtingudes}'
                )


def test_solve_fixacio_impossible(solucio_real, dades_reals):
    """Fixar més hores que les de l'assignació ha de donar INFEASIBLE."""
    # Placements reals d'una assignació concreta (garanteixen slots vàlids);
    # es deriven de la vista per curs, que porta professor_index explícit
    placements = {}
    for c, curs in enumerate(solucio_real['solucio']['horari']):
        for d, dia in enumerate(curs):
            for h, classes in enumerate(dia):
                for cl in classes:
                    clau = (cl['professor_index'], cl['modul_index'], cl['subgrup'], c)
                    placements.setdefault(clau, []).append((d, h, cl))
    (p_idx, _modul, _subgrup, c_idx), slots = next(iter(placements.items()))
    classe = slots[0][2]

    # Slot extra on el curs té classe (per passar la validació de disponibilitat)
    ocupats = {(d, h) for d, h, _ in slots}
    extra = next(
        (d, h)
        for d, dia in enumerate(solucio_real['solucio']['horari'][c_idx])
        for h, classes in enumerate(dia)
        if classes and (d, h) not in ocupats
    )

    matriu = _matriu_horari_buida()
    cella = {'modul': classe['modul_index'], 'curs': c_idx, 'aula': classe['aula_index'],
             'subgrup': classe['subgrup'], 'suport': False, 'simultani': False, 'profe': p_idx}
    for d, h, _ in slots:
        matriu[d][h].append(dict(cella))
    matriu[extra[0]][extra[1]].append(dict(cella))

    dades = copy.deepcopy(dades_reals)
    dades['horari'] = [matriu]
    r = client.post('/api/solve', json={
        'dades': dades,
        'opcions': {'max_time_seconds': 30, 'num_workers': 8, 'fixar_horari': True},
    })
    assert r.status_code == 200, r.text
    cos = r.json()
    assert cos['estat'] == 'INFEASIBLE', cos['estat']
    assert any('infactible' in a for a in cos['advertiments'])


def test_solve_sense_fixar_ignora_horari(dades_reals):
    """Sense opcions.fixar_horari, les hores detectades s'ignoren (amb avís)."""
    r = client.post('/api/solve', json={
        'dades': dades_reals,
        'opcions': {'max_time_seconds': 1, 'num_workers': 8},
    })
    assert r.status_code == 200
    assert any('ignora' in a for a in r.json()['advertiments'])


# ---------------------------------------------------------------------------
# /api/solve — casos d'error i límits
# ---------------------------------------------------------------------------

def test_solve_opcions_invalides(dades_reals):
    r = client.post('/api/solve', json={
        'dades': dades_reals,
        'opcions': {'max_time_seconds': -5},
    })
    assert r.status_code == 422


def test_solve_infeasible(dades_reals):
    """Bloquejar totes les hores d'un professor amb docència ha de donar INFEASIBLE."""
    dades = copy.deepcopy(dades_reals)
    professor = next(p for p in dades['professors']
                     if p.get('actiu') and p.get('moduls'))
    professor['desiderata'] = [
        {'dia': d, 'hora': h, 'tipus': 2} for d in range(5) for h in range(13)
    ]
    r = client.post('/api/solve', json={
        'dades': dades,
        'opcions': {'max_time_seconds': 30, 'num_workers': 8},
    })
    assert r.status_code == 200, r.text
    cos = r.json()
    assert cos['estat'] == 'INFEASIBLE', cos['estat']
    assert 'solucio' not in cos or cos.get('solucio') is None
    # Sense opcions.explicar_infeasible no hi ha motiu (ni el seu cost)
    assert 'motiu_infeasible' not in cos


def test_solve_explicar_infeasible(dades_reals):
    """Amb explicar_infeasible, un INFEASIBLE diu quins grups cal relaxar.

    Es bloquegen totes les hores d'un professor: el mínim a relaxar han de ser
    exactament les seves desiderates."""
    dades = copy.deepcopy(dades_reals)
    professor = next(p for p in dades['professors']
                     if p.get('actiu') and p.get('moduls'))
    professor['desiderata'] = [
        {'dia': d, 'hora': h, 'tipus': 2} for d in range(5) for h in range(13)
    ]
    r = client.post('/api/solve', json={
        'dades': dades,
        'opcions': {'max_time_seconds': 120, 'num_workers': 8, 'explicar_infeasible': True},
    })
    assert r.status_code == 200, r.text
    cos = r.json()
    assert cos['estat'] == 'INFEASIBLE', cos['estat']
    assert cos.get('motiu_infeasible'), cos.get('motiu_infeasible')
    assert any(professor['nom'] in motiu for motiu in cos['motiu_infeasible']), (
        cos['motiu_infeasible'])


def test_solve_temps_molt_curt(dades_reals):
    """Amb 1 segon el solver ha de respondre igualment amb un estat vàlid."""
    r = client.post('/api/solve', json={
        'dades': dades_reals,
        'opcions': {'max_time_seconds': 1, 'num_workers': 8},
    })
    assert r.status_code == 200
    assert r.json()['estat'] in ('OPTIMAL', 'FEASIBLE', 'INFEASIBLE', 'UNKNOWN')


# ---------------------------------------------------------------------------
# Restriccions per mòdul: horari_disponible i aules_possibles
# ---------------------------------------------------------------------------

def test_preprocess_restriccions_modul(dades_reals):
    """Les restriccions del mòdul passen a les dades processades."""
    dades = copy.deepcopy(dades_reals)
    modul = dades['moduls'][0]
    modul['horari_disponible'] = [{'dia': 0, 'hora': 0}, {'dia': 0, 'hora': 1}]
    modul['aules_possibles'] = [dades['aules'][0]['index']]

    r = client.post('/api/preprocess', json={'dades': dades})
    assert r.status_code == 200
    processat = next(m for m in r.json()['dades_processades']['moduls']
                     if m['index'] == modul['index'])
    assert processat['horari_disponible'] == [{'dia': 0, 'hora': 0}, {'dia': 0, 'hora': 1}]
    assert processat['aules_possibles'] == [dades['aules'][0]['index']]


def test_validate_restriccions_modul_advertiments(dades_reals):
    """Slots insuficients per a les hores i aules inexistents generen advertiments."""
    dades = copy.deepcopy(dades_reals)
    # Un mòdul que algun professor imparteixi amb més d'1 hora
    professor = next(p for p in dades['professors']
                     if p.get('actiu') and any(m.get('hores', 0) > 1 for m in p.get('moduls', [])))
    assignacio = next(m for m in professor['moduls'] if m.get('hores', 0) > 1)
    modul = next(m for m in dades['moduls'] if m['index'] == assignacio['index'])
    modul['horari_disponible'] = [{'dia': 0, 'hora': 3}]   # 1 slot per a 2+ hores
    modul['aules_possibles'] = [99999]                     # aula inexistent

    r = client.post('/api/validate', json={'dades': dades})
    assert r.status_code == 200
    advertiments = r.json()['advertiments']
    assert any('infactible' in a and modul['nom'] in a for a in advertiments), advertiments
    assert any('99999' in a for a in advertiments), advertiments


def test_solve_respecta_restriccions_modul(solucio_real, dades_reals):
    """Un mòdul restringit a slots i aules concrets només apareix allà.

    Es restringeix un mòdul normal als slots i aules exactes on la solució real
    el va col·locar (garanteix factibilitat) i es comprova la solució nova."""
    especials = {m['index'] for m in dades_reals['moduls']
                 if 'tutoria' in (m.get('nom', '') + m.get('codi', '')).lower()
                 or m.get('especialitat') in (2, 3)}
    especials |= set(dades_reals.get('projectes') or [])

    # Placements per mòdul a la solució real
    placements = {}
    for curs in solucio_real['solucio']['horari']:
        for d, dia in enumerate(curs):
            for h, classes in enumerate(dia):
                for c in classes:
                    placements.setdefault(c['modul_index'], []).append((d, h, c['aula_index']))

    modul_idx, llocs = next((m, p) for m, p in placements.items()
                            if m not in especials and len(p) >= 2)

    dades = copy.deepcopy(dades_reals)
    modul = next(m for m in dades['moduls'] if m['index'] == modul_idx)
    slots_permesos = sorted({(d, h) for d, h, _ in llocs})
    aules_permeses = sorted({a for _, _, a in llocs})
    modul['horari_disponible'] = [{'dia': d, 'hora': h} for d, h in slots_permesos]
    modul['aules_possibles'] = aules_permeses

    r = client.post('/api/solve', json={
        'dades': dades,
        'opcions': {'max_time_seconds': 120, 'num_workers': 8},
    })
    assert r.status_code == 200, r.text
    cos = r.json()
    assert cos['estat'] in ('OPTIMAL', 'FEASIBLE'), cos['estat']

    ocurrencies = 0
    for curs in cos['solucio']['horari']:
        for d, dia in enumerate(curs):
            for h, classes in enumerate(dia):
                for c in classes:
                    if c['modul_index'] == modul_idx:
                        ocurrencies += 1
                        assert (d, h) in slots_permesos, (
                            f'Mòdul {modul_idx} col·locat fora dels slots permesos: dia {d} hora {h}')
                        assert c['aula_index'] in aules_permeses, (
                            f'Mòdul {modul_idx} en aula no permesa: {c["aula_index"]}')
    assert ocurrencies == len(llocs)


# ---------------------------------------------------------------------------
# CORS i feines asíncrones (/api/jobs)
# ---------------------------------------------------------------------------

def test_cors_habilitat():
    """Les respostes a peticions amb Origin han de dur la capçalera CORS."""
    r = client.get('/api/health', headers={'Origin': 'http://localhost:5173'})
    assert r.status_code == 200
    assert r.headers.get('access-control-allow-origin') in ('*', 'http://localhost:5173')


def test_cors_preflight():
    r = client.options('/api/jobs', headers={
        'Origin': 'http://localhost:5173',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type',
    })
    assert r.status_code == 200
    assert r.headers.get('access-control-allow-origin')


def _espera_feina(feina_id, timeout=120):
    """Consulta la feina fins que surti d'en_curs (o s'esgoti el temps)."""
    import time as _time
    limit = _time.time() + timeout
    while _time.time() < limit:
        cos = client.get(f'/api/jobs/{feina_id}').json()
        if cos['estat_feina'] != 'en_curs':
            return cos
        _time.sleep(1)
    return cos


def test_feina_inexistent():
    assert client.get('/api/jobs/no-existeix').status_code == 404
    assert client.delete('/api/jobs/no-existeix').status_code == 404


def test_feina_opcions_invalides(dades_reals):
    r = client.post('/api/jobs', json={
        'dades': dades_reals,
        'opcions': {'max_time_seconds': -1},
    })
    assert r.status_code == 422


def test_feina_acaba_sola(dades_reals):
    """Una feina infactible (professor totalment bloquejat) acaba sola i ràpid."""
    dades = copy.deepcopy(dades_reals)
    professor = next(p for p in dades['professors']
                     if p.get('actiu') and p.get('moduls'))
    professor['desiderata'] = [
        {'dia': d, 'hora': h, 'tipus': 2} for d in range(5) for h in range(13)
    ]
    r = client.post('/api/jobs', json={
        'dades': dades,
        'opcions': {'max_time_seconds': 60, 'num_workers': 8},
    })
    assert r.status_code == 202, r.text
    cos = _espera_feina(r.json()['id'])
    assert cos['estat_feina'] == 'acabada', cos
    assert cos['resultat']['estat'] == 'INFEASIBLE'
    assert cos['aturada_demanada'] is False


def test_feina_aturada_conserva_millor_solucio(dades_reals):
    """El cicle complet: llançar, seguir el progrés, aturar a mig càlcul.

    S'espera fins que CP-SAT troba la primera solució i s'atura la feina: el
    resultat ha de ser la millor solució trobada (FEASIBLE), no una pèrdua."""
    import time as _time
    r = client.post('/api/jobs', json={
        'dades': dades_reals,
        'opcions': {'max_time_seconds': 280, 'num_workers': 8},
    })
    assert r.status_code == 202, r.text
    fid = r.json()['id']

    # Esperar la primera solució intermèdia (progrés visible)
    limit = _time.time() + 240
    cos = None
    while _time.time() < limit:
        cos = client.get(f'/api/jobs/{fid}').json()
        if cos['solucions_intermedies'] > 0 or cos['estat_feina'] != 'en_curs':
            break
        _time.sleep(2)

    r = client.delete(f'/api/jobs/{fid}')
    assert r.status_code == 200
    assert r.json()['aturada_demanada'] is True or r.json()['estat_feina'] != 'en_curs'

    cos = _espera_feina(fid, timeout=60)
    assert cos['estat_feina'] == 'acabada', cos
    if cos['solucions_intermedies'] > 0:
        # Hi havia solució quan hem aturat: s'ha de conservar
        assert cos['resultat']['estat'] in ('FEASIBLE', 'OPTIMAL')
        assert cos['resultat']['solucio'] is not None
        assert cos['objectiu_actual'] is not None
    else:
        assert cos['resultat']['estat'] in ('FEASIBLE', 'OPTIMAL', 'UNKNOWN')


# ---------------------------------------------------------------------------
# /api/solve — solució amb dades reals i invariants
# ---------------------------------------------------------------------------

def test_solve_troba_solucio(solucio_real):
    assert solucio_real['estat'] in ('OPTIMAL', 'FEASIBLE')
    assert solucio_real['solucio'] is not None
    stats = solucio_real['solucio']['stats']
    assert stats['temps_resolucio'] > 0
    assert stats['estat'] == solucio_real['estat']


def test_solucio_estructura(solucio_real, dades_reals):
    sol = solucio_real['solucio']
    horari = sol['horari']
    cursos_actius = [c for c in dades_reals['cursos'] if c.get('actiu')]
    assert len(horari) == len(cursos_actius)
    for curs in horari:
        assert len(curs) == 5  # dies
        for dia in curs:
            assert len(dia) == 11  # hores


def test_professor_mai_a_dos_llocs(solucio_real):
    """Restricció 2: un professor no pot tenir dues classes a la mateixa hora."""
    for p_idx, prof in enumerate(solucio_real['solucio']['professors']):
        for d, dia in enumerate(prof):
            for h, classes in enumerate(dia):
                assert len(classes) <= 1, (
                    f'Professor {p_idx} té {len(classes)} classes el dia {d} hora {h}: {classes}'
                )


def test_hores_professor_coincideixen(solucio_real, dades_reals):
    """Cada professor actiu ha de tenir exactament la suma d'hores dels seus mòduls."""
    hores_esperades = {
        p['index']: sum(m.get('hores', 0) for m in p.get('moduls', []))
        for p in dades_reals['professors'] if p.get('actiu')
    }
    professors = solucio_real['solucio']['professors']
    for p_idx, esperades in hores_esperades.items():
        if p_idx >= len(professors):
            continue
        reals = sum(
            1
            for dia in professors[p_idx]
            for classes in dia
            if classes
        )
        assert reals == esperades, (
            f'Professor {p_idx}: {reals} hores assignades, {esperades} esperades'
        )


def test_desiderata_hard_respectada(solucio_real, dades_reals):
    """Restricció 7: cap classe en un slot amb desiderata tipus 2 (no disponible)."""
    professors = solucio_real['solucio']['professors']
    for p in dades_reals['professors']:
        if not p.get('actiu') or p['index'] >= len(professors):
            continue
        for des in p.get('desiderata', []):
            if des['tipus'] != 2 or des['hora'] >= 11:
                continue
            classes = professors[p['index']][des['dia']][des['hora']]
            assert not classes, (
                f"Professor {p['nom']} té classe el dia {des['dia']} hora "
                f"{des['hora']} tot i tenir desiderata tipus 2"
            )


def test_curs_sense_forats(solucio_real):
    """Restricció 4.1: les hores ocupades d'un curs en un dia són consecutives."""
    for c_idx, curs in enumerate(solucio_real['solucio']['horari']):
        for d, dia in enumerate(curs):
            ocupades = [h for h, classes in enumerate(dia) if classes]
            if len(ocupades) > 1:
                assert ocupades[-1] - ocupades[0] + 1 == len(ocupades), (
                    f'Curs {c_idx} dia {d} té forats: hores ocupades {ocupades}'
                )


def test_maxim_una_classe_per_slot_de_curs(solucio_real):
    """Per als ALUMNES d'un curs, un slot té una sola "classe": com a molt un
    mòdul de grup sencer, o bé un desdoblament (subgrup 1 + subgrup 2). Diversos
    professors del MATEIX mòdul i subgrup (co-docència: titular + suport) són una
    sola classe i estan permesos: es compten mòduls distints, no professors."""
    for c_idx, curs in enumerate(solucio_real['solucio']['horari']):
        for d, dia in enumerate(curs):
            for h, classes in enumerate(dia):
                if not classes:
                    continue
                moduls_per_sg = {1: set(), 2: set(), 3: set()}
                for c in classes:
                    moduls_per_sg[c['subgrup']].add(c['modul_index'])
                lloc = f'Curs {c_idx} dia {d} hora {h}'
                if moduls_per_sg[3]:
                    assert len(moduls_per_sg[3]) == 1, f'{lloc}: dos mòduls de grup sencer alhora'
                    assert not moduls_per_sg[1] and not moduls_per_sg[2], (
                        f'{lloc}: grup sencer i subgrup alhora')
                else:
                    assert len(moduls_per_sg[1]) <= 1, f'{lloc}: dos mòduls al subgrup 1'
                    assert len(moduls_per_sg[2]) <= 1, f'{lloc}: dos mòduls al subgrup 2'


def test_aula_sense_solapaments(solucio_real, dades_reals):
    """Restricció 3: una aula no té dues classes alhora (excepte mòduls simultanis)."""
    simultanis = set()
    for p in dades_reals['professors']:
        for m in p.get('moduls', []):
            if m.get('simultani'):
                simultanis.add(m['index'])

    for a_pos, aula in enumerate(solucio_real['solucio']['aules']):
        for d, dia in enumerate(aula):
            for h, classes in enumerate(dia):
                moduls = {c['modul_index'] for c in classes}
                # Diversos professors del MATEIX mòdul (co-docència: titular +
                # suport, reunions) comparteixen aula: és una sola classe. Només
                # es prohibeix barrejar mòduls DIFERENTS no simultanis.
                if len(moduls) > 1:
                    assert all(m in simultanis for m in moduls), (
                        f'Aula pos {a_pos} dia {d} hora {h}: solapament no simultani: {moduls}'
                    )


def test_tutoria_mai_primera_ni_ultima_hora(solucio_real, dades_reals):
    """Restricció 5: la tutoria mai a primera ni última hora del dia del curs."""
    moduls_tutoria = {
        m['index'] for m in dades_reals['moduls']
        if 'tutoria' in m.get('nom', '').lower() or 'tutoria' in m.get('codi', '').lower()
    }
    for c_idx, curs in enumerate(solucio_real['solucio']['horari']):
        for d, dia in enumerate(curs):
            ocupades = [h for h, classes in enumerate(dia) if classes]
            if not ocupades:
                continue
            primera, ultima = ocupades[0], ocupades[-1]
            for h in (primera, ultima):
                for classe in dia[h]:
                    assert classe['modul_index'] not in moduls_tutoria, (
                        f'Curs {c_idx} dia {d}: tutoria a l\'hora {h} '
                        f'(primera o última del dia)'
                    )


# ---------------------------------------------------------------------------
# /api/solve — format compatible (reimportable a l'editor)
# ---------------------------------------------------------------------------

def test_solucio_compatible(solucio_real, dades_reals):
    compatible = solucio_real['solucio_compatible']
    assert compatible is not None
    # Mateixa estructura de primer nivell que l'entrada
    assert set(compatible.keys()) == set(dades_reals.keys())
    assert compatible['autor'] == 'HorariSolver'
    # L'horari conté les assignacions de la solució
    assignacions = [
        cella
        for dia in compatible['horari'][0]
        for hora in dia
        for cella in hora
        if cella is not None
    ]
    total_classes = sum(
        len(classes)
        for curs in solucio_real['solucio']['horari']
        for dia in curs
        for classes in dia
    )
    assert len(assignacions) > 0
    assert len(assignacions) <= total_classes
    for a in assignacions:
        for camp in ('modul', 'curs', 'aula', 'subgrup', 'profe'):
            assert camp in a


def test_solucio_compatible_preserva_flags(solucio_real, dades_reals):
    """Regressió: la solució compatible ha de conservar els flags `suport` i
    `simultani` de les assignacions (co-docència, mòduls simultanis, reunions),
    en lloc de forçar-los a False. Quan es perdien, l'editor tornava a marcar
    aquestes hores com a conflicte en recarregar la solució."""
    compatible = solucio_real['solucio_compatible']

    esperats_simultani = {
        (m['index'], p['index'], m.get('subgrup', 3))
        for p in dades_reals['professors']
        for m in p.get('moduls', [])
        if m.get('simultani')
    }
    esperats_suport = {
        (m['index'], p['index'], m.get('subgrup', 3))
        for p in dades_reals['professors']
        for m in p.get('moduls', [])
        if m.get('suport')
    }
    # El dataset ha de tenir alguna assignació amb flag per a que el test sigui útil
    assert esperats_simultani or esperats_suport

    celles = [c for dia in compatible['horari'][0] for hora in dia for c in hora if c]
    n_simultani = 0
    for c in celles:
        clau = (c['modul'], c['profe'], c['subgrup'])
        if clau in esperats_simultani:
            assert c['simultani'] is True, f'simultani perdut a la sortida compatible: {clau}'
            n_simultani += 1
        if clau in esperats_suport:
            assert c['suport'] is True, f'suport perdut a la sortida compatible: {clau}'

    # Si hi ha assignacions simultànies a l'entrada, han d'aparèixer marcades
    # (abans del fix el flag es forçava a False i això no passava mai)
    if esperats_simultani:
        assert n_simultani > 0, 'cap cel·la simultània marcada a la solució compatible'


# ---------------------------------------------------------------------------
# /api/solve — particions (repartiment d'hores en blocs)
# ---------------------------------------------------------------------------

def _dades_particio(hores, particio):
    """Dataset mínim: 1 professor amb 1 mòdul de `hores` hores i la `particio`
    donada. 6 hores/dia, professor lliure de restriccions de règim."""
    return {
        'professors': [{
            'index': 0, 'actiu': True, 'nom': 'Test Profe', 'nomCurt': 'Test',
            'especialitat': 0, 'controlable': False, 'lliureRestriccions': True,
            'desiderata': [],
            'moduls': [{'index': 0, 'hores': hores, 'aula': 0, 'subgrup': 3,
                        'particio': particio}],
        }],
        'moduls': [{'index': 0, 'codi': 'T', 'nom': 'Test', 'curs': 0, 'especialitat': 0}],
        'cursos': [{'index': 0, 'actiu': True, 'nom': 'TestCurs', 'aula': 0}],
        'aules': [{'index': 0, 'actiu': True, 'nom': 'Aula Test'}],
        'especialitats': [{'index': 0, 'actiu': True, 'codi': 'T', 'nom': 'Test'}],
        'config': {'horesSetmana': '8,9,10,11,12,13'},
    }


def _estructura_blocs(cos):
    """Longituds dels blocs (hores consecutives el mateix dia) del mòdul 0 /
    professor 0, ordenades de gran a petit."""
    horari = cos['solucio']['horari'][0]
    per_dia = {}
    for d, dia in enumerate(horari):
        for h, classes in enumerate(dia):
            for c in classes:
                if c['modul_index'] == 0 and c['professor_index'] == 0:
                    per_dia.setdefault(d, []).append(h)
    blocs = []
    for hores in per_dia.values():
        hores.sort()
        inici = 0
        for i in range(1, len(hores) + 1):
            if i == len(hores) or hores[i] != hores[i - 1] + 1:
                blocs.append(i - inici)
                inici = i
    return sorted(blocs, reverse=True)


def _solve_particio(particio, hores=3):
    r = client.post('/api/solve', json={
        'dades': _dades_particio(hores, particio),
        'opcions': {'max_time_seconds': 30, 'num_workers': 4},
    })
    assert r.status_code == 200, r.text
    cos = r.json()
    assert cos['estat'] in ('OPTIMAL', 'FEASIBLE'), cos.get('estat')
    return cos


def test_particio_buida_reparteix_lliure():
    """Sense partició, el solver reparteix lliurement però col·loca totes les hores."""
    cos = _solve_particio([], hores=3)
    assert sum(_estructura_blocs(cos)) == 3


def test_particio_unica_tot_seguit():
    """[[3]] força 3 hores consecutives el mateix dia."""
    cos = _solve_particio([[3]], hores=3)
    assert _estructura_blocs(cos) == [3]


def test_particio_unica_separada():
    """[[1,1,1]] força 3 hores en 3 dies diferents."""
    cos = _solve_particio([[1, 1, 1]], hores=3)
    assert _estructura_blocs(cos) == [1, 1, 1]


def test_particio_disjuncio_en_tria_una():
    """Amb diverses particions permeses, el solver en realitza EXACTAMENT una."""
    cos = _solve_particio([[3], [1, 1, 1]], hores=3)
    assert _estructura_blocs(cos) in ([3], [1, 1, 1])
