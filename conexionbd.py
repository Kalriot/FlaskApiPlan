from flask import Flask
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import MySQLdb

# Configuración de conexión
DB_CONFIG = {
    'host': 'bjy1t3rkru2hyxcvtuuq-mysql.services.clever-cloud.com',
    'user': 'ubxht0r5xhv24b6o',
    'passwd': 'xRZ9dgk2gsuYsyNo5WSd',
    'db': 'bjy1t3rkru2hyxcvtuuq',
    'port': 3306
}



def get_db_connection():
    return MySQLdb.connect(**DB_CONFIG)

def create_app():
    app = Flask(__name__)
    CORS(app)
    return app

# Lógica de modelos representados como clases normales
class Usuario:
    def __init__(self, id, username, correo, password_hash, codigo_estudiante, token_archivo, id_plan):
        self.id = id
        self.username = username
        self.correo = correo
        self._password_hash = password_hash
        self.codigo_estudiante = codigo_estudiante
        self.token_archivo = token_archivo
        self.id_plan = id_plan

    @property
    def password(self):
        raise AttributeError("No se puede acceder al password directamente.")

    @password.setter
    def password(self, password_plain):
        self._password_hash = generate_password_hash(password_plain)

    def verify_password(self, password_input):
        return check_password_hash(self._password_hash, password_input)


def guardar_plan_y_asignaturas(cod_facultad, cod_escuela, cod_especialidad, cod_plan, nombre_escuela, cursos, cursor, conn=None):
    cursor.execute("""
        SELECT id FROM planes_estudio 
        WHERE cod_facultad=%s AND cod_escuela=%s AND cod_especialidad=%s AND cod_plan=%s
    """, (cod_facultad, cod_escuela, cod_especialidad, cod_plan))
    plan = cursor.fetchone()

    if not plan:
        cursor.execute("""
            INSERT INTO planes_estudio (carrera, cod_facultad, cod_escuela, cod_especialidad, cod_plan)
            VALUES (%s, %s, %s, %s, %s)
        """, (nombre_escuela, cod_facultad, cod_escuela, cod_especialidad, cod_plan))
        plan_id = cursor.lastrowid
    else:
        plan_id = plan[0]

    for curso in cursos:
        cursor.execute("SELECT cod_asignatura FROM asignaturas WHERE cod_asignatura=%s", (curso["codAsignatura"],))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO asignaturas 
                (cod_asignatura, des_asignatura, ciclo, creditos, tipo_asignatura, cod_grupo, 
                 cod_asignatura_pre, cod_grupo_pre, creditos_pre, id_plan)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                curso["codAsignatura"], curso["desAsignatura"], curso["ciclo"], curso["creditos"],
                curso["tipoAsignatura"], curso["codGrupo"], curso["codAsignaturaPre"],
                curso["codGrupoPre"], curso["creditosPre"], plan_id
            ))

    if conn:
        conn.commit()

    return plan_id




def crear_o_actualizar_usuario_por_pdf(codigo_estudiante, token_archivo, plan_id, cursor, conn=None):
    codigo_estudiante = str(codigo_estudiante).strip()

    cursor.execute("SELECT id FROM usuarios WHERE codigo_estudiante = %s", (codigo_estudiante,))
    usuario = cursor.fetchone()

    if not usuario:
        temp_id = uuid.uuid4().hex[:8]
        temp_username = f"temp_{temp_id}"
        temp_email = f"{temp_id}@placeholder.local"
        password_hash = ''

        cursor.execute("""
            INSERT INTO usuarios (username, correo, password_hash, codigo_estudiante, token_archivo, id_plan)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (temp_username, temp_email, password_hash, codigo_estudiante, token_archivo, plan_id))
    else:
        cursor.execute("""
            UPDATE usuarios SET token_archivo = %s WHERE codigo_estudiante = %s
        """, (token_archivo, codigo_estudiante))

    if conn:
        conn.commit()