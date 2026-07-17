import json
import time
from typing import Dict, List, Tuple, Set
from ortools.sat.python import cp_model
import numpy as np
import copy
from exportar_html import exportar_horaris_html
import itertools


class HorariSolver:
    def __init__(self, dades_path: str):
        # Càrrega de dades
        self.carregar_dades(dades_path)
        
        # Constants temporals
        self.dies = self.config['dies_setmana']
        self.hores_per_dia = self.config['hores_per_dia']
        self.total_slots = self.dies * self.hores_per_dia

        # Minut d'inici de cada franja (des de mitjanit), per calcular el descans
        # de 12h entre dies. Ve de la config de l'editor; si no, valors per
        # defecte de l'institut (08:00 … 20:05).
        DEFECTE_MIN = [480, 540, 630, 690, 750, 810, 900, 960, 1020, 1095, 1150, 1205]
        self.hores_inici_min = self.config.get('hores_inici_min')
        if not self.hores_inici_min or len(self.hores_inici_min) < self.hores_per_dia:
            if self.hores_per_dia <= len(DEFECTE_MIN):
                self.hores_inici_min = DEFECTE_MIN[:self.hores_per_dia]
            else:
                self.hores_inici_min = DEFECTE_MIN + [
                    DEFECTE_MIN[-1] + 60 * (i + 1)
                    for i in range(self.hores_per_dia - len(DEFECTE_MIN))]
        
        # Inicialització del model
        self.model = cp_model.CpModel()

        # Estat de l'última resolució ('OPTIMAL', 'FEASIBLE', 'INFEASIBLE', 'UNKNOWN', 'MODEL_INVALID')
        self.ultim_estat = None

        # Instància CpSolver activa (per poder aturar la cerca des d'un altre fil)
        self.cp_solver = None
        self.atura_demanada = False

        # Explicació d'infactibilitat (opt-in: costa una mica de rendiment).
        # Els grups de restriccions "accionables" s'apliquen condicionats a un
        # literal d'assumpció; si el model és INFEASIBLE, CP-SAT retorna quins
        # literals formen part del conflicte (motiu_infeasible).
        self.explicar_infeasible = False
        # Si és cert, s'ignoren les preferències "prefereix no" (hores grogues,
        # desiderata tipus 1): no s'afegeix cap penalització a la funció objectiu.
        self.ignora_hores_grogues = False
        self._literals_assumpcio = {}      # clau de grup -> BoolVar
        self.etiquetes_assumpcions = {}    # índex del literal -> etiqueta llegible
        self.motiu_infeasible = []
        
        # Variables de decisió
        self.vars_assignacio = {}  # (modul, professor, dia, hora, aula, subgrup)

        # Variables (una per hora col·locada fora de l'aula preferida de la seva
        # assignació) que es penalitzen suaument a la funció objectiu, perquè el
        # solver mantingui l'aula triada a l'editor sempre que sigui possible.
        self.penalitzacio_aula = []

        # Variables auxiliars
        self.hores_programades = {}  # Comptador d'hores programades per mòdul
        self.slots_ocupats_professor = {}  # (professor, dia, hora) -> bool
        self.slots_ocupats_aula = {}  # (aula, dia, hora) -> bool
        self.slots_ocupats_curs = {}  # (curs, dia, hora) -> bool

    def carregar_dades(self, dades_path):
        """Carrega les dades del fitxer processat (path) o directament d'un dict"""
        if isinstance(dades_path, dict):
            dades = dades_path
        else:
            with open(dades_path, 'r', encoding='utf-8') as f:
                dades = json.load(f)

        self.professors = dades['professors']
        self.moduls = dades['moduls']
        self.cursos = dades['cursos']
        self.aules = dades['aules']
        self.especialitats = dades['especialitats']
        self.agrupacions = dades['agrupacions']
        self.config = dades['configuracio']
        # Hores pre-assignades (normalitzades pel preprocessador) que es poden
        # forçar amb executar(fixar_horari=True)
        self.horari_fixat = dades.get('horari_fixat', [])

        # Slots (dia, hora) fixats manualment, per professor. Amb fixar_horari
        # actiu, aquestes hores compten com a context però NO es validen entre
        # elles (ni contra les seves desiderates): les restriccions per professor
        # només s'apliquen a les hores que decideix el solver.
        self.fixar_horari = False
        self.slots_fixats_per_prof = {}
        self.slots_fixats_per_modul = {}
        for _fix in self.horari_fixat:
            _p, _d, _h, _m = (_fix.get('professor'), _fix.get('dia'),
                              _fix.get('hora'), _fix.get('modul'))
            if _p is not None and _d is not None and _h is not None:
                self.slots_fixats_per_prof.setdefault(_p, set()).add((_d, _h))
            if _m is not None and _d is not None and _h is not None:
                self.slots_fixats_per_modul.setdefault(_m, set()).add((_d, _h))

        # Índex per a accés ràpid
        self.modul_per_index = {m['index']: m for m in self.moduls if 'index' in m}
        self.professor_per_index = {p['index']: p for p in self.professors if 'index' in p}
        self.curs_per_index = {c['index']: c for c in self.cursos if 'index' in c}
        self.aula_per_index = {a['index']: a for a in self.aules if 'index' in a}
        self.moduls_projectes = set(self.config.get('moduls_especials', {}).get('projectes', []))
        self.horaris_projectes = self.config.get('horaris_projectes', [])

        self.slots_projectes = set()
        for slot in self.horaris_projectes:
            if 'dia' in slot and 'hora' in slot:
                self.slots_projectes.add((slot['dia'], slot['hora']))

        # Mòduls per curs
        self.moduls_per_curs = {}
        for curs in self.cursos:
            if 'moduls' in curs:
                self.moduls_per_curs[curs['index']] = curs['moduls']
            else:
                self.moduls_per_curs[curs['index']] = []
        
        for a in self.aula_per_index.values():
            if 'index' not in a:
                raise ValueError("Cada aula ha de tenir un index únic.")
            
            info_aula = f"Aula {a['index']} carregada - Nom: {a.get('nom', 'desconegut')}"
            
            # Añadir información sobre las nuevas propiedades
            restriccions = []
            if a.get('nomes_subgrups', False):
                restriccions.append("només per subgrups 1 i 2")
            if a.get('nomes_tardes', False):
                restriccions.append("només disponible a partir de l'hora 6")
            
            if restriccions:
                info_aula += f" - Restriccions: {', '.join(restriccions)}"
            
            print(info_aula)
        
        # Assignacions professor-mòdul-hores
        self.assignacions = []
        for professor in self.professors:
            if 'moduls' in professor:
                for modul_assign in professor['moduls']:
                    self.assignacions.append({
                        'professor': professor['index'],
                        'modul': modul_assign['index'],
                        'hores': modul_assign.get('hores', 0),
                        'aula': modul_assign.get('aula', -1),
                        'subgrup': modul_assign.get('subgrup', 3),  # 3 = grup sencer
                        '7hores': professor.get('7hores', False),
                        'DiesLliures': professor.get('DiesLliures', False),
                        'controlable': professor.get('controlable', True),
                        'simultani': modul_assign.get('simultani', False),
                        'suport': modul_assign.get('suport', False),
                        'particio': modul_assign.get('particio', [])
                    })

        # Marca de suport per assignació (m, professor, aula, subgrup). Un
        # professor de suport acompanya el TITULAR del mateix mòdul (assignació
        # NO de suport): p. ex. les reunions, on un professor és el titular i la
        # resta hi assisteixen com a suport.
        self.assig_es_suport = {}
        self.assig_es_simultani = {}
        for a_ in self.assignacions:
            clau = (a_['modul'], a_['professor'], a_['aula'], a_['subgrup'])
            self.assig_es_suport[clau] = a_.get('suport', False)
            self.assig_es_simultani[clau] = a_.get('simultani', False)

    def _flags_assignacio(self, modul, professor, aula, subgrup):
        """Retorna (suport, simultani) de l'assignació professor-mòdul. Aquests
        flags viuen a l'assignació de l'editor i s'han de propagar a la solució
        de sortida perquè l'editor no torni a marcar com a conflicte la
        co-docència (titular+suport) ni els mòduls simultanis. Si el solver ha
        triat una aula diferent de la preferida (aules_possibles), es fa un
        fallback ignorant l'aula, ja que el flag no depèn de l'aula concreta."""
        clau = (modul, professor, aula, subgrup)
        if clau in self.assig_es_suport:
            return self.assig_es_suport[clau], self.assig_es_simultani.get(clau, False)
        for a_ in self.assignacions:
            if a_['modul'] == modul and a_['professor'] == professor and a_['subgrup'] == subgrup:
                return a_.get('suport', False), a_.get('simultani', False)
        return False, False

    def _es_fixat(self, professor, dia, hora):
        """Cert si (dia,hora) és una hora fixada manualment del professor amb
        fixar_horari actiu (llavors queda exempta de la validació de restriccions)."""
        return self.fixar_horari and (dia, hora) in self.slots_fixats_per_prof.get(professor, set())

    def _modul_dia_fixat(self, modul, dia):
        """Cert si el mòdul té alguna hora fixada manualment aquest dia (amb
        fixar_horari actiu): llavors no se li apliquen les regles de posició
        (FOL/anglès sempre a primera/última, tutoria mai a primera/última)."""
        if not self.fixar_horari:
            return False
        return any(d == dia for (d, _h) in self.slots_fixats_per_modul.get(modul, set()))

    def _assumpcio(self, clau: str, etiqueta: str):
        """Literal d'assumpció per a un grup de restriccions (None si desactivat).

        Ús: `c = model.Add(...)` seguit de `if lit is not None: c.OnlyEnforceIf(lit)`.
        Amb explicar_infeasible actiu, el literal s'assumeix cert en resoldre;
        si el model és INFEASIBLE, el nucli de conflicte diu quins grups xoquen.
        """
        if not self.explicar_infeasible:
            return None
        if clau not in self._literals_assumpcio:
            lit = self.model.NewBoolVar(f"assumpcio_{clau}")
            self._literals_assumpcio[clau] = lit
            self.etiquetes_assumpcions[lit.Index()] = etiqueta
        return self._literals_assumpcio[clau]

    def crear_variables(self):
        """Crea les variables de decisió del model """
        print("Creant variables de decisió...")
        
        hores_disponibles_per_curs = {}
        for c_idx, curs in self.curs_per_index.items():
            # Por defecto, todas las horas están disponibles
            disponible = set([(dia, hora) for dia in range(self.dies) for hora in range(self.hores_per_dia)])
            
            # Si hay horario_disponible, lo procesamos
            if 'horari_disponible' in curs and curs['horari_disponible']:
                # Sobreescribimos el conjunto con solo las horas disponibles
                disponible = set([(slot['dia'], slot['hora']) for slot in curs['horari_disponible']])
                print(f"Curs {curs['nom']}: {len(disponible)} hores disponibles definides")
            
            hores_disponibles_per_curs[c_idx] = disponible
    
        
        for assignacio in self.assignacions:
            professor_idx = assignacio['professor']
            modul_idx = assignacio['modul']
            hores_requerides = assignacio['hores']
            aula_preferida = assignacio['aula']
            subgrup = assignacio['subgrup']

            curs_idx = -1
            slots_modul = None       # None = sense restricció d'horari del mòdul
            aules_modul = None       # None = sense restricció d'aules del mòdul
            if modul_idx in self.modul_per_index:
                modul = self.modul_per_index[modul_idx]
                curs_idx = modul.get('curs', -1)

                # Restricció d'horari del mòdul (matí/tarda o slots concrets)
                if modul.get('horari_disponible'):
                    slots_modul = {(s.get('dia'), s.get('hora')) for s in modul['horari_disponible']}
                    print(f"Mòdul {modul.get('nom', modul_idx)}: restringit a {len(slots_modul)} slots")

                # Conjunt d'aules del mòdul (espai/equipament)
                if modul.get('aules_possibles'):
                    aules_modul = set(modul['aules_possibles'])

            # L'aula preferida de l'assignació ha de respectar el conjunt del mòdul;
            # si no hi és, mana el conjunt (el preprocessador ja n'ha advertit)
            if aula_preferida != -1 and aules_modul is not None and aula_preferida not in aules_modul:
                aula_preferida = -1

            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Verificar si esta hora es válida para aulas con restricción de tardes


                    if curs_idx != -1 and (dia, hora) not in hores_disponibles_per_curs.get(curs_idx, set()):
                        continue

                    # Slot fora de l'horari disponible del mòdul
                    if slots_modul is not None and (dia, hora) not in slots_modul:
                        continue

                    es_hora_tarda = hora >= 6

                    # Determinar aules possibles per a aquest slot.
                    #
                    # - Si el mòdul defineix "aules_possibles", aquest conjunt és la
                    #   restricció dura (espai/equipament) i el solver pot triar
                    #   qualsevol de les seves aules; l'aula indicada a l'assignació
                    #   (triada a l'editor) queda només com a PREFERÈNCIA suau
                    #   (peso_aula a la funció objectiu). Així, quan una aula se
                    #   satura, el solver pot reubicar la classe en una altra aula
                    #   permesa en comptes de quedar condemnat (INFEASIBLE).
                    # - Si el mòdul no en defineix cap, es manté el comportament
                    #   clàssic: l'aula de l'assignació es fixa (o qualsevol aula si
                    #   és -1). Això evita fer créixer el model quan no cal.
                    if aules_modul is not None:
                        candidats = aules_modul
                    elif aula_preferida != -1:
                        candidats = (aula_preferida,)
                    else:
                        candidats = self.aula_per_index.keys()

                    aules_possibles = []
                    for aula_idx in candidats:
                        aula = self.aula_per_index.get(aula_idx)
                        if aula is None:
                            continue
                        # Restriccions pròpies de l'aula per a aquest slot/subgrup
                        if (aula.get('nomes_subgrups', False) and subgrup == 3) or \
                           (aula.get('nomes_tardes', False) and not es_hora_tarda):
                            continue
                        aules_possibles.append(aula_idx)

                    # Crear variables solo para combinaciones válidas
                    for aula_idx in aules_possibles:
                        var_name = f"m{modul_idx}_p{professor_idx}_d{dia}_h{hora}_a{aula_idx}_s{subgrup}"
                        var = self.model.NewBoolVar(var_name)
                        self.vars_assignacio[(modul_idx, professor_idx, dia, hora, aula_idx, subgrup)] = var
                        # Preferència suau per l'aula triada a l'editor
                        if aula_preferida != -1 and aula_idx != aula_preferida:
                            self.penalitzacio_aula.append(var)
            
            # Variable comptadora d'hores programades per aquesta assignació
            var_name = f"hores_m{modul_idx}_p{professor_idx}"
            self.hores_programades[(modul_idx, professor_idx)] = self.model.NewIntVar(hores_requerides, hores_requerides, var_name)
        
        
        # Variables per slots ocupats per professors
        for p_idx in self.professor_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    var_name = f"ocupat_p{p_idx}_d{dia}_h{hora}"
                    self.slots_ocupats_professor[(p_idx, dia, hora)] = self.model.NewBoolVar(var_name)
        
        # Variables per slots ocupats per aules
        for a_idx in self.aula_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    var_name = f"ocupat_a{a_idx}_d{dia}_h{hora}"
                    self.slots_ocupats_aula[(a_idx, dia, hora)] = self.model.NewBoolVar(var_name)
        
        # Variables per slots ocupats per cursos
        for c_idx in self.curs_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    var_name = f"ocupat_c{c_idx}_d{dia}_h{hora}"
                    self.slots_ocupats_curs[(c_idx, dia, hora)] = self.model.NewBoolVar(var_name)
        
        
        self.aula_usada_per_curs = {}
        for c_idx in self.curs_per_index:
            for a_idx in self.aula_per_index:
                var_name = f"aula_{a_idx}_usada_per_curs_{c_idx}"
                self.aula_usada_per_curs[(c_idx, a_idx)] = self.model.NewBoolVar(var_name)
        
        

    
        
        print(f"S'han creat {len(self.vars_assignacio)} variables d'assignació")
    def afegir_restriccions(self):
        """Afegeix totes les restriccions al model"""



        moduls_simultanis = {}  # {modul_idx: [(professor_idx, subgrup, assignacio_idx), ...]}

        for idx, assignacio in enumerate(self.assignacions):
            if assignacio.get('simultani', False):
                modul_idx = assignacio['modul']
                professor_idx = assignacio['professor']
                subgrup = assignacio['subgrup']
                print (f"  Módulo {modul_idx} asignado simultáneamente por profesor {professor_idx} en subgrupo {subgrup}")
                if modul_idx not in moduls_simultanis:
                    moduls_simultanis[modul_idx] = []
                
                moduls_simultanis[modul_idx].append((professor_idx, subgrup, idx))

        
        # 1. Restricció: Cada professor ha de tenir totes les seves hores i mòduls assignats
        print("Afegint restricció d'assignació completa per professor...")
        
        # Diccionari per agrupar les hores requerides per professor
        hores_totals_per_professor = {}
        
        # Calcular el total d'hores que hauria de tenir cada professor
        for assignacio in self.assignacions:
            p_idx = assignacio['professor']
            hores = assignacio['hores']
            
            if p_idx not in hores_totals_per_professor:
                hores_totals_per_professor[p_idx] = 0
            
            hores_totals_per_professor[p_idx] += hores
        
        # Per cada professor, assegurar que té exactament el nombre d'hores requerides
        for p_idx, total_hores in hores_totals_per_professor.items():
            # Recollir totes les variables d'assignació per aquest professor
            hores_assignades_vars = []
            
            for (m, p, d, h, a, s) in self.vars_assignacio:
                if p == p_idx:
                    hores_assignades_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
            
            # Restricció: La suma de totes les hores assignades ha de ser exactament igual al total d'hores requerides
            if hores_assignades_vars:
                self.model.Add(sum(hores_assignades_vars) == total_hores)
                print(f"  Professor {p_idx} ha de tenir exactament {total_hores} hores assignades")
        
        # Assegurar que cada mòdul de cada professor té exactament les hores requerides
        for assignacio in self.assignacions:
            p_idx = assignacio['professor']
            m_idx = assignacio['modul']
            hores_requerides = assignacio['hores']
            subgrup = assignacio['subgrup']
            
            # Recollir totes les variables d'assignació per aquesta combinació de professor, mòdul i subgrup
            assignacio_vars = []
            
            for (m, p, d, h, a, s) in self.vars_assignacio:
                if p == p_idx and m == m_idx and s == subgrup:
                    assignacio_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
            
            # Restricció: La suma de les hores assignades ha de ser exactament les hores requerides
            if assignacio_vars and hores_requerides > 0:
                self.model.Add(sum(assignacio_vars) == hores_requerides)
                print(f"  Mòdul {m_idx} del professor {p_idx} (subgrup {subgrup}) ha de tenir {hores_requerides} hores")

        # 2. Restricció: Un professor no pot estar en dos llocs alhora
        for p_idx in self.professor_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Recollir totes les variables d'assignació per aquest professor en aquest slot
                    slot_vars = []
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if p == p_idx and d == dia and h == hora:
                            slot_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    # Si la suma > 0, el professor està ocupat en aquest slot
                    if slot_vars:
                        self.model.Add(sum(slot_vars) >= 1).OnlyEnforceIf(self.slots_ocupats_professor[(p_idx, dia, hora)])
                        self.model.Add(sum(slot_vars) == 0).OnlyEnforceIf(self.slots_ocupats_professor[(p_idx, dia, hora)].Not())
                    else:
                        # If no variables exist for this slot, professor is never occupied here
                        self.model.Add(self.slots_ocupats_professor[(p_idx, dia, hora)] == 0)
                    
                    # Assegurar que no estigui en més d'un lloc alhora
                    self.model.Add(sum(slot_vars) <= 1)
        
        # 3. Restricció: Una aula no pot tenir més d'un mòdul DIFERENT alhora
        # (excepte mòduls simultanis). Diversos professors del MATEIX mòdul a la
        # mateixa aula/hora són UNA sola classe (co-docència: titular+suport,
        # reunions): comparteixen "tipus" i queden permesos.
        for a_idx in self.aula_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Agrupar variables por módulo
                    vars_por_modulo = {}
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if a == a_idx and d == dia and h == hora:
                            vars_por_modulo.setdefault(m, []).append(self.vars_assignacio[(m, p, d, h, a, s)])

                    # Crear variables para indicar si cada módulo está activo en este slot
                    modulo_activo = {}
                    for m, vars_m in vars_por_modulo.items():
                        var_name = f"modulo_{m}_activo_d{dia}_h{hora}_a{a_idx}"
                        modulo_activo[m] = self.model.NewBoolVar(var_name)
                        self.model.Add(sum(vars_m) >= 1).OnlyEnforceIf(modulo_activo[m])
                        self.model.Add(sum(vars_m) == 0).OnlyEnforceIf(modulo_activo[m].Not())

                    # Agrupar módulos simultáneos por tipo (cada mòdul normal és
                    # el seu propi tipus; els simultanis comparteixen tipus)
                    modulos_por_tipo = {}
                    for m in vars_por_modulo.keys():
                        if m in moduls_simultanis:
                            modulos_por_tipo.setdefault(m, []).append(m)
                        else:
                            modulos_por_tipo[f"normal_{m}"] = [m]

                    tipo_activo = {}
                    for tipo, modulos in modulos_por_tipo.items():
                        var_name = f"tipo_{tipo}_activo_d{dia}_h{hora}_a{a_idx}"
                        tipo_activo[tipo] = self.model.NewBoolVar(var_name)
                        modulos_activos = [modulo_activo[m] for m in modulos if m in modulo_activo]
                        if modulos_activos:
                            self.model.Add(sum(modulos_activos) >= 1).OnlyEnforceIf(tipo_activo[tipo])
                            self.model.Add(sum(modulos_activos) == 0).OnlyEnforceIf(tipo_activo[tipo].Not())
                        else:
                            self.model.Add(tipo_activo[tipo] == 0)

                    # Només un tipus de mòdul actiu per aula/hora
                    if len(tipo_activo) > 1:
                        self.model.Add(sum(tipo_activo.values()) <= 1)

                    # Ocupació de l'aula
                    todas_las_vars = [var for vars_list in vars_por_modulo.values() for var in vars_list]
                    if todas_las_vars:
                        self.model.Add(sum(todas_las_vars) >= 1).OnlyEnforceIf(self.slots_ocupats_aula[(a_idx, dia, hora)])
                        self.model.Add(sum(todas_las_vars) == 0).OnlyEnforceIf(self.slots_ocupats_aula[(a_idx, dia, hora)].Not())
                    else:
                        self.model.Add(self.slots_ocupats_aula[(a_idx, dia, hora)] == 0)

        # 4. Restricció: Un curs no pot tenir més d'una classe (per als alumnes)
        # alhora. Diversos professors del MATEIX mòdul i subgrup són UNA sola
        # classe (co-docència: titular + suport, reunions) i queden permesos: NO
        # es limita el nombre de professors per (mòdul, subgrup). El que es
        # prohibeix és tenir DOS mòduls diferents alhora per al mateix curs, o
        # grup sencer i subgrup a la vegada. És una impossibilitat física per
        # als alumnes i s'aplica sempre (també a les hores fixades).
        for c_idx in self.curs_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Agrupar variables per mòdul i subgrup
                    vars_per_modul_subgrup = {}
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if (m in self.modul_per_index and
                            self.modul_per_index[m].get('curs') == c_idx and
                            d == dia and h == hora):
                            vars_per_modul_subgrup.setdefault(m, {1: [], 2: [], 3: []})[s].append(
                                self.vars_assignacio[(m, p, d, h, a, s)])

                    # Un mateix mòdul no pot tenir grup sencer i subgrup alhora
                    for modul_idx, subgrups in vars_per_modul_subgrup.items():
                        grup_sencer_vars = subgrups[3]
                        subgrup_vars = subgrups[1] + subgrups[2]
                        if grup_sencer_vars and subgrup_vars:
                            grup_sencer_var = self.model.NewBoolVar(f"grup_sencer_{modul_idx}_{dia}_{hora}")
                            self.model.Add(sum(grup_sencer_vars) >= 1).OnlyEnforceIf(grup_sencer_var)
                            self.model.Add(sum(grup_sencer_vars) == 0).OnlyEnforceIf(grup_sencer_var.Not())
                            for var in subgrup_vars:
                                self.model.AddImplication(grup_sencer_var, var.Not())

                    subgrup1_actiu = {}
                    subgrup2_actiu = {}
                    grup_sencer_actiu = {}

                    for modul_idx, subgrups in vars_per_modul_subgrup.items():
                        # Crear variables para cada tipo de subgrupo por módulo
                        if subgrups[1]:
                            subgrup1_actiu[modul_idx] = self.model.NewBoolVar(f"subgrup1_{modul_idx}_actiu_{dia}_{hora}")
                            self.model.Add(sum(subgrups[1]) >= 1).OnlyEnforceIf(subgrup1_actiu[modul_idx])
                            self.model.Add(sum(subgrups[1]) == 0).OnlyEnforceIf(subgrup1_actiu[modul_idx].Not())

                        if subgrups[2]:
                            subgrup2_actiu[modul_idx] = self.model.NewBoolVar(f"subgrup2_{modul_idx}_actiu_{dia}_{hora}")
                            self.model.Add(sum(subgrups[2]) >= 1).OnlyEnforceIf(subgrup2_actiu[modul_idx])
                            self.model.Add(sum(subgrups[2]) == 0).OnlyEnforceIf(subgrup2_actiu[modul_idx].Not())

                        if subgrups[3]:
                            grup_sencer_actiu[modul_idx] = self.model.NewBoolVar(f"grup_sencer_{modul_idx}_actiu_{dia}_{hora}")
                            self.model.Add(sum(subgrups[3]) >= 1).OnlyEnforceIf(grup_sencer_actiu[modul_idx])
                            self.model.Add(sum(subgrups[3]) == 0).OnlyEnforceIf(grup_sencer_actiu[modul_idx].Not())

                    # Evitar superposición de grupos enteros
                    if len(grup_sencer_actiu) > 1:
                        self.model.Add(sum(grup_sencer_actiu.values()) <= 1)

                    # Evitar superposición del mismo subgrupo
                    if len(subgrup1_actiu) > 1:
                        self.model.Add(sum(subgrup1_actiu.values()) <= 1)

                    if len(subgrup2_actiu) > 1:
                        self.model.Add(sum(subgrup2_actiu.values()) <= 1)

                    # Restricción adicional: Si hay cualquier grupo entero activo, no puede haber ningún subgrupo activo
                    if grup_sencer_actiu and (subgrup1_actiu or subgrup2_actiu):
                        # Variable que indica si hay algún grupo entero activo para este curso en este slot
                        algun_grup_sencer = self.model.NewBoolVar(f"algun_grup_sencer_c{c_idx}_d{dia}_h{hora}")

                        # algun_grup_sencer es true si al menos un grup_sencer_actiu es true
                        self.model.Add(sum(grup_sencer_actiu.values()) >= 1).OnlyEnforceIf(algun_grup_sencer)
                        self.model.Add(sum(grup_sencer_actiu.values()) == 0).OnlyEnforceIf(algun_grup_sencer.Not())

                        # Si algun_grup_sencer es true, todos los subgrupos deben estar inactivos
                        for sg1 in subgrup1_actiu.values():
                            self.model.AddImplication(algun_grup_sencer, sg1.Not())

                        for sg2 in subgrup2_actiu.values():
                            self.model.AddImplication(algun_grup_sencer, sg2.Not())


        # 4.1 Hores consecutives per curs
        for c_idx in self.curs_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    slot_vars = []
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if m in self.modul_per_index and self.modul_per_index[m].get('curs') == c_idx and d == dia and h == hora:
                            slot_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    if slot_vars:
                        self.model.Add(sum(slot_vars) >= 1).OnlyEnforceIf(self.slots_ocupats_curs[(c_idx, dia, hora)])
                        self.model.Add(sum(slot_vars) == 0).OnlyEnforceIf(self.slots_ocupats_curs[(c_idx, dia, hora)].Not())
                    else:
                        self.model.Add(self.slots_ocupats_curs[(c_idx, dia, hora)] == 0)
                
                # Per cada dia, les classes han de ser consecutives (sense forats)
                hores_ocupades = [self.slots_ocupats_curs[(c_idx, dia, hora)] for hora in range(self.hores_per_dia)]
                
                # Buscar la primera i última hora
                for i in range(self.hores_per_dia - 1):
                    for j in range(i + 1, self.hores_per_dia):
                        # Si tenim classe a hora i i hora j, totes les hores entre mig també han de tenir classe
                        for k in range(i + 1, j):
                            self.model.Add(hores_ocupades[k] >= hores_ocupades[i] + hores_ocupades[j] - 1)
            
            # 4.2 Restricción para que los subgrupos no tengan horas muertas
            for c_idx in self.curs_per_index:
                for dia in range(self.dies):
                    # Obtener las variables de ocupación para cada tipo de subgrupo/grupo
                    slots_ocupats_sg1 = {}  # subgrupo 1
                    slots_ocupats_sg2 = {}  # subgrupo 2
                    slots_ocupats_sencer = {}  # grupo entero
                    
                    for hora in range(self.hores_per_dia):
                                # Para subgrupo 1
                                sg1_vars = []
                                for (m, p, d, h, a, s) in self.vars_assignacio:
                                    if (m in self.modul_per_index and 
                                        self.modul_per_index[m].get('curs') == c_idx and 
                                        d == dia and h == hora and s == 1):
                                        sg1_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
                                
                                if sg1_vars:
                                    slots_ocupats_sg1[hora] = self.model.NewBoolVar(f"sg1_ocupat_c{c_idx}_d{dia}_h{hora}")
                                    self.model.Add(sum(sg1_vars) >= 1).OnlyEnforceIf(slots_ocupats_sg1[hora])
                                    self.model.Add(sum(sg1_vars) == 0).OnlyEnforceIf(slots_ocupats_sg1[hora].Not())
                                
                                # Para subgrupo 2
                                sg2_vars = []
                                for (m, p, d, h, a, s) in self.vars_assignacio:
                                    if (m in self.modul_per_index and 
                                        self.modul_per_index[m].get('curs') == c_idx and 
                                        d == dia and h == hora and s == 2):
                                        sg2_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
                                
                                if sg2_vars:
                                    slots_ocupats_sg2[hora] = self.model.NewBoolVar(f"sg2_ocupat_c{c_idx}_d{dia}_h{hora}")
                                    self.model.Add(sum(sg2_vars) >= 1).OnlyEnforceIf(slots_ocupats_sg2[hora])
                                    self.model.Add(sum(sg2_vars) == 0).OnlyEnforceIf(slots_ocupats_sg2[hora].Not())
                                
                                # Para grupo entero
                                sencer_vars = []
                                for (m, p, d, h, a, s) in self.vars_assignacio:
                                    if (m in self.modul_per_index and 
                                        self.modul_per_index[m].get('curs') == c_idx and 
                                        d == dia and h == hora and s == 3):
                                        sencer_vars.append(self.vars_assignacio[(m, p, d, h, a, s)])
                                
                                if sencer_vars:
                                    slots_ocupats_sencer[hora] = self.model.NewBoolVar(f"sencer_ocupat_c{c_idx}_d{dia}_h{hora}")
                                    self.model.Add(sum(sencer_vars) >= 1).OnlyEnforceIf(slots_ocupats_sencer[hora])
                                    self.model.Add(sum(sencer_vars) == 0).OnlyEnforceIf(slots_ocupats_sencer[hora].Not())
                    
                    # NUEVO: Crear variables para "horas relevantes" para cada estudiante
                    # Para estudiantes del subgrupo 1: cualquier hora con clase de SG1 o grupo entero
                    hores_rellevants_sg1 = {}
                    for hora in range(self.hores_per_dia):
                        # Una hora es relevante si hay clase de SG1 o de grupo entero
                        if hora in slots_ocupats_sg1 or hora in slots_ocupats_sencer:
                            hores_rellevants_sg1[hora] = self.model.NewBoolVar(f"hora_rellevant_sg1_c{c_idx}_d{dia}_h{hora}")
                            
                            # Definir condiciones para que la hora sea relevante
                            if hora in slots_ocupats_sg1 and hora in slots_ocupats_sencer:
                                # La hora es relevante si hay clase de SG1 O de grupo entero
                                self.model.Add(slots_ocupats_sg1[hora] + slots_ocupats_sencer[hora] >= 1).OnlyEnforceIf(hores_rellevants_sg1[hora])
                                self.model.Add(slots_ocupats_sg1[hora] + slots_ocupats_sencer[hora] == 0).OnlyEnforceIf(hores_rellevants_sg1[hora].Not())
                            elif hora in slots_ocupats_sg1:
                                self.model.Add(hores_rellevants_sg1[hora] == slots_ocupats_sg1[hora])
                            else:
                                self.model.Add(hores_rellevants_sg1[hora] == slots_ocupats_sencer[hora])
                    
                    # Para estudiantes del subgrupo 2: cualquier hora con clase de SG2 o grupo entero
                    hores_rellevants_sg2 = {}
                    for hora in range(self.hores_per_dia):
                        if hora in slots_ocupats_sg2 or hora in slots_ocupats_sencer:
                            hores_rellevants_sg2[hora] = self.model.NewBoolVar(f"hora_rellevant_sg2_c{c_idx}_d{dia}_h{hora}")
                            
                            if hora in slots_ocupats_sg2 and hora in slots_ocupats_sencer:
                                self.model.Add(slots_ocupats_sg2[hora] + slots_ocupats_sencer[hora] >= 1).OnlyEnforceIf(hores_rellevants_sg2[hora])
                                self.model.Add(slots_ocupats_sg2[hora] + slots_ocupats_sencer[hora] == 0).OnlyEnforceIf(hores_rellevants_sg2[hora].Not())
                            elif hora in slots_ocupats_sg2:
                                self.model.Add(hores_rellevants_sg2[hora] == slots_ocupats_sg2[hora])
                            else:
                                self.model.Add(hores_rellevants_sg2[hora] == slots_ocupats_sencer[hora])
                    
                    # RESTRICCIÓN CLAVE: Aplicar consecutividad a las horas relevantes, no solo a las del subgrupo
                    # Para el subgrupo 1
                    if len(hores_rellevants_sg1) >= 2:
                        hores_ordenades = sorted(hores_rellevants_sg1.keys())
                        primera_hora = hores_ordenades[0]
                        ultima_hora = hores_ordenades[-1]
                        
                        # Para cada hora entre la primera y la última
                        for hora in range(primera_hora + 1, ultima_hora):
                            if hora in hores_rellevants_sg1:
                                # Para cada combinación de horas antes y después de esta
                                for hora_abans in range(primera_hora, hora):
                                    for hora_despres in range(hora + 1, ultima_hora + 1):
                                        if hora_abans in hores_rellevants_sg1 and hora_despres in hores_rellevants_sg1:
                                            # Si hay clase en hora_abans y hora_despres, debe haber clase en hora
                                            self.model.Add(
                                                hores_rellevants_sg1[hora] >= 
                                                hores_rellevants_sg1[hora_abans] + hores_rellevants_sg1[hora_despres] - 1
                                            )
                    
                    # Para el subgrupo 2 (mismo enfoque)
                    if len(hores_rellevants_sg2) >= 2:
                        hores_ordenades = sorted(hores_rellevants_sg2.keys())
                        primera_hora = hores_ordenades[0]
                        ultima_hora = hores_ordenades[-1]
                        
                        for hora in range(primera_hora + 1, ultima_hora):
                            if hora in hores_rellevants_sg2:
                                for hora_abans in range(primera_hora, hora):
                                    for hora_despres in range(hora + 1, ultima_hora + 1):
                                        if hora_abans in hores_rellevants_sg2 and hora_despres in hores_rellevants_sg2:
                                            self.model.Add(
                                                hores_rellevants_sg2[hora] >= 
                                                hores_rellevants_sg2[hora_abans] + hores_rellevants_sg2[hora_despres] - 1
                                            )                                                       
        #5. Restricció: Tutoria NO pot ser a primera o última hora efectiva
        for modul in self.moduls:
            if modul.get('es_tutoria', False):
                modul_idx = modul['index']
                curs_idx = modul.get('curs', -1)
                
                if curs_idx == -1:
                    continue  # Saltar si no està assignat a un curs
                
                # Per cada dia
                for dia in range(self.dies):
                    # Si la tutoria s'ha posat a mà aquest dia, no es valida la posició
                    if self._modul_dia_fixat(modul_idx, dia):
                        continue
                    # Crear variables que indiquen si el curs té alguna classe en cada hora
                    te_classe = [None] * self.hores_per_dia
                    for hora in range(self.hores_per_dia):
                        var_name = f"te_classe_c{curs_idx}_d{dia}_h{hora}"
                        te_classe[hora] = self.model.NewBoolVar(var_name)
                        
                        # Recollir totes les assignacions per aquest curs, dia i hora
                        vars_assignacio_hora = []
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if (m in self.modul_per_index and 
                                self.modul_per_index[m].get('curs') == curs_idx and 
                                d == dia and h == hora):
                                vars_assignacio_hora.append(self.vars_assignacio[(m, p, d, h, a, s)])
                        
                        # Si hi ha alguna assignació, te_classe = 1
                        if vars_assignacio_hora:
                            self.model.Add(sum(vars_assignacio_hora) >= 1).OnlyEnforceIf(te_classe[hora])
                            self.model.Add(sum(vars_assignacio_hora) == 0).OnlyEnforceIf(te_classe[hora].Not())
                        else:
                            self.model.Add(te_classe[hora] == 0)
                    
                    # Crear variables que indiquen si una hora és la primera amb classe
                    es_primera = [None] * self.hores_per_dia
                    for hora in range(self.hores_per_dia):
                        var_name = f"es_primera_tut_c{curs_idx}_d{dia}_h{hora}"
                        es_primera[hora] = self.model.NewBoolVar(var_name)
                        
                        # És la primera hora si té classe i totes les anteriors no en tenen
                        condicions = [te_classe[hora]]
                        for h_anterior in range(hora):
                            condicions.append(te_classe[h_anterior].Not())
                        
                        if len(condicions) > 1:
                            self.model.AddBoolAnd(condicions).OnlyEnforceIf(es_primera[hora])
                            self.model.AddBoolOr([c.Not() for c in condicions]).OnlyEnforceIf(es_primera[hora].Not())
                        else:
                            # Si és la primera hora absoluta, només cal que tingui classe
                            self.model.Add(te_classe[hora] == 1).OnlyEnforceIf(es_primera[hora])
                            self.model.Add(te_classe[hora] == 0).OnlyEnforceIf(es_primera[hora].Not())
                    
                    # Crear variables que indiquen si una hora és l'última amb classe
                    es_ultima = [None] * self.hores_per_dia
                    for hora in range(self.hores_per_dia):
                        var_name = f"es_ultima_tut_c{curs_idx}_d{dia}_h{hora}"
                        es_ultima[hora] = self.model.NewBoolVar(var_name)
                        
                        # És l'última hora si té classe i totes les posteriors no en tenen
                        condicions = [te_classe[hora]]
                        for h_posterior in range(hora + 1, self.hores_per_dia):
                            condicions.append(te_classe[h_posterior].Not())
                        
                        if len(condicions) > 1:
                            self.model.AddBoolAnd(condicions).OnlyEnforceIf(es_ultima[hora])
                            self.model.AddBoolOr([c.Not() for c in condicions]).OnlyEnforceIf(es_ultima[hora].Not())
                        else:
                            # Si és l'última hora absoluta, només cal que tingui classe
                            self.model.Add(te_classe[hora] == 1).OnlyEnforceIf(es_ultima[hora])
                            self.model.Add(te_classe[hora] == 0).OnlyEnforceIf(es_ultima[hora].Not())
                    
                    # Restricció: Tutoria NO pot ser a primera o última hora efectiva
                    nom_curs = self.curs_per_index.get(curs_idx, {}).get('nom', curs_idx)
                    lit_tutoria = self._assumpcio(
                        f"tutoria_m{modul_idx}",
                        f"Tutoria de {nom_curs} mai a primera ni última hora")
                    for hora in range(self.hores_per_dia):
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if m == modul_idx and d == dia and h == hora:
                                # Aquesta variable ha de ser 0 si l'hora és primera o última
                                c = self.model.Add(
                                    self.vars_assignacio[(m, p, d, h, a, s)] <= 1 - (es_primera[hora] + es_ultima[hora])
                                )
                                if lit_tutoria is not None:
                                    c.OnlyEnforceIf(lit_tutoria)
        
        # 6. Restricció: FOL i Anglès han de ser a primera o última hora efectiva del curs,
        # amb possibilitat d'hores consecutives del mateix mòdul
        # --- Restricció 6: FOL i Anglès només a primera o última hora efectiva del curs ---
        for modul in self.moduls:
            if modul.get('es_fol', False) or modul.get('es_angles', False):
                modul_idx = modul['index']
                curs_idx = modul.get('curs', -1)

                if curs_idx == -1:
                    continue  # Saltar si no està assignat a un curs

                nom_curs = self.curs_per_index.get(curs_idx, {}).get('nom', curs_idx)
                lit_folang = self._assumpcio(
                    f"folang_m{modul_idx}",
                    f"{modul.get('nom', modul_idx)} ({nom_curs}) sempre a primera o última hora (FOL/anglès)")

                for dia in range(self.dies):
                    # Si el mòdul (FOL/anglès) s'ha posat a mà aquest dia, no es
                    # valida la posició a primera/última hora.
                    if self._modul_dia_fixat(modul_idx, dia):
                        continue
                    # Variables per marcar si el mòdul es fa en cada hora
                    assignat_hora = [None] * self.hores_per_dia
                    for hora in range(self.hores_per_dia):
                        assignat_hora[hora] = self.model.NewBoolVar(
                            f"modul{modul_idx}_c{curs_idx}_d{dia}_h{hora}"
                        )
                        # Vincular amb les variables d’assignació reals
                        vars_assignacio_hora = []
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if m == modul_idx and d == dia and h == hora:
                                vars_assignacio_hora.append(self.vars_assignacio[(m, p, d, h, a, s)])
                        if vars_assignacio_hora:
                            self.model.Add(sum(vars_assignacio_hora) == 1).OnlyEnforceIf(assignat_hora[hora])
                            self.model.Add(sum(vars_assignacio_hora) == 0).OnlyEnforceIf(assignat_hora[hora].Not())
                        else:
                            self.model.Add(assignat_hora[hora] == 0)

                    # Cas 1: Una sola hora → només pot estar a la primera o a l’última
                    una_hora = self.model.NewBoolVar(f"una_hora_modul{modul_idx}_d{dia}")
                    self.model.Add(sum(assignat_hora) == 1).OnlyEnforceIf(una_hora)
                    self.model.Add(sum(assignat_hora) != 1).OnlyEnforceIf(una_hora.Not())

                    # Cas 2: Dues hores consecutives → només pot ser (0,1) o (n-2,n-1)
                    dues_hores = self.model.NewBoolVar(f"dues_hores_modul{modul_idx}_d{dia}")
                    self.model.Add(sum(assignat_hora) == 2).OnlyEnforceIf(dues_hores)
                    self.model.Add(sum(assignat_hora) != 2).OnlyEnforceIf(dues_hores.Not())

                    # Restriccions per al cas 1 (una sola hora)
                    if self.hores_per_dia >= 2:
                        self.model.AddBoolOr([
                            assignat_hora[0],                     # primera
                            assignat_hora[self.hores_per_dia-1]   # última
                        ]).OnlyEnforceIf([una_hora, lit_folang] if lit_folang is not None else una_hora)

                    # Restriccions per al cas 2 (dues hores seguides)
                    if self.hores_per_dia >= 2:
                        bloc_inici = self.model.NewBoolVar(f"bloc_inici_modul{modul_idx}_d{dia}")
                        bloc_final = self.model.NewBoolVar(f"bloc_final_modul{modul_idx}_d{dia}")

                        self.model.AddBoolAnd([assignat_hora[0], assignat_hora[1]]).OnlyEnforceIf(bloc_inici)
                        self.model.AddBoolOr([assignat_hora[0].Not(), assignat_hora[1].Not()]).OnlyEnforceIf(bloc_inici.Not())

                        self.model.AddBoolAnd([
                            assignat_hora[self.hores_per_dia-2],
                            assignat_hora[self.hores_per_dia-1]
                        ]).OnlyEnforceIf(bloc_final)
                        self.model.AddBoolOr([
                            assignat_hora[self.hores_per_dia-2].Not(),
                            assignat_hora[self.hores_per_dia-1].Not()
                        ]).OnlyEnforceIf(bloc_final.Not())

                        # Només es permet si és bloc d’inici o de final
                        self.model.AddBoolOr([bloc_inici, bloc_final]).OnlyEnforceIf(
                            [dues_hores, lit_folang] if lit_folang is not None else dues_hores)

                    # Casos invàlids: més d’1 i menys de 2 hores → prohibit
                    c = self.model.Add(sum(assignat_hora) <= 2)
                    if lit_folang is not None:
                        c.OnlyEnforceIf(lit_folang)
        
        # 7. Restricció: Respectar restriccions horàries dels professors
        for professor in self.professors:
            professor_idx = professor['index']
            restriccions = professor.get('restriccions', {})

            lit_desiderata = None
            if restriccions.get('no_disponible'):
                lit_desiderata = self._assumpcio(
                    f"desiderata_p{professor_idx}",
                    f"Desiderates (hores no disponibles) de {professor.get('nom', professor_idx)}")

            # No disponible (les hores manuals fixades en queden exemptes:
            # una hora posada a mà pot caure en una franja no disponible)
            for dia, hora in restriccions.get('no_disponible', []):
                if self._es_fixat(professor_idx, dia, hora):
                    continue
                for (m, p, d, h, a, s) in self.vars_assignacio:
                    if p == professor_idx and d == dia and h == hora:
                        c = self.model.Add(self.vars_assignacio[(m, p, d, h, a, s)] == 0)
                        if lit_desiderata is not None:
                            c.OnlyEnforceIf(lit_desiderata)
            
            # Prefereix no (penalització, no restricció dura)
            # Aquí podem afegir una penalització a la funció objectiu

        # 9. Restricció: Un professor no pot fer més de 6 hores diàries
        for p_idx in self.professor_per_index:
            for dia in range(self.dies):
                # Recollir totes les variables d'assignació per aquest professor i dia
                hores_diaries = []
                for hora in range(self.hores_per_dia):
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if p == p_idx and d == dia and h == hora:
                            hores_diaries.append(self.vars_assignacio[(m, p, d, h, a, s)])
                
                # Límit d'hores per dia (6, o 7 amb '7hores'). Amb fixar_horari,
                # les hores manuals no es validen: el límit no baixa mai per sota
                # del nombre d'hores ja fixades aquell dia (max(base, fixades)).
                nom_prof = self.professor_per_index[p_idx].get('nom', p_idx)
                if hores_diaries:
                    base = 7 if self.professor_per_index[p_idx].get('7hores', False) else 6
                    n_fix = sum(1 for h in range(self.hores_per_dia)
                                if self._es_fixat(p_idx, dia, h))
                    c = self.model.Add(sum(hores_diaries) <= max(base, n_fix))
                    lit = self._assumpcio(
                        f"maxhores_p{p_idx}",
                        f"Màxim {base} hores diàries de {nom_prof}"
                        + ("" if base != 6 else " (es pot ampliar amb '7hores')"))
                    if lit is not None:
                        c.OnlyEnforceIf(lit)

        # 10. Restricció: descans mínim de 12 h entre l'última classe d'un dia i
        # la primera de l'endemà. Es prohibeix qualsevol parella de classes en
        # dies consecutius amb menys de 12 h de separació (durada de classe: 60
        # min). Les parelles on totes dues hores són manuals (fixades) queden
        # exemptes: les hores fixades no es validen entre elles.
        DURADA_CLASSE_MIN = 60
        DESCANS_MINIM_MIN = 12 * 60
        for p_idx in self.professor_per_index:
            nom_prof = self.professor_per_index[p_idx].get('nom', p_idx)
            lit_descans = self._assumpcio(
                f"descans_p{p_idx}",
                f"Descans de 12 h de {nom_prof} entre l'última classe d'un dia "
                f"i la primera de l'endemà")
            for dia in range(1, self.dies):
                for h_prev in range(self.hores_per_dia):
                    fi_prev = self.hores_inici_min[h_prev] + DURADA_CLASSE_MIN
                    for h_next in range(self.hores_per_dia):
                        descans = (1440 - fi_prev) + self.hores_inici_min[h_next]
                        if descans >= DESCANS_MINIM_MIN:
                            continue
                        if (self._es_fixat(p_idx, dia - 1, h_prev) and
                                self._es_fixat(p_idx, dia, h_next)):
                            continue
                        c = self.model.Add(
                            self.slots_ocupats_professor[(p_idx, dia - 1, h_prev)] +
                            self.slots_ocupats_professor[(p_idx, dia, h_next)] <= 1)
                        if lit_descans is not None:
                            c.OnlyEnforceIf(lit_descans)
        
        

        
        # 12. Restricció: Un professor no pot tenir lliure ni dilluns ni divendres
        for p_idx in self.professor_per_index:
            prof = self.professor_per_index[p_idx]
            # Els professors "lliures de restriccions" no tenen l'exigència de
            # classe dilluns/divendres i poden tenir diversos dies lliures.
            if not prof.get('controlable', False) or prof.get('lliureRestriccions', False):
                continue

            te_classe_dia = []
            for dia in range(self.dies):
                # Recoger todas las clases de este profesor en este día
                classes_dia = []
                for hora in range(self.hores_per_dia):
                    classes_dia.append(self.slots_ocupats_professor[(p_idx, dia, hora)])
                
                # Variable que indica si el profesor tiene al menos una clase este día
                var_name = f"te_classe_p{p_idx}_d{dia}"
                te_classe = self.model.NewBoolVar(var_name)
                self.model.Add(sum(classes_dia) >= 1).OnlyEnforceIf(te_classe)
                self.model.Add(sum(classes_dia) == 0).OnlyEnforceIf(te_classe.Not())
                te_classe_dia.append(te_classe)
            
            lit_dies = self._assumpcio(
                f"dies_p{p_idx}",
                f"Règim de dies de {self.professor_per_index[p_idx].get('nom', p_idx)} "
                f"(classe dilluns i divendres, dies lliures)")

            def _amb_lit_dies(c):
                if lit_dies is not None:
                    c.OnlyEnforceIf(lit_dies)

            # Todos deben tener clase lunes y viernes
            _amb_lit_dies(self.model.Add(te_classe_dia[0] == 1))  # Lunes (día 0)
            if self.dies >= 5:
                _amb_lit_dies(self.model.Add(te_classe_dia[4] == 1))  # Viernes (día 4)

            # Solo aplicar restricción de días libres a profesores que pueden tenerlos
            if self.professor_per_index[p_idx].get('DiesLliures', False):
                # Días intermedios (martes a jueves) - simplemente contar cuántos días tienen clase
                dies_intermedis = te_classe_dia[1:4] if self.dies >= 5 else te_classe_dia[1:self.dies]

                if dies_intermedis:
                    # Al menos deben tener clase 2 de los 3 días intermedios (máximo 1 día libre)
                    _amb_lit_dies(self.model.Add(sum(dies_intermedis) >= len(dies_intermedis) - 1))

                    nom_professor = self.professor_per_index[p_idx]['nom']
                    print(f"  {nom_professor} puede tener como máximo 1 día libre entre martes y jueves")
            else:
                # Si no tiene DiesLliures, debe tener clase todos los días
                for dia in range(self.dies):
                    _amb_lit_dies(self.model.Add(te_classe_dia[dia] == 1))


        
        # 11. Restricció: Subgrup 1 + grup sencer <= 4 hores, i subgrup 2 + grup sencer <= 4 hores
        for p_idx in self.professor_per_index:
            for c_idx in self.curs_per_index:
                for dia in range(self.dies):
                    # Recoger variables para el grupo entero (subgrupo 3)
                    hores_grup_sencer = []
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if (p == p_idx and d == dia and s == 3 and 
                            m in self.modul_per_index and self.modul_per_index[m].get('curs') == c_idx):
                            hores_grup_sencer.append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    # Recoger variables para subgrupo 1 y limitar su suma con el grupo entero
                    hores_subgrup1 = []
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if (p == p_idx and d == dia and s == 1 and 
                            m in self.modul_per_index and self.modul_per_index[m].get('curs') == c_idx):
                            hores_subgrup1.append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    # Restricción: Subgrupo 1 + Grupo entero <= 4
                    if hores_subgrup1 or hores_grup_sencer:
                        self.model.Add(sum(hores_subgrup1) + sum(hores_grup_sencer) <= 4)
                        nom_professor = self.professor_per_index[p_idx]['nom']
                        nom_curs = self.curs_per_index[c_idx]['nom']
                    
                    # Recoger variables para subgrupo 2 y limitar su suma con el grupo entero
                    hores_subgrup2 = []
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if (p == p_idx and d == dia and s == 2 and 
                            m in self.modul_per_index and self.modul_per_index[m].get('curs') == c_idx):
                            hores_subgrup2.append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    # Restricción: Subgrupo 2 + Grupo entero <= 4
                    if hores_subgrup2 or hores_grup_sencer:
                        self.model.Add(sum(hores_subgrup2) + sum(hores_grup_sencer) <= 4)
                        nom_professor = self.professor_per_index[p_idx]['nom']
                        nom_curs = self.curs_per_index[c_idx]['nom']
        
        
        # Para cada módulo con múltiples asignaciones simultáneas
        for modul_idx, assignacions in moduls_simultanis.items():
            if len(assignacions) > 1:
                nom_modul = self.modul_per_index[modul_idx]['nom'] if modul_idx in self.modul_per_index else f"Módulo {modul_idx}"
                print(f"  Módulo simultáneo: {nom_modul} con {len(assignacions)} asignaciones")
                
                # Tomar la primera asignación como referencia
                professor_base, subgrup_base, idx_base = assignacions[0]
                
                # Para cada combinación de día y hora posible
                for dia in range(self.dies):
                    for hora in range(self.hores_per_dia):
                        # Recoger todas las variables de la asignación base en este slot
                        vars_base = []
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if m == modul_idx and p == professor_base and d == dia and h == hora and s == subgrup_base:
                                vars_base.append((self.vars_assignacio[(m, p, d, h, a, s)], a))
                        
                        if not vars_base:
                            continue
                        
                        # Para cada otra asignación simultánea del mismo módulo
                        for professor_other, subgrup_other, idx_other in assignacions[1:]:
                            # Para cada posible asignación base (combinación de aula)
                            for var_base, aula_base in vars_base:
                                # La asignación base implica que la otra asignación debe ocurrir en la misma aula
                                vars_other = []
                                for (m, p, d, h, a, s) in self.vars_assignacio:
                                    if (m == modul_idx and p == professor_other and 
                                        d == dia and h == hora and s == subgrup_other and a == aula_base):
                                        vars_other.append(self.vars_assignacio[(m, p, d, h, a, s)])
                                
                                # Si var_base=1, exactamente una de vars_other debe ser 1
                                if vars_other:
                                    # Si este slot se asigna al profesor base, también debe asignarse al otro profesor
                                    self.model.Add(sum(vars_other) == 1).OnlyEnforceIf(var_base)
                                    # Si este slot no se asigna al profesor base, tampoco debe asignarse al otro profesor
                                    self.model.Add(sum(vars_other) == 0).OnlyEnforceIf(var_base.Not())
                                    
                                    prof_base = self.professor_per_index[professor_base]['nom']
                                    prof_other = self.professor_per_index[professor_other]['nom']
                                    print(f"    {prof_base} (SG{subgrup_base}) y {prof_other} (SG{subgrup_other}) deben enseñar juntos")
        
                                   
        
        # Suport: un professor de suport només imparteix el mòdul a les hores en
        # què el TITULAR també el fa (mateix mòdul, mateixa hora). Així el suport
        # —o els assistents a una reunió, on un professor n'és el titular i la
        # resta hi consten com a suport— segueixen sempre el titular.
        titular_vars_mdh = {}   # (modul, dia, hora) -> vars de titular (no suport)
        hi_ha_suport = False
        for (m, p, d, h, a, s), var in self.vars_assignacio.items():
            if self.assig_es_suport.get((m, p, a, s), False):
                hi_ha_suport = True
            else:
                titular_vars_mdh.setdefault((m, d, h), []).append(var)

        if hi_ha_suport:
            print("Afegint restriccions de suport (acompanya el titular)...")
            for (m, p, d, h, a, s), var in self.vars_assignacio.items():
                if not self.assig_es_suport.get((m, p, a, s), False):
                    continue
                nom_prof = self.professor_per_index.get(p, {}).get('nom', p)
                lit_suport = self._assumpcio(
                    f"suport_p{p}",
                    f"Suport de {nom_prof}: acompanya el titular del seu mòdul a la mateixa hora")
                titulars = titular_vars_mdh.get((m, d, h), [])
                if titulars:
                    # Si el suport es col·loca aquí, el titular hi ha de ser també
                    enforce = [var] + ([lit_suport] if lit_suport is not None else [])
                    self.model.Add(sum(titulars) >= 1).OnlyEnforceIf(enforce)
                else:
                    # Cap titular no pot fer el mòdul en aquest (dia,hora): el suport tampoc
                    c = self.model.Add(var == 0)
                    if lit_suport is not None:
                        c.OnlyEnforceIf(lit_suport)

        if hasattr(self, 'moduls_projectes') and self.moduls_projectes and hasattr(self, 'slots_projectes'):
            print("Añadiendo restricciones para módulos de proyectos...")
            
            # Contar cuántas restricciones se aplican
            restricciones_aplicadas = 0
            
            for modul_idx in self.moduls_projectes:
                if modul_idx not in self.modul_per_index:
                    print(f"  ⚠️ Advertencia: Módulo de proyecto {modul_idx} no encontrado en los datos")
                    continue
                    
                modul_nom = self.modul_per_index[modul_idx].get('nom', f"Módulo {modul_idx}")
                print(f"  Aplicando restricciones de horario para proyecto: {modul_nom}")
                
                lit_projecte = self._assumpcio(
                    f"projecte_m{modul_idx}",
                    f"Projecte {modul_nom}: només als slots d'horaris de projectes")

                # Para cada posible asignación de este módulo
                for (m, p, d, h, a, s) in self.vars_assignacio:
                    if m == modul_idx:
                        # Si esta combinación día/hora no está en los slots permitidos, prohibirla
                        if (d, h) not in self.slots_projectes:
                            c = self.model.Add(self.vars_assignacio[(m, p, d, h, a, s)] == 0)
                            if lit_projecte is not None:
                                c.OnlyEnforceIf(lit_projecte)
                            restricciones_aplicadas += 1
                
            print(f"  Se han aplicado {restricciones_aplicadas} restricciones para módulos de proyectos")

        # Cargar grupos de módulos coordinados
        moduls_coordinats = self.config.get('moduls_especials', {}).get('moduls_coordinats', [])
        print(self.config)
        for grup in moduls_coordinats:
            moduls_indices = grup.get('moduls', [])
            if len(moduls_indices) < 2:
                print(f"  Grupo {grup.get('nom', 'sin nombre')} tiene menos de 2 módulos, no se aplican restricciones")
                continue  # Necesitamos al menos 2 módulos para coordinar
                
            nom_grup = grup.get('nom', 'Grupo sin nombre')
            
            # Para cada combinación de día y hora
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Para cada par de módulos en el grupo
                    for i in range(len(moduls_indices)):
                        for j in range(i+1, len(moduls_indices)):
                            modul_i = moduls_indices[i]
                            modul_j = moduls_indices[j]
                            
                            # Variables para el primer módulo en este slot
                            vars_i = []
                            for (m, p, d, h, a, s) in self.vars_assignacio:
                                if m == modul_i and d == dia and h == hora:
                                    vars_i.append(self.vars_assignacio[(m, p, d, h, a, s)])
                            
                            # Variables para el segundo módulo en este slot
                            vars_j = []
                            for (m, p, d, h, a, s) in self.vars_assignacio:
                                if m == modul_j and d == dia and h == hora:
                                    vars_j.append(self.vars_assignacio[(m, p, d, h, a, s)])
                            
                            # Si alguno de los módulos no tiene variables para este slot, continuamos
                            if not vars_i or not vars_j:
                                continue
                            
                            # Crear variables booleanas para representar si se imparte cada módulo
                            modul_i_activo = self.model.NewBoolVar(f"m{modul_i}_d{dia}_h{hora}_activo")
                            modul_j_activo = self.model.NewBoolVar(f"m{modul_j}_d{dia}_h{hora}_activo")
                            
                            # Conectar las variables booleanas con la suma de las variables originales
                            self.model.Add(sum(vars_i) >= 1).OnlyEnforceIf(modul_i_activo)
                            self.model.Add(sum(vars_i) == 0).OnlyEnforceIf(modul_i_activo.Not())
                            
                            self.model.Add(sum(vars_j) >= 1).OnlyEnforceIf(modul_j_activo)
                            self.model.Add(sum(vars_j) == 0).OnlyEnforceIf(modul_j_activo.Not())
                            
                            # Crear la implicación bidireccional: i activo ⟺ j activo
                            lit_coord = self._assumpcio(
                                f"coord_{nom_grup}",
                                f"Mòduls coordinats '{nom_grup}' (han d'anar a la mateixa hora)")
                            if lit_coord is not None:
                                self.model.AddImplication(modul_i_activo, modul_j_activo).OnlyEnforceIf(lit_coord)
                                self.model.AddImplication(modul_j_activo, modul_i_activo).OnlyEnforceIf(lit_coord)
                            else:
                                self.model.AddImplication(modul_i_activo, modul_j_activo)
                                self.model.AddImplication(modul_j_activo, modul_i_activo)
                            
                            # Información de debug
                            nom_i = self.modul_per_index[modul_i]['nom'] if modul_i in self.modul_per_index else f"Módulo {modul_i}"
                            nom_j = self.modul_per_index[modul_j]['nom'] if modul_j in self.modul_per_index else f"Módulo {modul_j}"
                            print(f"    Coordinando {nom_i} y {nom_j} (día {dia}, hora {hora})")
        
        
        
        for assignacio in self.assignacions:
            p_idx = assignacio['professor']
            m_idx = assignacio['modul']
            s_idx = assignacio['subgrup']
            particio = assignacio.get('particio', [])

            # `particio` és una llista de particions PERMESES (cada partició és
            # una llista de longituds de blocs, hores consecutives el mateix dia,
            # un bloc per dia). Buit => cap restricció (repartiment lliure). Amb
            # diverses particions, el solver en TRIA una (disjunció); amb una de
            # sola, es força (equivalent al comportament clàssic).
            particions = [p for p in particio if p] if particio else []
            if not particions:
                continue

            profesor_nom = self.professor_per_index.get(p_idx, {}).get('nom', f"Profesor {p_idx}")
            modul_nom = self.modul_per_index.get(m_idx, {}).get('nom', f"Módulo {m_idx}")
            print(f"  Aplicando partición(es) {particions} para {profesor_nom} en {modul_nom}")

            # -------- Preparar matriz de variables por día y hora --------
            vars_dia_horas = [
                [
                    sum(self.vars_assignacio[(m_idx, p_idx, d, h, a, s_idx)]
                        for (m, p, d2, h2, a, s) in self.vars_assignacio
                        if m == m_idx and p == p_idx and s == s_idx and d2 == d and h2 == h)
                    if any(m == m_idx and p == p_idx and s == s_idx and d2 == d and h2 == h
                        for (m, p, d2, h2, a, s) in self.vars_assignacio)
                    else self.model.NewConstant(0)
                    for h in range(self.hores_per_dia)
                ]
                for d in range(self.dies)
            ]

            # -------- Selector de partició (disjunció): exactament una activa --
            use = [self.model.NewBoolVar(f"usepart{i}_{m_idx}_{p_idx}_{s_idx}")
                   for i in range(len(particions))]
            self.model.Add(sum(use) == 1)

            for i, part in enumerate(particions):
                # -------- Variables de bloques por índice, día y hora --------
                y = {}
                for j, longitud in enumerate(part):
                    for d in range(self.dies):
                        for h in range(self.hores_per_dia - longitud + 1):
                            y[j, d, h] = self.model.NewBoolVar(f"y_p{i}_b{j}_d{d}_h{h}_{m_idx}_{p_idx}_{s_idx}")

                    # Cada bloque j se coloca una vez SI la partició i és activa
                    self.model.Add(
                        sum(y[j, d, h] for d in range(self.dies)
                                        for h in range(self.hores_per_dia - longitud + 1)) == use[i]
                    )

                # -------- Vincular y[j,d,h] con las asignaciones horarias ------
                for j, longitud in enumerate(part):
                    for d in range(self.dies):
                        for h in range(self.hores_per_dia - longitud + 1):
                            for k in range(longitud):
                                self.model.Add(
                                    vars_dia_horas[d][h + k] >= y[j, d, h]
                                )

                # -------- Evitar solapamientos: solo un bloque por día ---------
                for d in range(self.dies):
                    self.model.Add(
                        sum(y[j, d, h]
                            for j, longitud in enumerate(part)
                            for h in range(self.hores_per_dia - longitud + 1)) <= 1
                    )

        
        # 1. Minimizar horas muertas (huecos) de los profesores
        print("  Configurando minimización de horas muertas...")
        horas_muertas_vars = {}
        total_horas_muertas = 0

        for p_idx in self.professor_per_index:
            nom_professor = self.professor_per_index[p_idx]['nom']
            print(f"    Analizando horario de {nom_professor}")
            
            for dia in range(self.dies):
                # Para cada profesor y día, detectar horas muertas usando un enfoque diferente
                # Para cada par posible de horas con clase con un hueco entre ellas
                for hora1 in range(self.hores_per_dia - 2):
                    for hora2 in range(hora1 + 2, self.hores_per_dia):
                        # Para cada hora intermedia entre hora1 y hora2
                        for hora_intermedia in range(hora1 + 1, hora2):
                            # Si hay clase en hora1 y hora2 pero no en hora_intermedia, es una hora muerta
                            hora_muerta = self.model.NewBoolVar(f"hora_muerta_p{p_idx}_d{dia}_h{hora_intermedia}")
                            
                            # Esta hora es "muerta" si: clase en hora1 Y clase en hora2 Y NO clase en hora_intermedia
                            self.model.AddBoolAnd([
                                self.slots_ocupats_professor[(p_idx, dia, hora1)],
                                self.slots_ocupats_professor[(p_idx, dia, hora2)],
                                self.slots_ocupats_professor[(p_idx, dia, hora_intermedia)].Not()
                            ]).OnlyEnforceIf(hora_muerta)
                            
                            # Condición inversa para hora_muerta.Not()
                            self.model.AddBoolOr([
                                self.slots_ocupats_professor[(p_idx, dia, hora1)].Not(),
                                self.slots_ocupats_professor[(p_idx, dia, hora2)].Not(),
                                self.slots_ocupats_professor[(p_idx, dia, hora_intermedia)]
                            ]).OnlyEnforceIf(hora_muerta.Not())
                            
                            horas_muertas_vars[(p_idx, dia, hora_intermedia)] = hora_muerta
                            total_horas_muertas += hora_muerta

        # 2. Minimizar horas que los profesores prefieren no tener clase
        #    (hores grogues, desiderata tipus 1). Amb ignora_hores_grogues no
        #    s'afegeix cap penalització (el bucle no itera cap professor).
        print("  Configurando minimización de horas no preferidas...")
        preferencias_no_respetadas = 0
        if self.ignora_hores_grogues:
            print("  Preferències 'prefereix no' (hores grogues) IGNORADES per opció.")

        for professor in ([] if self.ignora_hores_grogues else self.professors):
            p_idx = professor['index']
            restriccions = professor.get('restriccions', {})
            nom_professor = professor.get('nom', f"Profesor {p_idx}")
            
            # Procesar preferencias (horas que prefiere no tener clase)
            for dia, hora in restriccions.get('prefereix_no', []):
                # Les hores manuals fixades no es penalitzen (no es validen)
                if self._es_fixat(p_idx, dia, hora):
                    continue
                print(f"    {nom_professor} prefiere no tener clase el día {dia}, hora {hora}")

                # Variable que indica si se respeta esta preferencia
                no_respetada = self.model.NewBoolVar(f"pref_no_respetada_p{p_idx}_d{dia}_h{hora}")
                
                # La preferencia no se respeta si el profesor tiene clase en esa hora
                self.model.Add(self.slots_ocupats_professor[(p_idx, dia, hora)] == 1).OnlyEnforceIf(no_respetada)
                self.model.Add(self.slots_ocupats_professor[(p_idx, dia, hora)] == 0).OnlyEnforceIf(no_respetada.Not())
                
                preferencias_no_respetadas += no_respetada

        # Añadir objetivos a la función de minimización
        print("  Configurando función objetivo...")
        # Definir pesos relativos para cada objetivo (ajustar según prioridades)
        peso_horas_muertas = 10
        peso_preferencias = 20
        # Pes baix: mantenir l'aula preferida només trenca empats i cedeix davant
        # d'hores mortes o preferències de professor. El solver reubica el mínim
        # d'hores necessari quan una aula queda saturada.
        peso_aula = 1

        objetivo_total = (total_horas_muertas * peso_horas_muertas
                          + preferencias_no_respetadas * peso_preferencias
                          + sum(self.penalitzacio_aula) * peso_aula)

        # Añadir la función objetivo al modelo
        self.model.Minimize(objetivo_total)
        print(f"  Objetivo configurado: {peso_horas_muertas}*horas_muertas "
              f"+ {peso_preferencias}*preferencias_no_respetadas "
              f"+ {peso_aula}*aules_no_preferides ({len(self.penalitzacio_aula)} vars)")



                
    def atura(self):
        """Demana aturar la cerca en curs (cridable des d'un altre fil).

        CP-SAT s'atura de manera neta i Solve() retorna la millor solució
        trobada fins al moment (FEASIBLE) o UNKNOWN si encara no n'hi havia cap.
        """
        self.atura_demanada = True
        if self.cp_solver is not None:
            self.cp_solver.stop_search()

    def resoldre(self, max_time_seconds: float = 900, num_workers: int = 8,
                 log_search_progress: bool = True, output_path: str = 'solucio_horaris.json',
                 solution_callback=None):
        """Resol el model i retorna la solució.

        Args:
            max_time_seconds: límit de temps del solver CP-SAT.
            num_workers: threads de cerca paral·lela.
            log_search_progress: mostrar el log de cerca d'OR-Tools.
            output_path: fitxer on guardar la solució (None = no guardar).
            solution_callback: CpSolverSolutionCallback opcional; CP-SAT el crida
                a cada solució millorada (útil per informar de progrés).
        """

        print("Iniciant la resolució del model...")




        # Crear solucionador
        solver = cp_model.CpSolver()
        self.cp_solver = solver

        # Configuració
        solver.parameters.optimize_with_core = True
        solver.parameters.linearization_level = 2
        solver.parameters.max_time_in_seconds = max_time_seconds
        solver.parameters.log_search_progress = log_search_progress
        solver.parameters.num_search_workers = num_workers
        solver.parameters.cp_model_presolve = True

        # Si l'aturada s'ha demanat abans de començar, no val la pena cercar
        if self.atura_demanada:
            solver.parameters.max_time_in_seconds = 0.01

        # Literals d'assumpció per explicar l'infactibilitat (si estan actius)
        if self._literals_assumpcio:
            self.model.AddAssumptions(list(self._literals_assumpcio.values()))
            print(f"Explicació d'infactibilitat activa: {len(self._literals_assumpcio)} grups de restriccions")

        # Resoldre el model
        start_time = time.time()
        if solution_callback is not None:
            status = solver.Solve(self.model, solution_callback)
        else:
            status = solver.Solve(self.model)
        end_time = time.time()
        
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            self.ultim_estat = 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE'
            print(f"Solució trobada en {end_time - start_time:.2f} segons")
            
            # Processar la solució
            solucio = {
                'horari': [[[[] for _ in range(self.hores_per_dia)] for _ in range(self.dies)] for _ in range(len(self.cursos))],
                'professors': [[[[] for _ in range(self.hores_per_dia)] for _ in range(self.dies)] for _ in range(len(self.professors))],
                'aules': [[[[] for _ in range(self.hores_per_dia)] for _ in range(self.dies)] for _ in range(len(self.aules))],
                'stats': {
                    'temps_resolucio': end_time - start_time,
                    'conflictes': solver.NumConflicts(),
                    'branques': solver.NumBranches(),
                    'estat': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE',
                    'objectiu': solver.ObjectiveValue()
                }
            }
            
            # Obtenir les assignacions
            for (m, p, d, h, a, s), var in self.vars_assignacio.items():
                if solver.Value(var) == 1:
                    modul = self.modul_per_index.get(m, {'nom': f'Mòdul {m}'})
                    professor = self.professor_per_index.get(p, {'nom': f'Professor {p}'})
                    curs_idx = modul.get('curs', -1)
                    
                    # Flags de co-docència (suport) i simultani de l'assignació,
                    # per no perdre'ls a la solució de sortida
                    suport_cella, simultani_cella = self._flags_assignacio(m, p, a, s)

                    # Afegir a l'horari del curs
                    if 0 <= curs_idx < len(solucio['horari']):
                        solucio['horari'][curs_idx][d][h].append({
                            'modul': modul['nom'],
                            'modul_index': m,
                            'professor': professor['nom'],
                            'professor_index': p,
                            'aula': self.aula_per_index.get(a, {'nom': f'Aula {a}'})['nom'],
                            'aula_index': a,
                            'subgrup': s,
                            'suport': suport_cella,
                            'simultani': simultani_cella
                        })
                    
                    # Afegir a l'horari del professor
                    if 0 <= p < len(solucio['professors']):
                        solucio['professors'][p][d][h].append({
                            'modul': modul['nom'],
                            'modul_index': m,
                            'curs': self.curs_per_index.get(curs_idx, {'nom': f'Curs {curs_idx}'})['nom'],
                            'curs_index': curs_idx,
                            'aula': self.aula_per_index.get(a, {'nom': f'Aula {a}'})['nom'],
                            'aula_index': a,
                            'subgrup': s
                        })
                    
                    # Afegir a l'horari de l'aula
                    if 0 <= a < len(solucio['aules']):
                        solucio['aules'][a][d][h].append({
                            'modul': modul['nom'],
                            'modul_index': m,
                            'professor': professor['nom'],
                            'professor_index': p,
                            'curs': self.curs_per_index.get(curs_idx, {'nom': f'Curs {curs_idx}'})['nom'],
                            'curs_index': curs_idx,
                            'subgrup': s
                        })
            
            # Guardar solució en JSON
            if output_path:
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(solucio, f, ensure_ascii=False, indent=2)
                print(f"Solució guardada a '{output_path}'")
            return solucio
        else:
            print("No s'ha trobat cap solució")
            if status == cp_model.MODEL_INVALID:
                self.ultim_estat = 'MODEL_INVALID'
                print("El model és invàlid")
            elif status == cp_model.INFEASIBLE:
                self.ultim_estat = 'INFEASIBLE'
                print("El model és infactible")

                if self.etiquetes_assumpcions:
                    self._explica_infactibilitat(solver, max_time_seconds, num_workers)
            else:
                self.ultim_estat = 'UNKNOWN'
                print("Estat desconegut")
            return None

    def afegir_restriccions_horari_fixat(self):
        """Força les hores pre-assignades (horari_fixat) a la seva posició exacta.

        Cada entrada de horari_fixat és {professor, modul, subgrup, dia, hora, aula}
        (aula -1 = qualsevol de les possibles). Si l'aula és concreta es fixa la
        variable exacta; si no, es força que el mòdul passi en aquell slot en
        alguna de les aules per a les quals existeix variable.
        """
        print(f"Afegint {len(self.horari_fixat)} hores pre-assignades com a restriccions...")

        # Índex (modul, professor, dia, hora, subgrup) -> variables (una per aula)
        vars_per_slot = {}
        for (m, p, d, h, a, s), var in self.vars_assignacio.items():
            vars_per_slot.setdefault((m, p, d, h, s), []).append(var)

        fixades = 0
        for fix in self.horari_fixat:
            m, p, s = fix['modul'], fix['professor'], fix['subgrup']
            d, h, a = fix['dia'], fix['hora'], fix.get('aula', -1)

            nom_prof = self.professor_per_index.get(p, {}).get('nom', p)
            lit_fixat = self._assumpcio(f"fixat_p{p}", f"Hores fixades a mà de {nom_prof}")

            if a != -1 and (m, p, d, h, a, s) in self.vars_assignacio:
                c = self.model.Add(self.vars_assignacio[(m, p, d, h, a, s)] == 1)
                if lit_fixat is not None:
                    c.OnlyEnforceIf(lit_fixat)
                fixades += 1
            else:
                candidates = vars_per_slot.get((m, p, d, h, s))
                if candidates:
                    c = self.model.Add(sum(candidates) == 1)
                    if lit_fixat is not None:
                        c.OnlyEnforceIf(lit_fixat)
                    fixades += 1
                else:
                    print(f"  AVÍS: no hi ha cap variable per fixar el mòdul {m} del "
                          f"professor {p} (dia {d}, hora {h}, subgrup {s}); es descarta")

        print(f"S'han fixat {fixades} hores pre-assignades")
        return fixades

    def _explica_infactibilitat(self, solver_original, max_time_seconds: float, num_workers: int):
        """Calcula motiu_infeasible: el mínim de grups de restriccions a relaxar.

        Segona resolució del mateix model amb els literals d'assumpció lliures i
        l'objectiu de minimitzar quants grups es violen. El resultat és el
        conjunt mínim (o gairebé) de grups que fan impossible l'horari: molt més
        útil que el nucli en brut de CP-SAT, que sol ser enorme. Si aquesta
        segona resolució no conclou dins del temps, es recorre al nucli en brut.
        """
        # Nucli en brut, com a pla B
        nucli = solver_original.SufficientAssumptionsForInfeasibility()
        nucli_brut = sorted({self.etiquetes_assumpcions[i]
                             for i in nucli if i in self.etiquetes_assumpcions})

        print("Buscant el mínim de restriccions a relaxar...")
        lits = list(self._literals_assumpcio.values())
        self.model.ClearAssumptions()
        self.model.Minimize(sum(1 - lit for lit in lits))

        solver2 = cp_model.CpSolver()
        self.cp_solver = solver2  # perquè atura() també funcioni en aquesta fase
        solver2.parameters.max_time_in_seconds = max_time_seconds
        solver2.parameters.num_search_workers = num_workers
        solver2.parameters.log_search_progress = False
        if self.atura_demanada:
            solver2.parameters.max_time_in_seconds = 0.01

        status2 = solver2.Solve(self.model)

        if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.motiu_infeasible = sorted(
                self.etiquetes_assumpcions[lit.Index()]
                for lit in lits if solver2.Value(lit) == 0)
            if status2 != cp_model.OPTIMAL:
                print("(mínim no demostrat: pot ser que es pugui relaxar menys)")
        elif status2 == cp_model.INFEASIBLE:
            # Ni relaxant tots els grups hi ha horari: el conflicte és estructural
            self.motiu_infeasible = []
        else:
            # Temps esgotat: retornem el nucli en brut (suficient, no mínim)
            self.motiu_infeasible = nucli_brut

        if self.motiu_infeasible:
            print("Per poder generar un horari cal relaxar com a mínim:")
            for motiu in self.motiu_infeasible:
                print(f"  - {motiu}")
        else:
            print("El conflicte és a les restriccions estructurals (hores exactes, "
                  "solapaments, horaris disponibles de cursos/mòduls...)")

    def executar(self, fixar_horari: bool = False, explicar_infeasible: bool = False,
                 ignora_hores_grogues: bool = False, **kwargs):
        """Mètode principal per executar tot el procés.

        Args:
            fixar_horari: si és cert i les dades contenen hores pre-assignades
                (horari_fixat), queden forçades a la seva posició exacta.
            explicar_infeasible: si és cert, en cas d'INFEASIBLE motiu_infeasible
                conté els grups de restriccions que formen el conflicte
                (té un cost de rendiment; per defecte desactivat).
            ignora_hores_grogues: si és cert, s'ignoren les preferències
                "prefereix no" (hores grogues, desiderata tipus 1); les hores
                "no disponible" (vermelles, tipus 2) segueixen sent dures.
        """
        self.explicar_infeasible = explicar_infeasible
        self.ignora_hores_grogues = ignora_hores_grogues
        # Les hores manuals només queden exemptes si realment es fixen.
        self.fixar_horari = bool(fixar_horari and self.horari_fixat)
        self.crear_variables()
        self.afegir_restriccions()
        if fixar_horari and self.horari_fixat:
            self.afegir_restriccions_horari_fixat()
        elif self.horari_fixat:
            print(f"AVÍS: hi ha {len(self.horari_fixat)} hores pre-assignades però "
                  f"fixar_horari és fals: el solver les ignora")
        return self.resoldre(**kwargs)

    def mostrar_horari_curs(self, solucio, curs_idx):
        """Mostra l'horari d'un curs específic"""
        if curs_idx not in self.curs_per_index:
            print(f"No s'ha trobat el curs amb índex {curs_idx}")
            return
        
        curs = self.curs_per_index[curs_idx]
        print(f"\n=== HORARI DEL CURS: {curs['nom']} ===")
        
        dies = ['Dilluns', 'Dimarts', 'Dimecres', 'Dijous', 'Divendres']
        hores = [f"{h+8}:00" for h in range(self.hores_per_dia)]
        
        # Capçalera
        print(f"{'Hora':^10}", end="")
        for dia in dies:
            print(f"{dia:^25}", end="")
        print()
        
        # Contingut
        for h, hora in enumerate(hores):
            print(f"{hora:^10}", end="")
            for d in range(self.dies):
                classes = solucio['horari'][curs_idx][d][h]
                if classes:
                    info = []
                    for classe in classes:
                        subgrup_info = f" (SG{classe['subgrup']})" if classe['subgrup'] < 3 else ""
                        info.append(f"{classe['modul']}{subgrup_info}\n{classe['professor']}\n{classe['aula']}")
                    print(f"{', '.join(info):^25}", end="")
                else:
                    print(f"{'---':^25}", end="")
            print()

