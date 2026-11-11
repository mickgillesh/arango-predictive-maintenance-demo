import os

from arango import ArangoClient
from arango.database import StandardDatabase


def get_db() -> StandardDatabase:
    url = os.environ["ARANGO_URL"]
    db_name = os.environ["ARANGO_DB"]
    user = os.environ["ARANGO_USER"]
    password = os.environ["ARANGO_PASSWORD"]
    client = ArangoClient(hosts=url)
    return client.db(db_name, username=user, password=password)


def ensure_database() -> bool:
    """Create ``ARANGO_DB`` if it does not exist. Returns True if it was created.

    Connects to ``_system`` with the same credentials, so the user needs
    permission to create databases. Refuses names without "aerofleet", matching
    the loader's safety guard.
    """
    db_name = os.environ["ARANGO_DB"]
    if "aerofleet" not in db_name:
        raise ValueError(f"ARANGO_DB={db_name!r} must contain 'aerofleet'")
    client = ArangoClient(hosts=os.environ["ARANGO_URL"])
    sys_db = client.db("_system", username=os.environ["ARANGO_USER"],
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
