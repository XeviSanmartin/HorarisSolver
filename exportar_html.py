import os
import random

def generar_color_pastel(seed):
    """Genera un color pastel basado en una semilla"""
    random.seed(seed)
    r = random.randint(180, 240)
    g = random.randint(180, 240)
    b = random.randint(180, 240)
    return f"rgb({r}, {g}, {b})"

def exportar_horaris_html(solucio, solver, output_path="horaris.html"):
    """Exporta los horarios a un archivo HTML"""
    
    # Diccionario para almacenar colores por módulo
    colores_modulos = {}
    
    # Generar colores únicos para cada módulo
    for modul_idx in solver.modul_per_index:
        colores_modulos[modul_idx] = generar_color_pastel(modul_idx)
    
    # Crear estructura HTML
    html = """
    <!DOCTYPE html>
    <html lang="ca">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Horaris Acadèmics</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .tabs {
                display: flex;
                margin-bottom: 20px;
                border-bottom: 1px solid #ccc;
            }
            .tab {
                padding: 10px 20px;
                cursor: pointer;
                background-color: #e0e0e0;
                border: 1px solid #ccc;
                border-bottom: none;
                margin-right: 5px;
                border-radius: 5px 5px 0 0;
            }
            .tab.active {
                background-color: #fff;
                border-bottom: 1px solid #fff;
                margin-bottom: -1px;
            }
            .tab-content {
                display: none;
            }
            .tab-content.active {
                display: block;
            }
            .horari-container {
                background-color: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                padding: 20px;
                margin-bottom: 30px;
            }
            h2 {
                color: #333;
                margin-top: 0;
                padding-bottom: 10px;
                border-bottom: 1px solid #eee;
            }
            table.horari {
                width: 100%;
                border-collapse: collapse;
                margin-top: 15px;
            }
            table.horari th, table.horari td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: center;
            }
            table.horari th {
                background-color: #f0f0f0;
                font-weight: bold;
            }
            .hora-col {
                width: 10%;
                background-color: #f0f0f0;
                font-weight: bold;
            }
            .classe {
                padding: 5px;
                border-radius: 4px;
                margin-bottom: 3px;
                font-size: 0.85em;
            }
            .modul {
                font-weight: bold;
            }
            .professor {
                font-style: italic;
            }
            .aula {
                font-size: 0.8em;
                color: #555;
            }
            .cell-content {
                min-height: 80px;
                vertical-align: top;
            }
        </style>
    </head>
    <body>
        <h1>Horaris Acadèmics</h1>
        
        <div class="tabs">
            <div class="tab active" onclick="openTab(event, 'cursos')">Horaris per Cursos</div>
            <div class="tab" onclick="openTab(event, 'professors')">Horaris per Professors</div>
            <div class="tab" onclick="openTab(event, 'aules')">Horaris per Aules</div>
        </div>
        
        <div id="cursos" class="tab-content active">
    """
    
    # Horarios por cursos
    for c_idx, curs_horari in enumerate(solucio['horari']):
        if c_idx in solver.curs_per_index:
            curs = solver.curs_per_index[c_idx]
            html += f"""
            <div class="horari-container">
                <h2>Horari: {curs['nom']}</h2>
                <table class="horari">
                    <tr>
                        <th>Hora</th>
                        <th>Dilluns</th>
                        <th>Dimarts</th>
                        <th>Dimecres</th>
                        <th>Dijous</th>
                        <th>Divendres</th>
                    </tr>
            """
            
            # Para cada hora del día
            for h in range(solver.hores_per_dia):
                hora = f"{h+8}:00"
                html += f"""
                    <tr>
                        <td class="hora-col">{hora}</td>
                """
                
                # Para cada día de la semana
                for d in range(solver.dies):
                    html += '<td class="cell-content">'
                    
                    # Clases en este slot
                    for classe in curs_horari[d][h]:
                        modul_idx = classe['modul_index']
                        color = colores_modulos.get(modul_idx, "#f0f0f0")
                        subgrup_text = f" (SG{classe['subgrup']})" if classe['subgrup'] < 4 else ""
                        
                        html += f"""
                        <div class="classe" style="background-color: {color}">
                            <div class="modul">{classe['modul']}{subgrup_text}</div>
                            <div class="professor">{classe['professor']}</div>
                            <div class="aula">{classe['aula']}</div>
                        </div>
                        """
                    
                    html += '</td>'
                
                html += """
                    </tr>
                """
            
            html += """
                </table>
            </div>
            """
    
    # Pestaña de profesores
    html += """
        </div>
        
        <div id="professors" class="tab-content">
    """
    
    # Horarios por profesores
    for p_idx, prof_horari in enumerate(solucio['professors']):
        if p_idx in solver.professor_per_index:
            professor = solver.professor_per_index[p_idx]
            html += f"""
            <div class="horari-container">
                <h2>Horari: {professor['nom']}</h2>
                <table class="horari">
                    <tr>
                        <th>Hora</th>
                        <th>Dilluns</th>
                        <th>Dimarts</th>
                        <th>Dimecres</th>
                        <th>Dijous</th>
                        <th>Divendres</th>
                    </tr>
            """
            
            # Para cada hora del día
            for h in range(solver.hores_per_dia):
                hora = f"{h+8}:00"
                html += f"""
                    <tr>
                        <td class="hora-col">{hora}</td>
                """
                
                # Para cada día de la semana
                for d in range(solver.dies):
                    html += '<td class="cell-content">'
                    
                    # Clases en este slot
                    for classe in prof_horari[d][h]:
                        modul_idx = classe['modul_index']
                        color = colores_modulos.get(modul_idx, "#f0f0f0")
                        subgrup_text = f" (SG{classe['subgrup']})" if classe['subgrup'] < 3 else ""
                        
                        html += f"""
                        <div class="classe" style="background-color: {color}">
                            <div class="modul">{classe['modul']}{subgrup_text}</div>
                            <div class="curs">{classe['curs']}</div>
                            <div class="aula">{classe['aula']}</div>
                        </div>
                        """
                    
                    html += '</td>'
                
                html += """
                    </tr>
                """
            
            html += """
                </table>
            </div>
            """
    
    # Pestaña de aulas
    html += """
        </div>
        
        <div id="aules" class="tab-content">
    """
    
    # Horarios por aulas
    for a_idx, aula_horari in enumerate(solucio['aules']):
        if a_idx in solver.aula_per_index:
            aula = solver.aula_per_index[a_idx]
            html += f"""
            <div class="horari-container">
                <h2>Horari: {aula['nom']}</h2>
                <table class="horari">
                    <tr>
                        <th>Hora</th>
                        <th>Dilluns</th>
                        <th>Dimarts</th>
                        <th>Dimecres</th>
                        <th>Dijous</th>
                        <th>Divendres</th>
                    </tr>
            """
            
            # Para cada hora del día
            for h in range(solver.hores_per_dia):
                hora = f"{h+8}:00"
                html += f"""
                    <tr>
                        <td class="hora-col">{hora}</td>
                """
                
                # Para cada día de la semana
                for d in range(solver.dies):
                    html += '<td class="cell-content">'
                    
                    # Clases en este slot
                    for classe in aula_horari[d][h]:
                        modul_idx = classe['modul_index']
                        color = colores_modulos.get(modul_idx, "#f0f0f0")
                        subgrup_text = f" (SG{classe['subgrup']})" if classe['subgrup'] < 3 else ""
                        
                        html += f"""
                        <div class="classe" style="background-color: {color}">
                            <div class="modul">{classe['modul']}{subgrup_text}</div>
                            <div class="professor">{classe['professor']}</div>
                            <div class="curs">{classe['curs']}</div>
                        </div>
                        """
                    
                    html += '</td>'
                
                html += """
                    </tr>
                """
            
            html += """
                </table>
            </div>
            """
    
    # Cerrar la estructura HTML
    html += """
        </div>
        
        <script>
            function openTab(evt, tabName) {
                // Ocultar todos los contenidos de pestañas
                var tabcontent = document.getElementsByClassName("tab-content");
                for (var i = 0; i < tabcontent.length; i++) {
                    tabcontent[i].className = tabcontent[i].className.replace(" active", "");
                }
                
                // Desactivar todas las pestañas
                var tabs = document.getElementsByClassName("tab");
                for (var i = 0; i < tabs.length; i++) {
                    tabs[i].className = tabs[i].className.replace(" active", "");
                }
                
                // Mostrar el contenido de la pestaña actual y activar la pestaña
                document.getElementById(tabName).className += " active";
                evt.currentTarget.className += " active";
            }
        </script>
    </body>
    </html>
    """
    
    # Guardar el archivo HTML
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Horaris exportats a {output_path}")
    return output_path