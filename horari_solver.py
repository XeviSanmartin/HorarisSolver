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



@dataclass
class Modul:
    index: int
    codi: str
    nom: str
    curs: int
    especialitat: int
    

@dataclass
class Curs:
    index: int
    actiu: bool
    nom: str
    color: List[int]
    aula: int
    horari_disponible: List[Dict] 


@dataclass
class Aula:
    index: int
    actiu: bool
    nom: str
    nomes_subgrups: bool = False
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
        
        # Desiderata dels professors
        self.desiderata_per_professor: Dict[int, List[Dict]] = {}

        self.assignacions: List[Dict] = []
        self.moduls_coordinats_grups = []
        
        self.moduls_projectes: Set[int] = set()
        self.horaris_projectes: List[Dict] = []


        # Hores disponibles (dilluns=0 a divendres=4, de 8h a 21h = hores 0 a 12)
        self.dies = 5  # dilluns a divendres
        self.hores_per_dia = 11  # de 8:00 a 21:00 (11 hores)

    def carrega_json(self, json_path: str):
        """Carrega les dades del fitxer JSON"""
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
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
                    controlable=prof_data.get('controlable', False)
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
                especialitat=modul_data.get('especialitat', -1)
            )
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
                    horari_disponible=curs_data.get('horari_disponible', [])
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
                    nomes_subgrups=aula_data.get('nomes_subgrups', False),
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
                    'controlable': prof.controlable
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
                    'es_sostenibilitat': modul.index in self.moduls_sostenibilitat,
                    'es_digitalizacio': modul.index in self.moduls_digitalizacio,
                    'professors_assignats': self.professors_per_modul[modul.index]
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
                    'horari_disponible': curs.horari_disponible if curs.horari_disponible else []
                }
                for curs in self.cursos
            ],
            'aules': [
                {
                    'index': aula.index,
                    'nom': aula.nom,
                    'actiu': aula.actiu,
                    'nomes_subgrups': aula.nomes_subgrups,
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
            'configuracio': {
                'dies_setmana': self.dies,
                'hores_per_dia': self.hores_per_dia,
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
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dades_solver, f, ensure_ascii=False, indent=2)
        
        print(f"Dades processades exportades a: {output_path}")
    
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
            'subgrups_per_curs': dict(self.subgrups_per_curs)
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
