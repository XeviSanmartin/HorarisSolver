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
        
        # Inicialització del model
        self.model = cp_model.CpModel()
        
        # Variables de decisió
        self.vars_assignacio = {}  # (modul, professor, dia, hora, aula, subgrup)
        
        # Variables auxiliars
        self.hores_programades = {}  # Comptador d'hores programades per mòdul
        self.slots_ocupats_professor = {}  # (professor, dia, hora) -> bool
        self.slots_ocupats_aula = {}  # (aula, dia, hora) -> bool
        self.slots_ocupats_curs = {}  # (curs, dia, hora) -> bool

    def carregar_dades(self, dades_path: str):
        """Carrega les dades del fitxer processat"""
        with open(dades_path, 'r', encoding='utf-8') as f:
            dades = json.load(f)
        
        self.professors = dades['professors']
        self.moduls = dades['moduls']
        self.cursos = dades['cursos']
        self.aules = dades['aules']
        self.especialitats = dades['especialitats']
        self.agrupacions = dades['agrupacions']
        self.config = dades['configuracio']
        
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
            if not a.get('aula_gran', not a.get('nomes_subgrups', False)):
                restriccions.append("aula petita (grup sencer només si el grup no necessita aula gran)")
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
                        'particio': modul_assign.get('particio', [])
                    })

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
            if modul_idx in self.modul_per_index:
                curs_idx = self.modul_per_index[modul_idx].get('curs', -1)

            curs_necessita_gran = self.curs_per_index.get(curs_idx, {}).get('necessita_aula_gran', True)


            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Verificar si esta hora es válida para aulas con restricción de tardes
                    

                    if curs_idx != -1 and (dia, hora) not in hores_disponibles_per_curs.get(curs_idx, set()):
                        continue  
              
                    es_hora_tarda = hora >= 6
                    
                    # Determinar aules possibles
                    if aula_preferida != -1:
                        # Si hay un aula preferida, verificar restricciones específicas
                        aula = self.aula_per_index.get(aula_preferida, {})
                        
                        # Verificar restricciones del aula preferida
                        es_aula_gran = aula.get('aula_gran', not aula.get('nomes_subgrups', False))
                        if (not es_aula_gran and subgrup == 3 and curs_necessita_gran) or \
                        (aula.get('nomes_tardes', False) and not es_hora_tarda):
                            continue  # Saltar esta combinación inválida
                        
                        aules_possibles = [aula_preferida]
                    else:
                        # Filtrar todas las aulas según las restricciones
                        aules_possibles = []
                        for a_idx, aula in self.aula_per_index.items():
                            # Verificar restricciones del aula
                            es_aula_gran = aula.get('aula_gran', not aula.get('nomes_subgrups', False))
                            if (not es_aula_gran and subgrup == 3 and curs_necessita_gran) or \
                            (aula.get('nomes_tardes', False) and not es_hora_tarda):
                                continue  # Saltar esta aula
                            
                            aules_possibles.append(a_idx)
                    
                    # Crear variables solo para combinaciones válidas
                    for aula_idx in aules_possibles:
                        var_name = f"m{modul_idx}_p{professor_idx}_d{dia}_h{hora}_a{aula_idx}_s{subgrup}"
                        self.vars_assignacio[(modul_idx, professor_idx, dia, hora, aula_idx, subgrup)] = self.model.NewBoolVar(var_name)
            
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
        
        # 3. Restricció: Una aula no pot tenir més d'una classe alhora (excepte mòduls simultanis del mateix tipus)
        for a_idx in self.aula_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Agrupar variables por módulo
                    vars_por_modulo = {}
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if a == a_idx and d == dia and h == hora:
                            if m not in vars_por_modulo:
                                vars_por_modulo[m] = []
                            vars_por_modulo[m].append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    # Crear variables para indicar si cada módulo está activo en este slot
                    modulo_activo = {}
                    for m, vars_m in vars_por_modulo.items():
                        var_name = f"modulo_{m}_activo_d{dia}_h{hora}_a{a_idx}"
                        modulo_activo[m] = self.model.NewBoolVar(var_name)
                        
                        # Si alguna variable del módulo es 1, el módulo está activo
                        self.model.Add(sum(vars_m) >= 1).OnlyEnforceIf(modulo_activo[m])
                        self.model.Add(sum(vars_m) == 0).OnlyEnforceIf(modulo_activo[m].Not())
                    
                    # NUEVA IMPLEMENTACIÓN: Agrupar módulos simultáneos por tipo
                    modulos_por_tipo = {}
                    for m in vars_por_modulo.keys():
                        # Si es un módulo simultáneo, usar su índice como clave
                        if m in moduls_simultanis:
                            if m not in modulos_por_tipo:
                                modulos_por_tipo[m] = []
                            modulos_por_tipo[m].append(m)
                        else:
                            # Si no es simultáneo, cada módulo tiene su propio tipo único
                            tipo_unico = f"normal_{m}"
                            modulos_por_tipo[tipo_unico] = [m]
                    
                    # Crear variables para indicar si cada tipo de módulo está activo
                    tipo_activo = {}
                    for tipo, modulos in modulos_por_tipo.items():
                        var_name = f"tipo_{tipo}_activo_d{dia}_h{hora}_a{a_idx}"
                        tipo_activo[tipo] = self.model.NewBoolVar(var_name)
                        
                        # El tipo está activo si alguno de sus módulos está activo
                        modulos_activos = [modulo_activo[m] for m in modulos if m in modulo_activo]
                        if modulos_activos:
                            self.model.Add(sum(modulos_activos) >= 1).OnlyEnforceIf(tipo_activo[tipo])
                            self.model.Add(sum(modulos_activos) == 0).OnlyEnforceIf(tipo_activo[tipo].Not())
                        else:
                            self.model.Add(tipo_activo[tipo] == 0)
                    
                    # RESTRICCIÓN CLAVE: Solo puede haber un tipo de módulo activo a la vez
                    if len(tipo_activo) > 1:
                        self.model.Add(sum(tipo_activo.values()) <= 1)
                    
                    # Actualizar variable de ocupación del aula
                    todas_las_vars = [var for vars_list in vars_por_modulo.values() for var in vars_list]
                    if todas_las_vars:
                        self.model.Add(sum(todas_las_vars) >= 1).OnlyEnforceIf(self.slots_ocupats_aula[(a_idx, dia, hora)])
                        self.model.Add(sum(todas_las_vars) == 0).OnlyEnforceIf(self.slots_ocupats_aula[(a_idx, dia, hora)].Not())
                    else:
                        self.model.Add(self.slots_ocupats_aula[(a_idx, dia, hora)] == 0)

        # 4. Restricció: Un curs no pot tenir més d'una classe alhora (excepte subgrups del mateix mòdul)
        for c_idx in self.curs_per_index:
            for dia in range(self.dies):
                for hora in range(self.hores_per_dia):
                    # Agrupar variables per mòdul i subgrup
                    vars_per_modul_subgrup = {}
                    
                    for (m, p, d, h, a, s) in self.vars_assignacio:
                        if (m in self.modul_per_index and 
                            self.modul_per_index[m].get('curs') == c_idx and 
                            d == dia and h == hora):
                            
                            # Agrupar per mòdul
                            if m not in vars_per_modul_subgrup:
                                vars_per_modul_subgrup[m] = {1: [], 2: [], 3: []}
                            
                            # Afegir variable al grup corresponent
                            vars_per_modul_subgrup[m][s].append(self.vars_assignacio[(m, p, d, h, a, s)])
                    
                    # Per cada mòdul, aplicar les restriccions
                    for modul_idx, subgrups in vars_per_modul_subgrup.items():
                        # 1. No es pot tenir més d'una classe del grup sencer (subgrup 3)
                        if subgrups[3]:
                            self.model.Add(sum(subgrups[3]) <= 1)
                        
                        # 2. No es pot tenir més d'una classe del subgrup 1
                        if subgrups[1]:
                            self.model.Add(sum(subgrups[1]) <= 1)
                        
                        # 3. No es pot tenir més d'una classe del subgrup 2
                        if subgrups[2]:
                            self.model.Add(sum(subgrups[2]) <= 1)
                        
                        # 4. No es pot tenir el grup sencer i algun subgrup alhora
                        grup_sencer_vars = subgrups[3]
                        subgrup_vars = subgrups[1] + subgrups[2]
                        
                        if grup_sencer_vars and subgrup_vars:
                            # Si hi ha classe de grup sencer, no pot haver-hi de subgrup
                            grup_sencer_var = self.model.NewBoolVar(f"grup_sencer_{modul_idx}_{dia}_{hora}")
                            self.model.Add(sum(grup_sencer_vars) >= 1).OnlyEnforceIf(grup_sencer_var)
                            self.model.Add(sum(grup_sencer_vars) == 0).OnlyEnforceIf(grup_sencer_var.Not())
                            
                            self.model.AddImplication(grup_sencer_var, self.model.NewBoolVar("zero").Not())
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
                    for hora in range(self.hores_per_dia):
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if m == modul_idx and d == dia and h == hora:
                                # Aquesta variable ha de ser 0 si l'hora és primera o última
                                self.model.Add(
                                    self.vars_assignacio[(m, p, d, h, a, s)] <= 1 - (es_primera[hora] + es_ultima[hora])
                                )
        
        # 6. Restricció: FOL i Anglès han de ser a primera o última hora efectiva del curs
        for modul in self.moduls:
            if modul.get('es_fol', False) or modul.get('es_angles', False):
                modul_idx = modul['index']
                curs_idx = modul.get('curs', -1)
                
                if curs_idx == -1:
                    continue  # Saltar si no està assignat a un curs
                
                # Per cada dia
                for dia in range(self.dies):
                    # Crear variables que indiquen si el curs té alguna classe en cada hora
                    te_classe = [None] * self.hores_per_dia
                    for hora in range(self.hores_per_dia):
                        var_name = f"te_classe_c{curs_idx}_d{dia}_h{hora}"
                        te_classe[hora] = self.model.NewBoolVar(var_name)
                        
                        # Recollir totes les assignacions per aquest curs, dia i hora (excepte FOL/Anglès)
                        vars_assignacio_hora = []
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if (m in self.modul_per_index and 
                                self.modul_per_index[m].get('curs') == curs_idx and 
                                d == dia and h == hora ): 
                                vars_assignacio_hora.append(self.vars_assignacio[(m, p, d, h, a, s)])
                        
                        # Si hi ha alguna assignació d'altres mòduls, te_classe = 1
                        if vars_assignacio_hora:
                            self.model.Add(sum(vars_assignacio_hora) >= 1).OnlyEnforceIf(te_classe[hora])
                            self.model.Add(sum(vars_assignacio_hora) == 0).OnlyEnforceIf(te_classe[hora].Not())
                        else:
                            self.model.Add(te_classe[hora] == 0)
                    
                    # Crear variables que indiquen si una hora és la primera amb classe
                    es_primera = [None] * self.hores_per_dia
                    for hora in range(self.hores_per_dia):
                        var_name = f"es_primera_c{curs_idx}_d{dia}_h{hora}"
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
                        var_name = f"es_ultima_c{curs_idx}_d{dia}_h{hora}"
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
                    
                    # Restricció: FOL/Anglès només pot ser a primera o última hora efectiva
                    for hora in range(self.hores_per_dia):
                        for (m, p, d, h, a, s) in self.vars_assignacio:
                            if m == modul_idx and d == dia and h == hora:
                                # Aquesta variable només pot ser 1 si l'hora és primera o última
                                self.model.Add(
                                    self.vars_assignacio[(m, p, d, h, a, s)] <= es_primera[hora] + es_ultima[hora]
                                )
        
        # 7. Restricció: Respectar restriccions horàries dels professors
        for professor in self.professors:
            professor_idx = professor['index']
            restriccions = professor.get('restriccions', {})
            
            # No disponible
            for dia, hora in restriccions.get('no_disponible', []):
                for (m, p, d, h, a, s) in self.vars_assignacio:
                    if p == professor_idx and d == dia and h == hora:
                        self.model.Add(self.vars_assignacio[(m, p, d, h, a, s)] == 0)
            
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
                
                # Limitar a 6 hores màxim per dia
                if hores_diaries and not self.professor_per_index[p_idx].get('7hores', False):
                    self.model.Add(sum(hores_diaries) <= 6)
                elif hores_diaries and self.professor_per_index[p_idx].get('7hores', False):
                    self.model.Add(sum(hores_diaries) <= 7)

        # 10. Restricció: Un professor no pot fer classe a primera hora si el dia anterior va tenir classe a última hora
        for p_idx in self.professor_per_index:
            for dia in range(1, self.dies):  # Comencem des del segon dia (dia 1)
                # Si el professor està ocupat l'última hora del dia anterior, no pot estar-ho a primera hora d'avui
                self.model.Add(
                    self.slots_ocupats_professor[(p_idx, dia, 0)] <= 
                    1 - self.slots_ocupats_professor[(p_idx, dia-1, self.hores_per_dia-1)]
                )
        
        

        
        # 12. Restricció: Un professor no pot tenir lliure ni dilluns ni divendres
        for p_idx in self.professor_per_index:
            if not self.professor_per_index[p_idx].get('controlable', False):
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
            
            # Todos deben tener clase lunes y viernes
            self.model.Add(te_classe_dia[0] == 1)  # Lunes (día 0)
            if self.dies >= 5:
                self.model.Add(te_classe_dia[4] == 1)  # Viernes (día 4)
            
            # Solo aplicar restricción de días libres a profesores que pueden tenerlos
            if self.professor_per_index[p_idx].get('DiesLliures', False):
                # Días intermedios (martes a jueves) - simplemente contar cuántos días tienen clase
                dies_intermedis = te_classe_dia[1:4] if self.dies >= 5 else te_classe_dia[1:self.dies]
                
                if dies_intermedis:
                    # Al menos deben tener clase 2 de los 3 días intermedios (máximo 1 día libre)
                    self.model.Add(sum(dies_intermedis) >= len(dies_intermedis) - 1)
                    
                    nom_professor = self.professor_per_index[p_idx]['nom']
                    print(f"  {nom_professor} puede tener como máximo 1 día libre entre martes y jueves")
            else:
                # Si no tiene DiesLliures, debe tener clase todos los días
                for dia in range(self.dies):
                    self.model.Add(te_classe_dia[dia] == 1)


        
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
        
        # 13. Restricción: Módulos simultáneos deben impartirse a la vez y en la misma aula
        print("Añadiendo restricción para módulos simultáneos...")

        # Identificar todas las asignaciones con simultani=true
        
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
                
        # 14. Restricción: Módulos coordinados deben impartirse a la misma hora
        print("Añadiendo restricción para módulos coordinados...")
        
       
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
                            self.model.AddImplication(modul_i_activo, modul_j_activo)
                            self.model.AddImplication(modul_j_activo, modul_i_activo)
                            
                            # Información de debug
                            nom_i = self.modul_per_index[modul_i]['nom'] if modul_i in self.modul_per_index else f"Módulo {modul_i}"
                            nom_j = self.modul_per_index[modul_j]['nom'] if modul_j in self.modul_per_index else f"Módulo {modul_j}"
                            print(f"    Coordinando {nom_i} y {nom_j} (día {dia}, hora {hora})")

        print("Añadiendo restricciones para particiones de horas (SIMPLIFICADA)...")
        """
        # Lista para acumular las penalizaciones
        penalizaciones_particion = []

        for assignacio in self.assignacions:
            p_idx = assignacio['professor']
            m_idx = assignacio['modul']
            subgrup = assignacio['subgrup']
            particions = assignacio.get('particio', [])
            
            if not particions:
                continue
            
            print(f"  Módulo {m_idx} (Prof: {p_idx}, SG: {subgrup}) - Particiones: {particions}")
            
            # Obtener tamaños permitidos para bloques
            tamaños_permitidos = set()
            for particion in particions:
                for tamaño in particion:
                    tamaños_permitidos.add(tamaño)
            
            print(f"    Tamaños de bloque permitidos: {tamaños_permitidos}")
            
            # Para cada día, identificar bloques y verificar tamaños
            vars_por_dia = {}
            for (m, p, d, h, a, s) in self.vars_assignacio:
                if m == m_idx and p == p_idx and s == subgrup:
                    if d not in vars_por_dia:
                        vars_por_dia[d] = []
                    vars_por_dia[d].append((h, self.vars_assignacio[(m, p, d, h, a, s)]))
            
            # Para cada día, detectar secuencias y crear penalizaciones para las de tamaño no permitido
            for dia, vars_horas in vars_por_dia.items():
                vars_horas.sort(key=lambda x: x[0])
                
                # Agrupar horas en posibles bloques consecutivos
                i = 0
                while i < len(vars_horas):
                    inicio_bloque = i
                    hora_actual = vars_horas[i][0]
                    
                    # Encontrar secuencia de horas consecutivas
                    j = i + 1
                    while j < len(vars_horas) and vars_horas[j][0] == hora_actual + 1:
                        hora_actual = vars_horas[j][0]
                        j += 1
                    
                    # Si encontramos un bloque potencial
                    if j > inicio_bloque + 1:  # Al menos 2 horas consecutivas
                        tamaño_bloque = j - inicio_bloque
                        
                        # Penalizar bloques de tamaños NO permitidos (soft constraint)
                        if tamaño_bloque not in tamaños_permitidos:
                            vars_bloque = [var for _, var in vars_horas[inicio_bloque:j]]
                            
                            # Crear variable de penalización
                            penalty_var = self.model.NewBoolVar(f"penalty_m{m_idx}_p{p_idx}_s{subgrup}_d{dia}_h{vars_horas[inicio_bloque][0]}_t{tamaño_bloque}")
                            
                            # La penalización se activa si todas las variables del bloque son 1
                            self.model.Add(sum(vars_bloque) >= tamaño_bloque).OnlyEnforceIf(penalty_var)
                            self.model.Add(sum(vars_bloque) < tamaño_bloque).OnlyEnforceIf(penalty_var.Not())
                            
                            # Añadir a la lista de penalizaciones
                            penalizaciones_particion.append(penalty_var)
                            
                            print(f"    Penalizando bloque de tamaño {tamaño_bloque} en día {dia}, hora {vars_horas[inicio_bloque][0]}")
                    
                    i = j

        # Añadir penalizaciones al objetivo si existen
        if penalizaciones_particion:
            # Crear variable de objetivo si no existe
            if not hasattr(self, 'objective_terms'):
                self.objective_terms = []
            
            # Añadir penalización por bloques de tamaño no permitido (peso alto)
            peso_particion = 1 # Ajustar según importancia relativa
            self.objective_terms.append(sum(penalizaciones_particion) * peso_particion)
            
            # Si hay una función objetivo, actualizarla
            if hasattr(self, 'objective_var'):
                self.model.Minimize(sum(self.objective_terms))
            else:
                # Si no hay otros términos en la función objetivo, crear uno nuevo
                self.objective_var = self.model.NewIntVar(0, 1000000, 'objective')
                self.model.Add(self.objective_var == sum(self.objective_terms))
                self.model.Minimize(self.objective_var)
            
            print(f"  Añadidas {len(penalizaciones_particion)} penalizaciones soft para particiones")
            """
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
                
                # Para cada posible asignación de este módulo
                for (m, p, d, h, a, s) in self.vars_assignacio:
                    if m == modul_idx:
                        # Si esta combinación día/hora no está en los slots permitidos, prohibirla
                        if (d, h) not in self.slots_projectes:
                            self.model.Add(self.vars_assignacio[(m, p, d, h, a, s)] == 0)
                            restricciones_aplicadas += 1
                
            print(f"  Se han aplicado {restricciones_aplicadas} restricciones para módulos de proyectos")








                
    def resoldre(self):
        """Resol el model i retorna la solució"""

        print("Iniciant la resolució del model...")



        
        # Crear solucionador
        solver = cp_model.CpSolver()
        
        # Configuració
        solver.parameters.optimize_with_core = True
        solver.parameters.linearization_level = 2
        solver.parameters.max_time_in_seconds = 600  # Límit de 10 minuts
        solver.parameters.log_search_progress = True
        solver.parameters.num_search_workers = 8  # Usar múltiples threads (ajustar según CPU)
        solver.parameters.relative_gap_limit = 0.05  # Límite de gap relativo
        
        # Resoldre el model
        start_time = time.time()
        status = solver.Solve(self.model)
        end_time = time.time()
        
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(f"Solució trobada en {end_time - start_time:.2f} segons")
            
            # Processar la solució
            solucio = {
                'horari': [[[[] for _ in range(self.hores_per_dia)] for _ in range(self.dies)] for _ in range(len(self.cursos))],
                'professors': [[[[] for _ in range(self.hores_per_dia)] for _ in range(self.dies)] for _ in range(len(self.professors))],
                'aules': [[[[] for _ in range(self.hores_per_dia)] for _ in range(self.dies)] for _ in range(len(self.aules))],
                'stats': {
                    'temps_resolucio': end_time - start_time,
                    'conflictes': solver.NumConflicts(),
                    'branques': solver.NumBranches()
                }
            }
            
            # Obtenir les assignacions
            for (m, p, d, h, a, s), var in self.vars_assignacio.items():
                if solver.Value(var) == 1:
                    modul = self.modul_per_index.get(m, {'nom': f'Mòdul {m}'})
                    professor = self.professor_per_index.get(p, {'nom': f'Professor {p}'})
                    curs_idx = modul.get('curs', -1)
                    
                    # Afegir a l'horari del curs
                    if 0 <= curs_idx < len(solucio['horari']):
                        solucio['horari'][curs_idx][d][h].append({
                            'modul': modul['nom'],
                            'modul_index': m,
                            'professor': professor['nom'],
                            'professor_index': p,
                            'aula': self.aula_per_index.get(a, {'nom': f'Aula {a}'})['nom'],
                            'aula_index': a,
                            'subgrup': s
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
            with open('solucio_horaris.json', 'w', encoding='utf-8') as f:
                json.dump(solucio, f, ensure_ascii=False, indent=2)
            
            print("Solució guardada a 'solucio_horaris.json'")
            return solucio
        else:
            print("No s'ha trobat cap solució")
            if status == cp_model.MODEL_INVALID:
                print("El model és invàlid")
            elif status == cp_model.INFEASIBLE:
                print("El model és infactible")
            elif status == cp_model.UNKNOWN:
                print("Estat desconegut")
            return None

    def executar(self):
        """Mètode principal per executar tot el procés"""
        self.crear_variables()
        self.afegir_restriccions()
        return self.resoldre()

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


def genera_json_solucio_compatible(solucio, dades_solver_path):
    """Genera un JSON amb exactament el mateix format que Solver.json"""
    import json
    import copy
    import time
    
    # Carregar les dades del solver originals
    with open(dades_solver_path, 'r', encoding='utf-8') as f:
        dades = json.load(f)

    # Carregar Buit.json per obtenir l'estructura exacta
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
                        "suport": False,
                        "simultani": False,
                        "profe": classe['professor_index']
                    }
                    
                    # Buscar un slot buit per col·locar l'assignació
                    if dia_idx < len(output["horari"][0]) and hora_idx < len(output["horari"][0][dia_idx]):
                        for slot_idx in range(len(output["horari"][0][dia_idx][hora_idx])):
                            if output["horari"][0][dia_idx][hora_idx][slot_idx] is None:
                                output["horari"][0][dia_idx][hora_idx][slot_idx] = assignacio
                                break
    
    
    # Guardar a l'arxiu
    with open('solucio_horaris_compatible.hor', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print("Solució guardada amb format exacte de Solver.json a 'solucio_horaris_compatible.json'")
    return output

if __name__ == "__main__":
    main()