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


def check_connection() -> dict:
    db = get_db()
    version = db.version()
    user_collections = [c for c in db.collections() if not c["name"].startswith("_")]
    return {
        "version": version,
        "database": db.name,
        "collection_count": len(user_collections),
    }
