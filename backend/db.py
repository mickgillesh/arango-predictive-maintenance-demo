import os

from arango import ArangoClient
from arango.database import StandardDatabase

_TRUE = {"", "true", "1", "yes"}
_FALSE = {"false", "0", "no"}


def tls_verify() -> bool | str:
    """TLS certificate verification for the cluster, from ``ARANGO_TLS_VERIFY``.

    ``true`` (default) checks against the system's trusted authorities,
    ``false`` disables the check (for deployments with a self-signed
    certificate), and any other value is a path to a CA bundle. The same value
    is accepted by python-arango (``verify_override``), the AI Suite SDK
    (``verify``) and the notebook's REST helper.
    """
    raw = os.environ.get("ARANGO_TLS_VERIFY", "true").strip()
    if raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    if not os.path.exists(raw):
        raise ValueError(f"ARANGO_TLS_VERIFY={raw!r} is neither true/false nor an existing path")
    return raw


def _client() -> ArangoClient:
    return ArangoClient(hosts=os.environ["ARANGO_URL"], verify_override=tls_verify())


def get_db() -> StandardDatabase:
    db_name = os.environ["ARANGO_DB"]
    user = os.environ["ARANGO_USER"]
    password = os.environ["ARANGO_PASSWORD"]
    return _client().db(db_name, username=user, password=password)


def ensure_database() -> bool:
    """Create ``ARANGO_DB`` if it does not exist. Returns True if it was created.

    Connects to ``_system`` with the same credentials, so the user needs
    permission to create databases. Refuses names without "aerofleet", matching
    the loader's safety guard.
    """
    db_name = os.environ["ARANGO_DB"]
    if "aerofleet" not in db_name:
        raise ValueError(f"ARANGO_DB={db_name!r} must contain 'aerofleet'")
    sys_db = _client().db("_system", username=os.environ["ARANGO_USER"],
                          password=os.environ["ARANGO_PASSWORD"])
    if sys_db.has_database(db_name):
        return False
    sys_db.create_database(db_name)
    return True


def check_connection() -> dict:
    db = get_db()
    version = db.version()
    user_collections = [c for c in db.collections() if not c["name"].startswith("_")]
    return {
        "version": version,
        "database": db.name,
        "collection_count": len(user_collections),
    }
