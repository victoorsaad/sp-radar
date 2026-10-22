"""Database connection helpers and schema bootstrap."""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

CONNINFO = (
    f"host={os.environ.get('PGHOST', '127.0.0.1')} "
    f"port={os.environ.get('PGPORT', '5432')} "
    f"dbname={os.environ['PGDATABASE']} "
    f"user={os.environ['PGUSER']} "
    f"password={os.environ['PGPASSWORD']}"
)


def connect():
    """Return a new psycopg connection."""
    return psycopg.connect(CONNINFO)


def init_schema():
    """Create tables/indexes if they do not exist."""
    sql = (Path(__file__).resolve().parent / "schema.sql").read_text()
    with connect() as conn:
        conn.execute(sql)
        conn.commit()


if __name__ == "__main__":
    init_schema()
    print("schema ok")
