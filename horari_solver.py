import json
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict

@dataclass
class Professor:
    index: int
    actiu: bool
    nom: str
    nom_curt: str
    especialitat: int
    comentaris: str
    tutor_curs: int
    desiderata: List[Dict]
    moduls: List[Dict]
    hores7: bool
    DiesLliures: bool
    controlable: bool
    lliureRestriccions: bool = False



@dataclass
class Modul:
    index: int
    codi: str
    nom: str
    curs: int
    especialitat: int
    # Slots {dia, hora} on es pot impartir (buit = qualsevol hora)
    horari_disponible: List[Dict] = None
    # Índexs de les aules on es pot impartir, per espai/equipament (buit = qualsevol)
    aules_possibles: List[int] = None
    # Si les hores d'aquest mòdul compten com a "presència" del professor (per a
    # dies lliures i per a l'objectiu de matí/tarda). Reunions i similars: False.
    validaAssistencia: bool = True

    def __post_init__(self):
        if self.horari_disponible is None:
            self.horari_disponible = []
        if self.aules_possibles is None:
            self.aules_possibles = []



@dataclass
class Curs:
    index: int
    actiu: bool
    nom: str
    color: List[int]
    aula: int
    horari_disponible: List[Dict]
    # Si és cert (per defecte), les classes de grup sencer d'aquest grup només
    # poden anar a aules grans. Si és fals, el grup té pocs alumnes i hi cap a
    # qualsevol aula (també les petites), fins i tot sencer.
    necessita_aula_gran: bool = True


@dataclass
class Aula:
    index: int
    actiu: bool
    nom: str
    # Aula gran (per defecte): hi cap un grup sencer. Si és falsa, és una aula
    # petita i només hi caben grups sencers que no necessiten aula gran, o els
    # desdoblaments (subgrups 1 i 2). Substitueix l'antic `nomes_subgrups`.
    aula_gran: bool = True
    nomes_tardes: bool = False

@dataclass
class Especialitat:
    index: int
    actiu: bool
    codi: str
    nom: str

