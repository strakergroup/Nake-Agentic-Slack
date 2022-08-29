import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


# This is the first place enviroment variables are used
load_dotenv()


class EnginePool:
    """This class contains engines for different databases."""

    databases: tuple[str] = (
        "ray_integration",
        "sitemanager",
        "api",
        "ray_integration_log",
    )
    """The list of databases that this app uses. This should match the arguments
    in the __init__() function for text editor autocomplete features."""

    def __init__(
        self,
        ray_integration: Engine,
        sitemanager: Engine,
        api: Engine,
        ray_integration_log: Engine,
    ) -> None:
        self.ray_integration = ray_integration
        self.ray_integration_log = ray_integration_log
        self.sitemanager = sitemanager
        self.api = api

    @classmethod
    def load(cls) -> "EnginePool":
        engines = {db: cls._create_engine(db) for db in cls.databases}
        return cls(**engines)

    @staticmethod
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


# Other modules will import this to use the database.
engines = EnginePool.load()
