import os
import stat

from mcui.auth.store import Store
from mcui.files import private_db
from mcui.metrics.store import SQLiteMetricStore
from tests.conftest import fast_store


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_databases_are_private_despite_umask(tmp_path):
    old = os.umask(0o022)  # a container's umask
    try:
        auth = Store(tmp_path / "data" / "mcui.db", hasher=fast_store().hasher)
        metrics = SQLiteMetricStore(tmp_path / "data" / "metrics.db")
    finally:
        os.umask(old)
    try:
        assert mode(tmp_path / "data") == 0o700
        for name in ("mcui.db", "mcui.db-wal", "mcui.db-shm", "metrics.db", "metrics.db-wal"):
            assert mode(tmp_path / "data" / name) == 0o600, name
    finally:  # closing removes the WAL files
        auth.close()
        metrics.db.close()


def test_existing_readable_files_are_tightened(tmp_path):
    db, wal = tmp_path / "mcui.db", tmp_path / "mcui.db-wal"
    db.touch(mode=0o644)
    wal.touch(mode=0o644)
    db.chmod(0o644)
    wal.chmod(0o644)
    private_db(db)
    assert mode(db) == 0o600 and mode(wal) == 0o600