class HorariData:
    def __init__(self):
        self.professors: List[Professor] = []
        self.moduls: List[Modul] = []
        self.cursos: List[Curs] = []
        self.aules: List[Aula] = []
        self.especialitats: List[Especialitat] = []
        
        # Estructures derivades per al solver
        self.professor_per_index: Dict[int, Professor] = {}
        self.modul_per_index: Dict[int, Modul] = {}
        self.curs_per_index: Dict[int, Curs] = {}
        self.aula_per_index: Dict[int, Aula] = {}
        
        # Relacions per al solver
        self.moduls_per_professor: Dict[int, List[Dict]] = defaultdict(list)
        self.professors_per_modul: Dict[int, List[int]] = defaultdict(list)
        self.moduls_per_curs: Dict[int, List[int]] = defaultdict(list)
        self.subgrups_per_curs: Dict[int, Set[int]] = defaultdict(set)
        self.tutories_per_curs: Dict[int, int] = {}  # curs -> professor_index
        
        # Restriccions especials
        self.moduls_fol: Set[int] = set()
        self.moduls_angles: Set[int] = set()
        self.moduls_sostenibilitat: Set[int] = set()
        self.moduls_digitalizacio: Set[int] = set()
        self.moduls_suport: Set[int] = set()
        self.moduls_simultaneos: Set[int] = set()
        # Override explícit de l'editor "primera/última hora" per mòdul
        # (True/False); None = no definit → es dedueix de FOL/anglès
        self.primera_ultima_per_modul: Dict[int, object] = {}
        
        # Desiderata dels professors
        self.desiderata_per_professor: Dict[int, List[Dict]] = {}

        self.assignacions: List[Dict] = []
        self.moduls_coordinats_grups = []
        
        self.moduls_projectes: Set[int] = set()
        self.horaris_projectes: List[Dict] = []

        # Hores pre-assignades (camp "horari" del Solver.json) que el solver
        # ha de mantenir inamovibles, ja normalitzades i validades
        self.horari_fixat: List[Dict] = []
        self.advertiments_horari_fixat: List[str] = []


        # Hores disponibles (dilluns=0 a divendres=4, de 8h a 21h = hores 0 a 12)
        self.dies = 5  # dilluns a divendres
        self.hores_per_dia = 11  # de 8:00 a 21:00 (11 hores) — s'ajusta amb config.horesSetmana
        # Minut d'inici de cada franja (des de mitjanit), per calcular el descans
        # entre dies. None = el solver farà servir uns valors per defecte.
        self.hores_inici_min = None
        # Frontera matí/tarda i pesos dels objectius (es fixen a carrega_dades).
        self.hora_inici_tarda = 6
        self.objectius = {}

    @staticmethod
    def _minuts(hhmm):
        """'HH:MM' -> minuts des de mitjanit (0 si no es pot interpretar)."""
        try:
            h, m = str(hhmm).split(':')
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    def carrega_json(self, json_path: str, periode: int = 0):
        """Carrega les dades del fitxer JSON"""
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.carrega_dades(data, periode=periode)

    def carrega_dades(self, data: dict, periode: int = 0):
        """Carrega les dades a partir d'un diccionari amb format Solver.json

        Args:
            periode: període del camp "horari" del qual s'extreuen les hores
                pre-assignades (l'editor exporta 5 períodes; per defecte el 0).
        """
        # Hores de la setmana des de la config de l'editor: fixen quantes franges
        # té el dia i a quina hora comença cada una (per al descans entre dies).
        config_editor = data.get('config') or {}
        hs = config_editor.get('horesSetmana')
        if hs:
            parts = [t.strip() for t in str(hs).split(',') if t.strip()]
            if parts:
                self.hores_per_dia = len(parts)
                self.hores_inici_min = [self._minuts(t) for t in parts]
        # Frontera matí/tarda (índex de la primera franja de tarda) i pesos dels
        # objectius configurables (vegeu Solver.py, funció objectiu).
        self.hora_inici_tarda = config_editor.get('horaIniciTarda', 6)
        self.objectius = config_editor.get('objectius') or {}

        # Carrega professors
        for prof_data in data.get('professors', []):
            if prof_data.get('actiu', True):
                professor = Professor(
                    index=prof_data['index'],
                    actiu=prof_data['actiu'],
                    nom=prof_data['nom'],
                    nom_curt=prof_data['nomCurt'],
                    especialitat=prof_data['especialitat'],
                    comentaris=prof_data.get('comentaris', ''),
                    tutor_curs=prof_data.get('tutorCurs', -1),
                    desiderata=prof_data.get('desiderata', []),
                    moduls=prof_data.get('moduls', []),
                    hores7=prof_data.get('7hores', False),
                    DiesLliures=prof_data.get('DiesLliures', False),
                    controlable=prof_data.get('controlable', False),
                    lliureRestriccions=prof_data.get('lliureRestriccions', False)
                )
                self.professors.append(professor)
                self.professor_per_index[professor.index] = professor
                
                # Desiderata del professor
                self.desiderata_per_professor[professor.index] = professor.desiderata
                
                # Relació professor-mòduls
                for modul_assign in professor.moduls:
                    modul_index = modul_assign['index']
                    
                    # Crea una còpia del mòdul per evitar modificar l'original
                    modul_copy = modul_assign.copy()
                    
                   
                    
                    self.moduls_per_professor[professor.index].append(modul_copy)
                    self.professors_per_modul[modul_index].append(professor.index)
                    
                    self.assignacions.append({
                        'professor': professor.index,
                        'modul': modul_index,
                        'hores': modul_assign.get('hores', 0),
                        'aula': modul_copy.get('aula', -1),  # Utilitza l'aula modificada
                        'subgrup': modul_assign.get('subgrup', 3)  # 3 = grup sencer
                    })
                    
                    # Detectar mòduls amb suport
                    if modul_assign.get('suport', False):
                        self.moduls_suport.add(modul_index)
                    
                    # Detectar mòduls simultanis
                    if modul_assign.get('simultani', False):
                        self.moduls_simultaneos.add(modul_index)
                
                # Detectar tutories
                if professor.tutor_curs != -1:
                    self.tutories_per_curs[professor.tutor_curs] = professor.index
        
        # Carrega mòduls
        for modul_data in data.get('moduls', []):
            modul = Modul(
                index=modul_data['index'],
                codi=modul_data.get('codi', ''),
                nom=modul_data.get('nom', ''),
                curs=modul_data.get('curs', -1),
                especialitat=modul_data.get('especialitat', -1),
                horari_disponible=modul_data.get('horari_disponible', []) or [],
                aules_possibles=modul_data.get('aules_possibles', []) or [],
                validaAssistencia=modul_data.get('validaAssistencia', True)
            )
            self.primera_ultima_per_modul[modul.index] = modul_data.get('primera_ultima_hora')
            self.moduls.append(modul)
            self.modul_per_index[modul.index] = modul
            
            # Identificar mòduls especials per nom/codi
            nom_lower = modul.nom.lower()
            codi_lower = modul.codi.lower()
            
            if 'tutoria' in nom_lower or 'tutoria' in codi_lower:
                # Les tutories ja s'han processat amb els professors
                pass
            elif ('anglès' in nom_lower or 'an-' in codi_lower or 'angles' in nom_lower or 
                  modul.especialitat == 3):  
                self.moduls_angles.add(modul.index)
            elif 'sostenibilitat' in nom_lower or 'so-' in codi_lower:
                self.moduls_sostenibilitat.add(modul.index)
            elif 'digitalització' in nom_lower or 'di-' in codi_lower or 'digitalitzacio' in nom_lower:
                self.moduls_digitalizacio.add(modul.index)
            elif ('fol' in nom_lower or 'formació i orientació' in nom_lower or 
                  modul.especialitat == 2):  
                self.moduls_fol.add(modul.index)
            
            # Afegir mòduls al curs corresponent
            if modul.curs != -1:
                self.moduls_per_curs[modul.curs].append(modul.index)
        
        # Carrega cursos
        for curs_data in data.get('cursos', []):
            if curs_data.get('actiu', True):
                curs = Curs(
                    index=curs_data['index'],
                    actiu=curs_data['actiu'],
                    nom=curs_data['nom'],
                    color=curs_data.get('color', []),
                    aula=curs_data.get('aula', -1),
                    horari_disponible=curs_data.get('horari_disponible', []),
                    necessita_aula_gran=curs_data.get('necessita_aula_gran', True)
                )
                self.cursos.append(curs)
                self.curs_per_index[curs.index] = curs
        
        # Carrega aules
        for aula_data in data.get('aules', []):
            if aula_data.get('actiu', True):
                aula = Aula(
                    index=aula_data['index'],
                    actiu=aula_data['actiu'],
                    nom=aula_data['nom'],
                    # `aula_gran` és el camp nou; per compatibilitat amb dades
                    # antigues es dedueix de l'antic `nomes_subgrups` (una aula
                    # "només subgrups" era, de fet, una aula petita → no gran).
                    aula_gran=aula_data.get(
                        'aula_gran', not aula_data.get('nomes_subgrups', False)),
                    nomes_tardes=aula_data.get('nomes_tardes', False)
                )
                self.aules.append(aula)
                self.aula_per_index[aula.index] = aula
        
        # Carrega especialitats
        for esp_data in data.get('especialitats', []):
            if esp_data.get('actiu', True):
                especialitat = Especialitat(
                    index=esp_data['index'],
                    actiu=esp_data['actiu'],
                    codi=esp_data['codi'],
                    nom=esp_data['nom']
                )
                self.especialitats.append(especialitat)

        if 'moduls_coordinats' in data:
            moduls_coord = data['moduls_coordinats']
            if 'grups' in moduls_coord:
                self.moduls_coordinats_grups = moduls_coord['grups']
                print(f"Cargados {len(self.moduls_coordinats_grups)} grupos de módulos coordinados")

        if 'projectes' in data:
            self.moduls_projectes = set(data['projectes'])
            print(f"Cargados {len(self.moduls_projectes)} módulos de proyectos con restricciones de horario")

        # Cargar horarios permitidos para proyectos
        if 'horaris_projectes' in data:
            self.horaris_projectes = data['horaris_projectes']
            print(f"Cargadas {len(self.horaris_projectes)} restricciones horarias para proyectos")
        
        # Analitzar subgrups
        self._analitza_subgrups()

        # Carregar hores pre-assignades (cal fer-ho al final: la validació
        # necessita professors, mòduls, cursos i aules ja carregats)
        self._carrega_horari_fixat(data.get('horari') or [], periode)

        print(f"Carregades dades: {len(self.professors)} professors, {len(self.moduls)} mòduls, {len(self.cursos)} cursos, {len(self.aules)} aules")
    
    def _analitza_subgrups(self):
        """Analitza els subgrups per cada curs"""
        for professor in self.professors:
            for modul_assign in professor.moduls:
                curs_index = modul_assign.get('subgrup', -1)
                if curs_index != -1:
                    # Trobar el curs del mòdul
                    modul_index = modul_assign['index']
                    if modul_index in self.modul_per_index:
                        curs_modul = self.modul_per_index[modul_index].curs
                        if curs_modul != -1:
                            self.subgrups_per_curs[curs_modul].add(modul_assign['subgrup'])
    
    def _carrega_horari_fixat(self, horari: list, periode: int):
        """Extreu i valida les hores pre-assignades del camp "horari".

        Accepta els dos formats coneguts:
        - Export de l'editor: horari[periode][dia][hora] -> llista de cel·les
          (una posició per professor, amb null als buits).
        - Format pla: horari[dia][hora] -> cel·la (dict o null).

        Cada cel·la és {modul, curs, aula, subgrup, suport, simultani, profe}.
        Les cel·les vàlides es normalitzen a self.horari_fixat; les invàlides
        es descarten amb un advertiment a self.advertiments_horari_fixat.
        """
        self.horari_fixat = []
        self.advertiments_horari_fixat = []
        if not horari:
            return
        avis = self.advertiments_horari_fixat.append

        # Detecció de format: en el format de l'editor les cel·les (llistes)
        # apareixen al tercer nivell; en el pla, els dicts al segon
        format_editor = True
        trobat = False
        for nivell1 in horari:
            if not isinstance(nivell1, list):
                continue
            for nivell2 in nivell1:
                if isinstance(nivell2, dict):
                    format_editor = False
                    trobat = True
                elif isinstance(nivell2, list):
                    trobat = True
                if trobat:
                    break
            if trobat:
                break

        if format_editor:
            if periode >= len(horari):
                avis(f"Horari fixat: el període {periode} no existeix a l'horari "
                     f"(només n'hi ha {len(horari)}); no es fixa cap hora")
                return
            matriu = horari[periode]
        else:
            matriu = horari

        for dia_idx, dia in enumerate(matriu):
            if not isinstance(dia, list):
                continue
            for hora_idx, cella in enumerate(dia):
                # Format editor: cella és una llista indexada per professor (la
                # POSICIÓ és l'índex del professor; les cel·les no porten 'profe').
                # Format pla: cella és un únic dict que sí que porta 'profe'.
                if isinstance(cella, list):
                    for profe_pos, slot in enumerate(cella):
                        if isinstance(slot, dict):
                            self._afegeix_hora_fixada(dia_idx, hora_idx, slot, profe_defecte=profe_pos)
                elif isinstance(cella, dict):
                    self._afegeix_hora_fixada(dia_idx, hora_idx, cella)

        # Un professor no pot tenir dues hores fixades al mateix slot
        vists = set()
        depurats = []
        for fix in self.horari_fixat:
            clau = (fix['professor'], fix['dia'], fix['hora'])
            if clau in vists:
                avis(f"Horari fixat (dia {fix['dia']}, hora {fix['hora']}): el professor "
                     f"{fix['professor']} ja té una hora fixada en aquest slot; es descarta la repetida")
                continue
            vists.add(clau)
            depurats.append(fix)
        self.horari_fixat = depurats

        # No es poden fixar més hores que les que té l'assignació
        recompte = defaultdict(int)
        for fix in self.horari_fixat:
            recompte[(fix['professor'], fix['modul'], fix['subgrup'])] += 1
        for (p_idx, m_idx, subgrup), n in recompte.items():
            prof = self.professor_per_index[p_idx]
            hores = sum(ma.get('hores', 0) for ma in prof.moduls
                        if ma.get('index') == m_idx and ma.get('subgrup', 3) == subgrup)
            if n > hores:
                avis(f"Horari fixat: es fixen {n} hores del mòdul {m_idx} per a {prof.nom} "
                     f"(subgrup {subgrup}) però l'assignació només en té {hores}: "
                     f"l'horari serà infactible")

        if self.horari_fixat:
            print(f"Carregades {len(self.horari_fixat)} hores pre-assignades (període {periode})")

    def _afegeix_hora_fixada(self, dia: int, hora: int, cella: Dict, profe_defecte: int = -1):
        """Valida una cel·la pre-assignada i, si és coherent, l'afegeix a horari_fixat.

        `profe_defecte` és l'índex de professor per posició (format editor, on la
        cel·la no porta 'profe'); el camp 'profe'/'professor' de la cel·la mana."""
        avis = self.advertiments_horari_fixat.append
        p_idx = cella.get('profe', cella.get('professor', profe_defecte))
        m_idx = cella.get('modul', -1)
        subgrup = cella.get('subgrup', 3)
        aula = cella.get('aula', -1)
        lloc = f"dia {dia}, hora {hora}"

        if dia >= self.dies or hora >= self.hores_per_dia:
            avis(f"Horari fixat ({lloc}): fora del rang del solver "
                 f"({self.dies} dies x {self.hores_per_dia} hores); es descarta")
            return

        prof = self.professor_per_index.get(p_idx)
        if prof is None:
            avis(f"Horari fixat ({lloc}): el professor {p_idx} no existeix o no està actiu; es descarta")
            return

        if m_idx not in self.modul_per_index:
            avis(f"Horari fixat ({lloc}): el mòdul {m_idx} no existeix; es descarta")
            return

        assignacions = [ma for ma in prof.moduls
                        if ma.get('index') == m_idx and ma.get('subgrup', 3) == subgrup]
        if not assignacions:
            avis(f"Horari fixat ({lloc}): {prof.nom} no té assignat el mòdul {m_idx} "
                 f"amb subgrup {subgrup}; es descarta")
            return

        # El curs ha de tenir el slot dins del seu horari disponible
        modul = self.modul_per_index[m_idx]
        curs = self.curs_per_index.get(modul.curs)
        if curs is not None and curs.horari_disponible:
            disponibles = {(s.get('dia'), s.get('hora')) for s in curs.horari_disponible}
            if (dia, hora) not in disponibles:
                avis(f"Horari fixat ({lloc}): el curs {curs.nom} no té aquest slot "
                     f"al seu horari disponible; es descarta")
                return

        # El mòdul també ha de poder anar en aquest slot
        if modul.horari_disponible:
            slots_modul = {(s.get('dia'), s.get('hora')) for s in modul.horari_disponible}
            if (dia, hora) not in slots_modul:
                avis(f"Horari fixat ({lloc}): el mòdul {modul.nom} no té aquest slot "
                     f"al seu horari disponible; es descarta")
                return

        # Coherència amb l'aula preferida de l'assignació: el solver només crea
        # variables per a l'aula preferida quan n'hi ha una
        aula_preferida = assignacions[0].get('aula', -1)
        if aula_preferida != -1 and aula not in (-1, aula_preferida):
            avis(f"Horari fixat ({lloc}): l'aula {aula} no coincideix amb l'aula preferida "
                 f"{aula_preferida} de l'assignació; es fixa amb l'aula preferida")
        if aula_preferida != -1:
            aula = aula_preferida

        # Restriccions de l'aula (el solver no crea variables per combinacions invàlides)
        if aula != -1:
            aula_obj = self.aula_per_index.get(aula)
            if aula_obj is None:
                avis(f"Horari fixat ({lloc}): l'aula {aula} no existeix o no està activa; "
                     f"es fixa l'hora sense aula concreta")
                aula = -1
            elif ((not aula_obj.aula_gran and subgrup == 3
                   and (curs is None or curs.necessita_aula_gran))
                  or (aula_obj.nomes_tardes and hora < 6)):
                if aula == aula_preferida:
                    avis(f"Horari fixat ({lloc}): l'aula preferida {aula} no és compatible "
                         f"amb aquest slot (aula petita per a grup sencer o només tardes); es descarta")
                    return
                avis(f"Horari fixat ({lloc}): l'aula {aula} no és compatible amb aquest slot; "
                     f"es fixa l'hora sense aula concreta")
                aula = -1

        # L'aula fixada ha de ser dins del conjunt d'aules del mòdul (si en té)
        if aula != -1 and modul.aules_possibles and aula not in modul.aules_possibles:
            avis(f"Horari fixat ({lloc}): l'aula {aula} no és al conjunt d'aules del mòdul "
                 f"{modul.nom}; es fixa l'hora amb les aules del mòdul")
            aula = -1

        self.horari_fixat.append({
            'professor': p_idx,
            'modul': m_idx,
            'subgrup': subgrup,
            'dia': dia,
            'hora': hora,
            'aula': aula,
        })

    def get_restriccions_professor(self, professor_index: int) -> Dict:
        """Obté les restriccions d'un professor"""
        if professor_index not in self.desiderata_per_professor:
            return {}
        
        restriccions = {
            'no_disponible': [],  # (dia, hora) on no vol treballar (tipus 2)
            'prefereix_no': [],   # (dia, hora) on prefereix no treballar (tipus 1)
        }
        
        for desideratum in self.desiderata_per_professor[professor_index]:
            dia = desideratum.get('dia', -1)
            hora = desideratum.get('hora', -1)
            tipus = desideratum.get('tipus', 0)
            
            if dia != -1 and hora != -1:
                if tipus == 2:  # No disponible
                    restriccions['no_disponible'].append((dia, hora))
                elif tipus == 1:  # Prefereix que no
                    restriccions['prefereix_no'].append((dia, hora))
        
        return restriccions
    
    def get_moduls_agrupables(self) -> List[Tuple[int, int]]:
        """Retorna parelles de mòduls que es poden agrupar"""
        agrupables = []
        
        # Sostenibilitat es pot agrupar amb altres mòduls opcionals del mateix curs
        for modul_sost in self.moduls_sostenibilitat:
            if modul_sost in self.modul_per_index:
                curs_sost = self.modul_per_index[modul_sost].curs
                for modul_index in self.moduls_per_curs[curs_sost]:
                    if (modul_index != modul_sost and 
                        modul_index in self.modul_per_index):
                        modul = self.modul_per_index[modul_index]
                        # Buscar mòduls opcionals o que tinguin "opt" al nom/codi
                        if ('optatiu' in modul.nom.lower() or 'opt' in modul.codi.lower() or
                            'mòdul optatiu' in modul.nom.lower()):
                            agrupables.append((modul_sost, modul_index))
        
        # Digitalització també es pot agrupar amb altres mòduls
        for modul_digit in self.moduls_digitalizacio:
            if modul_digit in self.modul_per_index:
                curs_digit = self.modul_per_index[modul_digit].curs
                for modul_index in self.moduls_per_curs[curs_digit]:
                    if (modul_index != modul_digit and 
                        modul_index in self.modul_per_index and
                        modul_index not in self.moduls_sostenibilitat):  # Evitar duplicats
                        modul = self.modul_per_index[modul_index]
                        # Buscar mòduls que es puguin combinar (opcionals, IPO, etc.)
                        if ('optatiu' in modul.nom.lower() or 'opt' in modul.codi.lower() or
                            'ipo' in modul.nom.lower() or 'ipo' in modul.codi.lower()):
                            agrupables.append((modul_digit, modul_index))
        
        return agrupables
    
    def es_tutoria(self, modul_index: int) -> bool:
        """Comprova si un mòdul és una tutoria"""
        if modul_index in self.modul_per_index:
            return 'tutoria' in self.modul_per_index[modul_index].nom.lower()
        return False
    
    def exporta_dades_processades(self, output_path: str):
        """Exporta les dades processades a un fitxer JSON per al solver"""
        dades_solver = self.genera_dades_processades()

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dades_solver, f, ensure_ascii=False, indent=2)

        print(f"Dades processades exportades a: {output_path}")

    def genera_dades_processades(self) -> dict:
        """Genera el diccionari de dades processades per al solver"""
        dades_solver = {
            'professors': [
                {
                    'index': prof.index,
                    'nom': prof.nom,
                    'nom_curt': prof.nom_curt,
                    'especialitat': prof.especialitat,
                    'tutor_curs': prof.tutor_curs,
                    '7hores': prof.hores7,
                    'DiesLliures': prof.DiesLliures,
                    'moduls': self.moduls_per_professor[prof.index],
                    'restriccions': self.get_restriccions_professor(prof.index),
                    'controlable': prof.controlable,
                    'lliureRestriccions': prof.lliureRestriccions
                }
                for prof in self.professors
            ],
            'moduls': [
                {
                    'index': modul.index,
                    'codi': modul.codi,
                    'nom': modul.nom,
                    'curs': modul.curs,
                    'especialitat': modul.especialitat,
                    'es_tutoria': self.es_tutoria(modul.index),
                    'es_fol': modul.index in self.moduls_fol,
                    'es_angles': modul.index in self.moduls_angles,
                    'primera_ultima_hora': self.primera_ultima_per_modul.get(modul.index),
                    'es_sostenibilitat': modul.index in self.moduls_sostenibilitat,
                    'es_digitalizacio': modul.index in self.moduls_digitalizacio,
                    'professors_assignats': self.professors_per_modul[modul.index],
                    'horari_disponible': modul.horari_disponible,
                    'aules_possibles': modul.aules_possibles,
                    'validaAssistencia': modul.validaAssistencia
                }
                for modul in self.moduls
            ],
            'cursos': [
                {
                    'index': curs.index,
                    'nom': curs.nom,
                    'aula_principal': curs.aula,
                    'moduls': self.moduls_per_curs[curs.index],
                    'subgrups': list(self.subgrups_per_curs[curs.index]),
                    'tutor_professor': self.tutories_per_curs.get(curs.index, -1),
                    'horari_disponible': curs.horari_disponible if curs.horari_disponible else [],
                    'necessita_aula_gran': curs.necessita_aula_gran
                }
                for curs in self.cursos
            ],
            'aules': [
                {
                    'index': aula.index,
                    'nom': aula.nom,
                    'actiu': aula.actiu,
                    'aula_gran': aula.aula_gran,
                    'nomes_tardes': aula.nomes_tardes
                }
                for aula in self.aules
            ],
            'especialitats': [
                {
                    'index': esp.index,
                    'codi': esp.codi,
                    'nom': esp.nom
                }
                for esp in self.especialitats
            ],
            'agrupacions': self.get_moduls_agrupables(),
            'horari_fixat': self.horari_fixat,
            'configuracio': {
                'dies_setmana': self.dies,
                'hores_per_dia': self.hores_per_dia,
                'hores_inici_min': self.hores_inici_min,
                'hora_inici_tarda': self.hora_inici_tarda,
                'objectius': self.objectius,
                'moduls_especials': {
                    'fol': list(self.moduls_fol),
                    'angles': list(self.moduls_angles),
                    'sostenibilitat': list(self.moduls_sostenibilitat),
                    'digitalizacio': list(self.moduls_digitalizacio),
                    'suport': list(self.moduls_suport),
                    'simultaneos': list(self.moduls_simultaneos),
                    'moduls_coordinats': self.moduls_coordinats_grups,
                    'projectes': list(self.moduls_projectes)
                },
                'horaris_projectes': self.horaris_projectes
            }
        }

        return dades_solver
    
    def valida_dades(self) -> List[str]:
        """Valida les dades carregades i retorna una llista d'errors/advertiments"""
        errors = []
        
        # Validar que tots els professors amb tutoria tenen un curs vàlid
        for professor in self.professors:
            if professor.tutor_curs != -1 and professor.tutor_curs not in self.curs_per_index:
                errors.append(f"Professor {professor.nom} té tutoria de curs inexistent: {professor.tutor_curs}")
        
        # Validar que tots els mòduls tenen professors assignats
        for modul in self.moduls:
            if modul.index not in self.professors_per_modul or not self.professors_per_modul[modul.index]:
                errors.append(f"Mòdul {modul.nom} no té cap professor assignat")
        
        # Validar que FOL i Anglès tenen almenys un mòdul per coordinar
        if not self.moduls_fol:
            errors.append("No s'han detectat mòduls de FOL")
        
        if not self.moduls_angles:
            errors.append("No s'han detectat mòduls d'Anglès")
        
        # Validar que cada curs té una tutoria
        cursos_sense_tutoria = []
        for curs in self.cursos:
            if curs.index not in self.tutories_per_curs:
                cursos_sense_tutoria.append(curs.nom)
        
        if cursos_sense_tutoria:
            errors.append(f"Cursos sense tutoria: {', '.join(cursos_sense_tutoria)}")

        # Validar les restriccions d'horari i aules dels mòduls
        errors.extend(self._valida_restriccions_moduls())

        # Advertiments de la càrrega de l'horari pre-assignat
        errors.extend(self.advertiments_horari_fixat)

        return errors

    def _valida_restriccions_moduls(self) -> List[str]:
        """Valida horari_disponible i aules_possibles dels mòduls"""
        errors = []

        for modul in self.moduls:
            # Aules inexistents o inactives al conjunt del mòdul
            aules_dolentes = [a for a in modul.aules_possibles if a not in self.aula_per_index]
            if aules_dolentes:
                errors.append(f"Mòdul {modul.nom}: les aules {aules_dolentes} del seu conjunt "
                              f"d'aules no existeixen o no estan actives")

            if not modul.horari_disponible:
                continue

            slots_modul = {(s.get('dia'), s.get('hora')) for s in modul.horari_disponible}

            # Intersecció amb l'horari disponible del curs
            curs = self.curs_per_index.get(modul.curs)
            if curs is not None and curs.horari_disponible:
                slots_curs = {(s.get('dia'), s.get('hora')) for s in curs.horari_disponible}
                slots_modul = slots_modul & slots_curs
                if not slots_modul:
                    errors.append(f"Mòdul {modul.nom}: cap slot del seu horari disponible "
                                  f"coincideix amb l'horari del curs {curs.nom}: serà infactible")
                    continue

            # Prou slots per a les hores de cada assignació
            for p_idx in self.professors_per_modul.get(modul.index, []):
                prof = self.professor_per_index.get(p_idx)
                if prof is None:
                    continue
                for ma in prof.moduls:
                    if ma.get('index') == modul.index and ma.get('hores', 0) > len(slots_modul):
                        errors.append(f"Mòdul {modul.nom}: {prof.nom} hi té {ma['hores']} hores "
                                      f"però l'horari disponible del mòdul només té "
                                      f"{len(slots_modul)} slots: serà infactible")

        # Aula preferida de l'assignació fora del conjunt d'aules del mòdul
        for prof in self.professors:
            for ma in prof.moduls:
                modul = self.modul_per_index.get(ma.get('index'))
                if (modul is not None and modul.aules_possibles and
                        ma.get('aula', -1) != -1 and ma['aula'] not in modul.aules_possibles):
                    errors.append(f"Mòdul {modul.nom}: l'aula preferida {ma['aula']} de "
                                  f"l'assignació de {prof.nom} no és al conjunt d'aules del mòdul; "
                                  f"es faran servir les aules del mòdul {modul.aules_possibles}")

        return errors

    def get_estadistiques(self) -> Dict:
        """Retorna estadístiques de les dades carregades"""
        stats = {
            'total_professors': len(self.professors),
            'total_moduls': len(self.moduls),
            'total_cursos': len(self.cursos),
            'total_aules': len(self.aules),
            'moduls_fol': len(self.moduls_fol),
            'moduls_angles': len(self.moduls_angles),
            'moduls_sostenibilitat': len(self.moduls_sostenibilitat),
            'moduls_digitalizacio': len(self.moduls_digitalizacio),
            'moduls_suport': len(self.moduls_suport),
            'moduls_simultaneos': len(self.moduls_simultaneos),
            'tutories': len(self.tutories_per_curs),
            'subgrups_per_curs': dict(self.subgrups_per_curs),
            'hores_fixades': len(self.horari_fixat)
        }
        return stats

