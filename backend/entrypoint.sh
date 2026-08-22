#!/bin/sh
# Apply migrations before serving. Safe to run on every boot; alembic is a no-op
# when the schema is already current.
set -e
echo "waiting for database..."
python - <<'PY'
import time, sqlalchemy, os
url = os.environ["DATABASE_URL"]
for i in range(60):
    try:
        sqlalchemy.create_engine(url).connect().close()
        print("database ready"); break
    except Exception as e:
        if i == 59: raise
        time.sleep(2)
PY
alembic upgrade head
exec "$@"
