#!/bin/sh

echo "Esperando a que la base de datos MySQL esté disponible..."

python << END
import sys
import time
import MySQLdb
import os

host = os.environ.get('DB_HOST', 'db')
user = os.environ.get('DB_USER', 'prestamos_user')
password = os.environ.get('DB_PASSWORD', 'prestamos_secure_password')
database = os.environ.get('DB_NAME', 'prestamos_db')
port = int(os.environ.get('DB_PORT', 3306))

while True:
    try:
        conn = MySQLdb.connect(
            host=host,
            user=user,
            passwd=password,
            db=database,
            port=port
        )
        conn.close()
        break
    except Exception as e:
        time.sleep(1)

END

echo "¡Conexión a MySQL exitosa!"

# Aplicar migraciones y estáticos
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Iniciar Gunicorn
echo "Iniciando Gunicorn..."
exec gunicorn proyecto_prestamos.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120