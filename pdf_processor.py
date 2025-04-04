import pdfplumber
import pandas as pd
import re
import json
import os
import uuid
from conexionbd import crear_o_actualizar_usuario_por_pdf,guardar_plan_y_asignaturas,get_db_connection

UPLOADS_FOLDER = 'uploads'

def procesar_pdf(pdf_path, excel_path):
    datos_personales = {
        "Código de Matrícula": "",
        "Facultad": "",
        "Escuela": "",
        "Especialidad": "",
        "Plan": ""
    }
    ciclos = []
    filas_tablas = []

    patron_codigo = re.compile(r"Código de Matrícula\s*:\s*(\d+)")
    patron_facultad = re.compile(r"Facultad\s*:\s*(\d+)")
    patron_escuela = re.compile(r"Escuela\s*:\s*(\d+)\s*-\s*(.+)")
    patron_especialidad = re.compile(r"Especialidad\s*:\s*(\d+)")
    patron_plan = re.compile(r"Plan\s*:\s*(\d+)")
    patron_ciclo = re.compile(r"CICLO\s*(\d+)", re.IGNORECASE)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            texto = page.extract_text()
            if texto:
                if not datos_personales["Código de Matrícula"]:
                    if match := patron_codigo.search(texto): datos_personales["Código de Matrícula"] = match.group(1)
                    if match := patron_facultad.search(texto): datos_personales["Facultad"] = match.group(1)
                    if match := patron_escuela.search(texto):
                        datos_personales["Escuela"] = match.group(1)
                        datos_personales["EscuelaNombre"] = match.group(2).strip()
                    if match := patron_especialidad.search(texto): datos_personales["Especialidad"] = match.group(1)
                    if match := patron_plan.search(texto): datos_personales["Plan"] = match.group(1) + "  "
                ciclos.extend(patron_ciclo.findall(texto))

            tablas = page.extract_tables()
            for table in tablas:
                filas_tablas.extend(table)

    excel_path = convertir_a_excel(datos_personales, ciclos, filas_tablas, excel_path)
    return excel_path

def convertir_a_excel(datos, ciclos, filas_tablas, excel_path):
    os.makedirs(UPLOADS_FOLDER, exist_ok=True)
    excel_path = os.path.join(UPLOADS_FOLDER, os.path.basename(excel_path))

    df_datos = pd.DataFrame([datos])
    df_ciclos = pd.DataFrame(ciclos, columns=["Ciclo"])
    df_tablas = pd.DataFrame(filas_tablas)

    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        df_datos.to_excel(writer, sheet_name='Datos', index=False)
        df_ciclos.to_excel(writer, sheet_name='Ciclos', index=False)
        df_tablas.to_excel(writer, sheet_name='TablaCursos', index=False)

    return excel_path

def convertir_a_json(excel_path, json_path):
    xls = pd.ExcelFile(excel_path)
    df_datos = pd.read_excel(xls, sheet_name="Datos")
    df_ciclos = pd.read_excel(xls, sheet_name="Ciclos")
    df_tablas = pd.read_excel(xls, sheet_name="TablaCursos", dtype=str)

    datos = df_datos.iloc[0]
    codEstudiante = str(datos["Código de Matrícula"])
    codFacultad = int(str(datos["Código de Matrícula"])[2:4])
    codEscuela = int(datos["Escuela"])
    codEspecialidad = int(datos["Especialidad"])
    codPlan = str(datos["Plan"])
    nombreEscuela = datos.get("EscuelaNombre", "")

    ciclos = df_ciclos["Ciclo"].astype(int).tolist()
    ciclo_actual = None
    cursos_final = []

    for _, row in df_tablas.iterrows():
        row = [str(cell).replace('\n', ' ').strip() if pd.notna(cell) else "" for cell in row.tolist()]

        if len(row) > 1 and "Asignatura" in row[1]:
            ciclo_actual = ciclos.pop(0) if ciclos else None
            continue

        if len(row) < 6 or not row[1] or "-" not in row[1]:
            continue

        codAsignatura, desAsignatura = [part.strip().replace('\n', ' ') for part in row[1].split("-", 1)]

        creditos = int(float(row[2])) if pd.notna(row[2]) else 0
        tipo = row[3] if pd.notna(row[3]) else "O"
        codGrupo = row[4] if pd.notna(row[4]) else "--"
        prereq = row[5]
        codGrupoPre = row[6] if len(row) > 6 and pd.notna(row[6]) else "--"

        prerequisitos = []
        if prereq:
            partes = re.split(r"\n|,|\r", prereq)
            for pre in partes:
                if "-" in pre:
                    codPre, desPre = [p.strip() for p in pre.split("-", 1)]
                    prerequisitos.append((codPre, desPre))
        else:
            prerequisitos.append(("NR", "NR"))

        for codPre, desPre in prerequisitos:
            curso_json = {
                "codFacultad": codFacultad,
                "codEscuela": codEscuela,
                "codPlan": codPlan,
                "codEspecialidad": codEspecialidad,
                "ciclo": ciclo_actual if ciclo_actual else 0,
                "codAsignatura": codAsignatura,
                "desAsignatura": desAsignatura,
                "creditos": creditos,
                "tipoAsignatura": tipo,
                "codGrupo": codGrupo,
                "codAsignaturaPre": codPre,
                "desAsignaturaPre": desPre,
                "codGrupoPre": codGrupoPre,
                "creditosPre": 0
            }
            cursos_final.append(curso_json)
    tokenArchivo = str(uuid.uuid4())
    resultado_final = {
            "carrera": nombreEscuela,
            "cursos": cursos_final,
            "tokenArchivo":tokenArchivo
        }
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        
        idplan=guardar_plan_y_asignaturas(codFacultad, codEscuela, codEspecialidad, codPlan, nombreEscuela, cursos_final,cursor, conn)
        crear_o_actualizar_usuario_por_pdf(codEstudiante, tokenArchivo, idplan, cursor, conn)
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, indent=4, ensure_ascii=False)
