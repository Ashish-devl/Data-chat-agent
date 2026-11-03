"""Load Chinook, a public sample music-store database (MIT licence, Luis Rocha), into Postgres.

The script is downloaded once from the pinned v1.4.5 release and checked against its SHA-256.
It creates database `chinook` with user chinook_readonly / chinook_readonly (SELECT only).

Usage:
  python eval/datasets/chinook.py postgresql://datachat:datachat@localhost:5432/postgres
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

URL = "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_PostgreSql.sql"
SHA256 = "e3fde5c1a5b51a2a91429a702c9ca6e69ba56e6c7f5e112724d70c3d03db695e"
CACHE = Path.home() / ".cache" / "datachat-agent" / "datasets" / "Chinook_PostgreSql.sql"
DB = "chinook"
READONLY = "chinook_readonly"


def script() -> str:
    if not CACHE.exists() or hashlib.sha256(CACHE.read_bytes()).hexdigest() != SHA256:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=60) as r:  # noqa: S310 - fixed https URL
            data = r.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != SHA256:
            raise RuntimeError(f"Chinook download does not match the pinned SHA-256 ({digest}).")
        CACHE.write_bytes(data)
    sql = CACHE.read_text(encoding="utf-8")
    # The file switches database with a psql command; we create the database ourselves.
    return sql.split("\\c chinook;", 1)[1]


def load(admin_url: str) -> None:
    url = make_url(admin_url).set(drivername="postgresql+psycopg")
    body = script()
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {DB}"))
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :u"), {"u": READONLY})
        if exists.scalar() is None:
            conn.execute(text(f"CREATE ROLE {READONLY} LOGIN PASSWORD '{READONLY}'"))
    admin.dispose()

    target = create_engine(url.set(database=DB))
    with target.begin() as conn:
        # Straight to the driver with no parameters, so '%' in the data is not a placeholder.
        conn.connection.driver_connection.execute(body)
        conn.execute(text(f"GRANT CONNECT ON DATABASE {DB} TO {READONLY}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {READONLY}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READONLY}"))
        conn.execute(text("ANALYZE"))
        counts = {
            t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in ("artist", "album", "track", "customer", "invoice", "invoice_line")
        }
    target.dispose()
    print(f"Loaded {DB}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    load(sys.argv[1])