def main():
    try:
        # Iniciar el solver
        solver = HorariSolver('dades_solver_processades.json')
        
        # Executar tot el procés
        solucio = solver.executar()
        
        if solucio:
            # Mostrar horari d'alguns cursos

            
            print("\nS'ha generat l'horari amb èxit!")
            print(f"Temps de resolució: {solucio['stats']['temps_resolucio']:.2f} segons")
            print(f"Nombre de conflictes: {solucio['stats']['conflictes']}")
            print(f"Nombre de branques: {solucio['stats']['branques']}")
            print("\nS'ha generat l'horari amb èxit!")
            print(f"Temps de resolució: {solucio['stats']['temps_resolucio']:.2f} segons")
            print(f"Nombre de conflictes: {solucio['stats']['conflictes']}")
            print(f"Nombre de branques: {solucio['stats']['branques']}")
            
            # ===== INICI DEPURACIÓ =====
            print("\n===== ESTADÍSTIQUES DE LA SOLUCIÓ =====")
            
            # 1. Estadístiques per professors
            print("\n--- Assignacions per professor ---")
            hores_per_professor = {}
            dies_ocupats_per_professor = {}
            dies_setmana = ['Dilluns', 'Dimarts', 'Dimecres', 'Dijous', 'Divendres']
            
            for p_idx, prof_horari in enumerate(solucio['professors']):
                if p_idx in solver.professor_per_index:
                    nom_professor = solver.professor_per_index[p_idx]['nom']
                    
                    # Comptabilitzar hores
                    total_hores = 0
                    dies_ocupats = set()
                    
                    for d_idx, dia_horari in enumerate(prof_horari):
                        hores_dia = 0
                        for h_idx, hora_classes in enumerate(dia_horari):
                            if hora_classes:  # Si té classes en aquesta hora
                                total_hores += 1
                                hores_dia += 1
                                dies_ocupats.add(d_idx)
                        
                        if hores_dia > 0:
                            print(f"   {nom_professor} - {dies_setmana[d_idx]}: {hores_dia} hores")
                    
                    hores_per_professor[p_idx] = total_hores
                    dies_ocupats_per_professor[p_idx] = dies_ocupats
                    
                    print(f"{nom_professor}: {total_hores} hores totals, {len(dies_ocupats)} dies ocupats")
                    
                    # Verificar que no té dies lliures en dilluns/divendres
                    if 0 not in dies_ocupats:
                        print(f"   ⚠️ ALERTA: {nom_professor} té lliure el dilluns!")
                    if 4 not in dies_ocupats and len(dies_setmana) >= 5:
                        print(f"   ⚠️ ALERTA: {nom_professor} té lliure el divendres!")
                    
                    # Verificar càrrega diària
                    for d_idx, dia_horari in enumerate(prof_horari):
                        hores_dia = sum(1 for hora_classes in dia_horari if hora_classes)
                        if hores_dia > 6:
                            print(f"   ⚠️ ALERTA: {nom_professor} té {hores_dia} hores en {dies_setmana[d_idx]}!")
            
            # Estadístiques globals de professors
            print("\n--- Resum de professors ---")
            print(f"Total professors amb assignacions: {len(hores_per_professor)}")
            print(f"Mitjana d'hores per professor: {sum(hores_per_professor.values()) / len(hores_per_professor) if hores_per_professor else 0:.2f}")
            print(f"Professor amb més hores: {max(hores_per_professor.items(), key=lambda x: x[1]) if hores_per_professor else 'Cap'}")
            print(f"Professor amb menys hores: {min(hores_per_professor.items(), key=lambda x: x[1]) if hores_per_professor else 'Cap'}")
            
            # 2. Estadístiques per cursos
            print("\n--- Assignacions per curs ---")
            hores_per_curs = {}
            moduls_assignats_per_curs = {}
            
            for c_idx, curs_horari in enumerate(solucio['horari']):
                if c_idx in solver.curs_per_index:
                    nom_curs = solver.curs_per_index[c_idx]['nom']
                    total_hores = 0
                    moduls_assignats = set()
                    
                    for dia_horari in curs_horari:
                        for hora_classes in dia_horari:
                            for classe in hora_classes:
                                total_hores += 1
                                moduls_assignats.add(classe['modul_index'])
                    
                    hores_per_curs[c_idx] = total_hores
                    moduls_assignats_per_curs[c_idx] = moduls_assignats
                    
                    print(f"{nom_curs}: {total_hores} hores, {len(moduls_assignats)} mòduls diferents")
                    
                    # Verificar si tots els mòduls del curs estan assignats
                    if c_idx in solver.moduls_per_curs:
                        moduls_totals = set(solver.moduls_per_curs[c_idx])
                        moduls_faltants = moduls_totals - moduls_assignats
                        if moduls_faltants:
                            print(f"   ⚠️ ALERTA: {nom_curs} té {len(moduls_faltants)} mòduls sense assignar!")
            
            # 3. Estadístiques per aules
            print("\n--- Utilització d'aules ---")
            hores_per_aula = {}
            
            for a_idx, aula_horari in enumerate(solucio['aules']):
                if a_idx in solver.aula_per_index:
                    nom_aula = solver.aula_per_index[a_idx]['nom']
                    total_hores = 0
                    
                    for dia_horari in aula_horari:
                        for hora_classes in dia_horari:
                            if hora_classes:
                                total_hores += 1
                    
                    hores_per_aula[a_idx] = total_hores
                    print(f"{nom_aula}: {total_hores} hores utilitzades")
            
            # 4. Verificació de la solució
            print("\n--- Verificació de la solució ---")
            total_classes_programades = sum(len(classes) for prof in solucio['professors'] for dia in prof for classes in dia)
            total_hores_requerides = sum(assignacio['hores'] for assignacio in solver.assignacions)
            
            print(f"Total classes programades: {total_classes_programades}")
            print(f"Total hores requerides: {total_hores_requerides}")
            
            if total_classes_programades != total_hores_requerides:
                print(f"⚠️ ALERTA: Hi ha una discrepància entre classes programades i hores requerides!")
 


            genera_json_solucio_compatible(solucio, 'dades_finals.json')
            
            exportar_horaris_html(solucio, solver, "horaris_visuals.html")

    except Exception as e:
        print(f"Error en l'execució: {str(e)}")
        import traceback
        traceback.print_exc()


