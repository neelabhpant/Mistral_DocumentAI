"""Thin access layer for the governed data lake: PDFs in S3 (via the Hadoop CLI, so IDBroker credentials
apply) and Iceberg tables via the project's Impala data connection."""

import os
import subprocess
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

from ap.config import DB, IMPALA_CONNECTION, S3_ROOT

LANDING = f"{S3_ROOT}/landing"
_ENV = {**os.environ, "HADOOP_ROOT_LOGGER": "ERROR,console"}


# ---------- S3 ----------


def hdfs(*args: str, data: bytes | None = None) -> bytes:
    r = subprocess.run(["hdfs", "dfs", *args], input=data, capture_output=True, env=_ENV)
    if r.returncode:
        raise RuntimeError(f"hdfs dfs {' '.join(args)} failed: {r.stderr.decode()[-500:]}")
    return r.stdout


def put_files(paths: list, prefix: str = LANDING) -> None:
    """Upload local files in one CLI call (each call starts a JVM, so batch them)."""
    hdfs("-mkdir", "-p", prefix)
    hdfs("-put", "-f", *map(str, paths), prefix + "/")


def put_bytes(data: bytes, uri: str) -> None:
    hdfs("-put", "-f", "-", uri, data=data)


def get_bytes(uri: str) -> bytes:
    return hdfs("-cat", uri)


# ---------- Impala / Iceberg ----------


@lru_cache(maxsize=1)
def _connection():
    import cml.data_v1 as cmldata

    return cmldata.get_connection(IMPALA_CONNECTION)


def cursor():
    return _connection().get_cursor()


def execute(sql: str) -> None:
    cursor().execute(sql)


def query(sql: str) -> list[dict]:
    cur = cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description or []]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def lit(v) -> str:
    """SQL literal for Impala."""
    if v is None or v == "":
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return repr(v) if not isinstance(v, Decimal) else str(v)
    if isinstance(v, datetime):
        return f"CAST('{v:%Y-%m-%d %H:%M:%S}' AS TIMESTAMP)"
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def insert(table: str, rows: list[dict], batch: int = 200) -> None:
    if not rows:
        return
    cols = list(rows[0])
    for i in range(0, len(rows), batch):
        values = ",\n".join("(" + ", ".join(lit(r[c]) for c in cols) + ")" for r in rows[i : i + batch])
        execute(f"INSERT INTO {DB}.{table} ({', '.join(cols)}) VALUES\n{values}")
