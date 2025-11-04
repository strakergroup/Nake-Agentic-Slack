from dotenv import load_dotenv
from straker_utils.sql import AsyncDBEnginePool, DBEnginePool

# This is the first time enviroment variables are used.
load_dotenv()


engines = DBEnginePool(
    (
        "ray_integration",
        "ray_integration_readonly",
        "sitemanager",
        "sitemanager_readonly",
        "api",
        "api_readonly",
        "ray_integration_log",
        "translators_readonly",
        "sitecommons",
        "machine_translation_readonly",
        "verify",
    ),
    dbapi="mysqlconnector",
    # echo=True,  # Uncomment to log SQL queries
)

# Async database engines for use in async functions
async_engines = AsyncDBEnginePool(
    (
        "ray_integration",
        "ray_integration_readonly",
        "sitemanager",
        "sitemanager_readonly",
        "api",
        "api_readonly",
        "ray_integration_log",
        "translators_readonly",
        "sitecommons",
        "machine_translation_readonly",
        "verify",
    ),
    dbapi="mysqlconnector",
    # echo=True,  # Uncomment to log SQL queries
)