def genera_json_solucio_compatible(solucio, dades_solver_path=None, template=None,
                                   output_path='solucio_horaris_compatible.hor'):
    """Genera un JSON amb exactament el mateix format que Solver.json.

    Args:
        solucio: solució retornada per HorariSolver.resoldre().
        dades_solver_path: (obsolet, es mantenia per compatibilitat; no s'usa).
        template: dict amb format Solver.json a usar com a plantilla.
                  Si és None, es carrega 'Buit.json'.
        output_path: fitxer de sortida (None = no guardar).
    """
    import json
    import copy
    import time

    # Carregar la plantilla per obtenir l'estructura exacta
    if template is None:
        with open('Buit.json', 'r', encoding='utf-8') as f:
            template = json.load(f)

    # Crear una còpia exacta de l'estructura de Solver.json
    output = copy.deepcopy(template)
    
    # Actualitzar metadades
    output["autor"] = "HorariSolver"
    output["comentaris"] = "Solució generada automàticament"
    output["dataHora"] = int(time.time() * 1000)
    
    
    aules_assignades = {}
    
    # Recollir totes les assignacions del solver
    for p_idx, prof_horari in enumerate(solucio['professors']):
        for d_idx, dia_horari in enumerate(prof_horari):
            for h_idx, hora_classes in enumerate(dia_horari):
                for classe in hora_classes:
                    # Guardar l'aula assignada pel solver
                    key = (p_idx, classe['modul_index'], classe['subgrup'])
                    aules_assignades[key] = classe['aula_index']
    
    # Actualitzar les aules dels professors
    for professor in output["professors"]:
        if 'index' in professor and 'moduls' in professor:
            p_idx = professor['index']
            for modul in professor['moduls']:
                if 'index' in modul and 'subgrup' in modul:
                    m_idx = modul['index']
                    s_idx = modul['subgrup']
                    
                    # Buscar l'aula assignada pel solver
                    key = (p_idx, m_idx, s_idx)
                    if key in aules_assignades:
                        # Actualitzar l'aula del mòdul amb l'assignada pel solver
                        modul['aula'] = aules_assignades[key]


    # Inicialitzar correctament l'estructura de l'horari
    dies = 5
    hores = 12  # Normalment 12 hores de 8:00 a 20:00
    slots_per_hora = 18  # Observat als exemples de Solver.json
    
    # Crear estructura completa des de zero
    output["horari"] = []
    
    # Nivell 0 (principal): correspon a la vista general
    nivell_principal = []
    for d in range(dies):
        dia = []
        for h in range(hores):
            hora = [None] * slots_per_hora  # Inicialitzar amb nulls
            dia.append(hora)
        nivell_principal.append(dia)
    output["horari"].append(nivell_principal)
    
    # Afegir els altres 4 nivells (buits, però amb l'estructura correcta)
    for _ in range(4):
        output["horari"].append([])
    
    # Ara omplir amb les assignacions del solver
    for curs_idx, curs_horari in enumerate(solucio['horari']):
        for dia_idx, dia_horari in enumerate(curs_horari):
            for hora_idx, hora_classes in enumerate(dia_horari):
                for classe in hora_classes:
                    # Crear l'assignació en el format exacte de Solver.json
                    assignacio = {
                        "modul": classe['modul_index'],
                        "curs": classe.get('curs_index', curs_idx),
                        "aula": classe['aula_index'],
                        "subgrup": classe['subgrup'],
                        "suport": classe.get('suport', False),
                        "simultani": classe.get('simultani', False),
                        "profe": classe['professor_index']
                    }
                    
                    # Asignar directamente a la posición del professor
                    prof_idx = classe['professor_index']
                    if prof_idx < slots_per_hora:
                        output["horari"][0][dia_idx][hora_idx][prof_idx] = assignacio
    
    
    # Guardar a l'arxiu
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"Solució guardada amb format exacte de Solver.json a '{output_path}'")
    return output

if __name__ == "__main__":
    main()