from flask import Flask, request, jsonify, send_file,Response
import os
import json

import openpyxl
from openpyxl.styles import PatternFill, Alignment, Border, Side
from io import BytesIO
import MySQLdb
from conexionbd import create_app, get_db_connection
from pdf_processor import procesar_pdf, convertir_a_json
import logging
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from decimal import Decimal

logging.basicConfig(level=logging.INFO)

app = create_app()

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)


@app.route('/guardar_estado', methods=['POST'])
def guardar_estado_multiple():
    data = request.get_json()
    username = data.get('user')
    token = data.get('tokenArchivo')
    notas = data.get('notas', {})

    if not username or not token or not notas:
        return jsonify({'success': False, 'error': 'Datos incompletos'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM usuarios WHERE username = %s AND token_archivo = %s", (username, token))
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': 'Usuario no encontrado'}), 404

    user_id = user[0]

    for cod_asignatura, info in notas.items():
        estado = info.get('estado', 'ninguno')
        nota_final = info.get('nota')
        parciales = info.get('parciales', [])



        # Buscar si ya existe relación
        cursor.execute("""
            SELECT id FROM usuarios_asignaturas
            WHERE id_usuario = %s AND cod_asignatura = %s
        """, (user_id, cod_asignatura))
        ua = cursor.fetchone()
        print(ua,info)

        if ua:
            ua_id = ua[0]
            cursor.execute("""
                UPDATE usuarios_asignaturas
                SET estado = %s, nota = NULL
                WHERE id = %s
            """, (estado, ua_id))
        else:
            cursor.execute("""
                INSERT INTO usuarios_asignaturas (id_usuario, cod_asignatura, estado, nota)
                VALUES (%s, %s, %s, NULL)
            """, (user_id, cod_asignatura, estado))
            ua_id = cursor.lastrowid

        # Eliminar parciales antiguos (si los hubiera)
        cursor.execute("DELETE FROM notas_parciales WHERE id_usuario_asignatura = %s", (ua_id,))
        if estado == 'llevado' and isinstance(nota_final, (int, float)):
            cursor.execute("""
                UPDATE usuarios_asignaturas
                SET nota = %s
                WHERE id = %s
            """, (nota_final, ua_id))

        elif estado == 'en curso':
            # Reiniciar nota final previa
            cursor.execute("""
                UPDATE usuarios_asignaturas
                SET nota = NULL
                WHERE id = %s
            """, (ua_id,))

            suma_ponderada = 0
            total_porcentaje = 0
            for parcial in parciales:
                try:
                    nota = float(parcial['nota'])
                    porcentaje = float(parcial['porcentaje'])
                    if not (0 <= nota <= 20) or not (0 <= porcentaje <= 100):
                        continue
                    suma_ponderada += nota * porcentaje
                    total_porcentaje += porcentaje
                    cursor.execute("""
                        INSERT INTO notas_parciales (id_usuario_asignatura, nota, porcentaje)
                        VALUES (%s, %s, %s)
                    """, (ua_id, nota, porcentaje))
                except (ValueError, TypeError):
                    continue

            if total_porcentaje > 0:
                promedio = round(suma_ponderada / 100, 2)

                cursor.execute("""
                    UPDATE usuarios_asignaturas
                    SET nota = %s
                    WHERE id = %s
                """, (promedio, ua_id))

        else:  
            cursor.execute("""
                UPDATE usuarios_asignaturas
                SET nota = NULL
                WHERE id = %s
            """, (ua_id,))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'success': True, 'message': 'Notas y estados guardados correctamente'})



@app.route('/upload', methods=['POST'])
def upload_file():
    if 'pdf' not in request.files:
        return 'No se encontró ningún archivo', 400

    file = request.files['pdf']
    if file.filename == '' or not file.filename.endswith('.pdf'):
        return 'Archivo inválido. Sube un PDF.', 400

    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(pdf_path)

    base_filename = os.path.splitext(file.filename)[0]
    excel_name = f"{base_filename}.xlsx"
    json_name = f"{base_filename}.json"

    excel_real_path = procesar_pdf(pdf_path, excel_name)
    json_path = os.path.join(app.config['PROCESSED_FOLDER'], json_name)

    convertir_a_json(excel_real_path, json_path)

    with open(json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    return jsonify(json_data)



class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)  
        return super().default(obj)
    
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('user')
    password = data.get('pass')

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, password_hash FROM usuarios WHERE username = %s", (username,))
        usuario = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if usuario and check_password_hash(usuario[1], password):
        datos_usuario = obtener_datos_dict(username)
        
        

        return Response(
            json.dumps(datos_usuario, cls=CustomJSONEncoder, ensure_ascii=False),
            mimetype='application/json; charset=utf-8'
        )
    else:
        return jsonify({"success": False, "error": "Credenciales inválidas"}), 401


