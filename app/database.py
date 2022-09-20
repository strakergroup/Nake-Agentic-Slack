import os
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


# This is the first place enviroment variables are used
load_dotenv()


class EnginePool:
    """This class contains engines for different databases."""

    databases: tuple[str] = (
        "ray_integration",
        "ray_integration_readonly",
        "sitemanager",
        "sitemanager_readonly",
        "api",
        "api_readonly",
        "ray_integration_log",
    )
    """The list of databases that this app uses. This should match the arguments
    in the __init__() function for text editor autocomplete features."""

    def __init__(
        self,
        ray_integration: Engine,
        ray_integration_readonly: Engine,
        sitemanager: Engine,
        sitemanager_readonly: Engine,
        api: Engine,
        api_readonly: Engine,
        ray_integration_log: Engine,
    ) -> None:
        self.ray_integration = ray_integration
        self.ray_integration_readonly = ray_integration_readonly
        self.sitemanager = sitemanager
        self.sitemanager_readonly = sitemanager_readonly
        self.api = api
        self.api_readonly = api_readonly
        self.ray_integration_log = ray_integration_log

    def ping_all(self):
        for db in self.databases:
            engine: Engine = getattr(self, db)
            with engine.connect() as conn:
                conn.scalar(select(1))

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
        db_name = re.sub(r"_readonly$", "", database, flags=re.IGNORECASE)
        assert host, f"The DB_HOST_{database} environment variable is not set"
        assert port, f"The DB_PORT_{database} environment variable is not set"
        assert user, f"The DB_USER_{database} environment variable is not set"
        assert password, f"The DB_PASSWORD_{database} environment variable is not set"
        database_url = f"mysql+mysqldb://{user}:{password}@{host}:{port}/{db_name}"
        return create_engine(database_url, future=True, pool_recycle=7200)


# Other modules will import this to use the database.
engines = EnginePool.load()
