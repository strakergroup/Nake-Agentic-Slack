import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


# This is the first place enviroment variables are used
load_dotenv()


def _create_engine(database: str) -> Engine:
    host = os.getenv(f"DB_HOST_{database}")
    port = os.getenv(f"DB_PORT_{database}")
    user = os.getenv(f"DB_USER_{database}")
    password = os.getenv(f"DB_PASSWORD_{database}")
    assert host, f"The DB_HOST_{database} environment variable is not set"
    assert port, f"The DB_PORT_{database} environment variable is not set"
    assert user, f"The DB_USER_{database} environment variable is not set"
    assert password, f"The DB_PASSWORD_{database} environment variable is not set"
    database_url = f"mysql+mysqldb://{user}:{password}@{host}:{port}/{database}"
    return create_engine(database_url, future=True)


engines: dict[str, Engine] = {
    db: _create_engine(db) for db in ("ray_integration", "sitemanager", "api")
}