def obtener_datos_dict(username):
    with get_db_connection() as conn:
        with conn.cursor(MySQLdb.cursors.DictCursor) as cursor:
            query = """
            SELECT u.*, p.*, a.*
            FROM usuarios u
            LEFT JOIN planes_estudio p ON u.id_plan = p.id
            LEFT JOIN asignaturas a ON a.id_plan = u.id_plan
            WHERE u.username = %s
            ORDER BY a.ciclo ASC
            """
            cursor.execute(query, (username,))
            result = cursor.fetchall()

            if not result:
                return {}

            usuario = result[0]
            plan = {key: usuario[key] for key in ["cod_escuela", "cod_especialidad", "cod_facultad", "cod_plan", "carrera"]}

            # Crear lista de cursos con comprensión de listas
            cursos = [{
                "ciclo": a["ciclo"],
                "codAsignatura": a["cod_asignatura"],
                "desAsignatura": a["des_asignatura"],
                "codAsignaturaPre": a["cod_asignatura_pre"] or "NR",
                "desAsignaturaPre": get_descripcion_asignatura(a["cod_asignatura_pre"]),
                "codEscuela": plan["cod_escuela"],
                "codEspecialidad": plan["cod_especialidad"],
                "codFacultad": plan["cod_facultad"],
                "codPlan": plan["cod_plan"],
                "codGrupo": a["cod_grupo"] or "--",
                "codGrupoPre": a["cod_grupo_pre"] or "--",
                "creditos": a["creditos"],
                "creditosPre": a["creditos_pre"] or 0,
                "tipoAsignatura": a["tipo_asignatura"]
            } for a in result]

            return {
                "carrera": plan["carrera"],
                "cursos": cursos,
                "tokenArchivo": usuario["token_archivo"],
                "notas": obtener_notas_usuario(usuario['id']) 
            }


def obtener_notas_usuario(usuario_id): 
    conn = get_db_connection()
    cursor = conn.cursor(MySQLdb.cursors.DictCursor)

    try:
        cursor.execute("""
            SELECT cod_asignatura, estado, nota, id
            FROM usuarios_asignaturas
            WHERE id_usuario = %s
        """, (usuario_id,))
        notas = cursor.fetchall()

        notas_dict = {}

        for nota in notas:
            if nota['estado'] == 'llevado':
                notas_dict[nota['cod_asignatura']] = {
                    "estado": nota['estado'],
                    "nota": nota['nota'],  # Solo la nota final
                }
            else:
                # Si el curso está "en curso", buscamos los parciales
                cursor.execute("""
                    SELECT nota, porcentaje
                    FROM notas_parciales
                    WHERE id_usuario_asignatura = %s
                """, (nota['id'],))  # Usamos el ID de la asignatura
                parciales = cursor.fetchall()

                notas_dict[nota['cod_asignatura']] = {
                    "estado": nota['estado'],
                    "nota": None,  # No tiene una nota final calculada
                    "parciales": [{"nota": p['nota'], "porcentaje": p['porcentaje']} for p in parciales] if parciales else [],
                }

        return notas_dict
    finally:
        cursor.close()
        conn.close()



def get_descripcion_asignatura(cod_asignatura_pre):
    if not cod_asignatura_pre or cod_asignatura_pre == "NR":
        return "NR"

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT des_asignatura FROM asignaturas WHERE cod_asignatura = %s", (cod_asignatura_pre,))
        result = cursor.fetchone()
        return result[0] if result else "Desconocido"
    finally:
        cursor.close()
        conn.close()



@app.route('/register', methods=['POST'])
def register():
    data = request.json

    username = data.get('user')
    correo = data.get('correo')
    password = data.get('pass')
    tokenArchivo = data.get('tokenArchivo')

    if not all([username, correo, password, tokenArchivo]):
        return jsonify({'success': False, 'error': 'Faltan datos requeridos'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM usuarios WHERE token_archivo = %s", (tokenArchivo,))
        user = cursor.fetchone()

        if not user:
            return jsonify({'success': False, 'error': 'Token inválido o expirado'}), 404

        user_id = user[0]

        cursor.execute("SELECT 1 FROM usuarios WHERE username = %s AND id != %s", (username, user_id))
        if cursor.fetchone():
            return jsonify({'success': False, 'error': 'El nombre de usuario ya está en uso'}), 409

        cursor.execute("SELECT 1 FROM usuarios WHERE correo = %s AND id != %s", (correo, user_id))
        if cursor.fetchone():
            return jsonify({'success': False, 'error': 'El correo ya está registrado'}), 409

        hashed_password = generate_password_hash(password)

        cursor.execute("""
            UPDATE usuarios 
            SET username = %s, correo = %s, password_hash = %s 
            WHERE id = %s
        """, (username, correo, hashed_password, user_id))

        conn.commit()
        return jsonify({'success': True, 'message': 'Usuario registrado correctamente'})
    
    finally:
        cursor.close()
        conn.close()



if __name__ == '__main__':
    app.run(debug=True)