def main():
    """Funció principal per provar la càrrega de dades"""
    horari_data = HorariData()
    
    try:
        horari_data.carrega_json('BuitRestriccions.json')
        
        # Mostrar estadístiques
        stats = horari_data.get_estadistiques()
        print("\n=== ESTADÍSTIQUES ===")
        for key, value in stats.items():
            print(f"{key}: {value}")
        
        # Mostrar alguns exemples de restriccions
        print("\n=== EXEMPLES DE RESTRICCIONS DE PROFESSORS ===")
        for i, professor in enumerate(horari_data.professors[:3]):  # Primer 3 professors
            restriccions = horari_data.get_restriccions_professor(professor.index)
            print(f"\nProfessor {professor.nom} ({professor.nom_curt}):")
            print(f"  No disponible: {restriccions['no_disponible']}")
            print(f"  Prefereix no: {restriccions['prefereix_no']}")
            
        # Mostrar mòduls agrupables
        agrupables = horari_data.get_moduls_agrupables()
        print(f"\n=== MÒDULS AGRUPABLES ===")
        print(f"Parelles agrupables: {len(agrupables)}")
        for modul1, modul2 in agrupables[:5]:  # Primer 5 parelles
            nom1 = horari_data.modul_per_index[modul1].nom if modul1 in horari_data.modul_per_index else "Desconegut"
            nom2 = horari_data.modul_per_index[modul2].nom if modul2 in horari_data.modul_per_index else "Desconegut"
            print(f"  {nom1} <-> {nom2}")
        
        # Mostrar detalls dels mòduls especials detectats
        print(f"\n=== MÒDULS ESPECIALS DETECTATS ===")
        print("FOL (IPO):")
        for modul_idx in horari_data.moduls_fol:
            if modul_idx in horari_data.modul_per_index:
                modul = horari_data.modul_per_index[modul_idx]
                curs_nom = horari_data.curs_per_index[modul.curs].nom if modul.curs in horari_data.curs_per_index else f"Curs {modul.curs}"
                print(f"  - {modul.nom} (codi: {modul.codi}, curs: {curs_nom})")
        
        print("Anglès:")
        for modul_idx in horari_data.moduls_angles:
            if modul_idx in horari_data.modul_per_index:
                modul = horari_data.modul_per_index[modul_idx]
                curs_nom = horari_data.curs_per_index[modul.curs].nom if modul.curs in horari_data.curs_per_index else f"Curs {modul.curs}"
                print(f"  - {modul.nom} (codi: {modul.codi}, curs: {curs_nom})")
        
        print("Sostenibilitat:")
        for modul_idx in horari_data.moduls_sostenibilitat:
            if modul_idx in horari_data.modul_per_index:
                modul = horari_data.modul_per_index[modul_idx]
                curs_nom = horari_data.curs_per_index[modul.curs].nom if modul.curs in horari_data.curs_per_index else f"Curs {modul.curs}"
                print(f"  - {modul.nom} (codi: {modul.codi}, curs: {curs_nom})")
        
        print("Digitalització:")
        for modul_idx in horari_data.moduls_digitalizacio:
            if modul_idx in horari_data.modul_per_index:
                modul = horari_data.modul_per_index[modul_idx]
                curs_nom = horari_data.curs_per_index[modul.curs].nom if modul.curs in horari_data.curs_per_index else f"Curs {modul.curs}"
                print(f"  - {modul.nom} (codi: {modul.codi}, curs: {curs_nom})")
                
        # Mostrar mòduls per curs
        print(f"\n=== MÒDULS PER CURS ===")
        for curs in horari_data.cursos:
            print(f"\n{curs.nom} (índex {curs.index}):")
            moduls_curs = horari_data.moduls_per_curs[curs.index]
            for modul_idx in moduls_curs:
                if modul_idx in horari_data.modul_per_index:
                    modul = horari_data.modul_per_index[modul_idx]
                    professors_assignats = horari_data.professors_per_modul.get(modul_idx, [])
                    prof_noms = [horari_data.professor_per_index[p].nom_curt for p in professors_assignats if p in horari_data.professor_per_index]
                    print(f"  - {modul.nom} (professors: {', '.join(prof_noms) if prof_noms else 'Cap'})")
            
        # Mostrar tutories detectades
        print(f"\n=== TUTORIES DETECTADES ===")
        for curs_idx, professor_idx in horari_data.tutories_per_curs.items():
            curs_nom = horari_data.curs_per_index[curs_idx].nom if curs_idx in horari_data.curs_per_index else f"Curs {curs_idx}"
            professor_nom = horari_data.professor_per_index[professor_idx].nom if professor_idx in horari_data.professor_per_index else f"Professor {professor_idx}"
            print(f"  {curs_nom}: {professor_nom}")
        
        # Validar dades
        print(f"\n=== VALIDACIÓ DE DADES ===")
        errors = horari_data.valida_dades()
        if errors:
            print("Errors/Advertiments detectats:")
            for error in errors:
                print(f"  - {error}")
        else:
            print("✓ Totes les validacions han passat correctament")
        
        # Exportar dades processades
        horari_data.exporta_dades_processades('dades_solver_processades.json')
        print("\n✓ Processament completat!")
            
    except Exception as e:
        print(f"Error carregant dades: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
