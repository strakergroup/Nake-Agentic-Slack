import os
from dotenv import load_dotenv
from sqlalchemy import create_engine


# This is the first place enviroment variables are used
load_dotenv()

_host = os.getenv("DB_HOST")
_port = os.getenv("DB_PORT")
_name = os.getenv("DB_NAME")
_user = os.getenv("DB_USER")
_password = os.getenv("DB_PASSWORD")

DATABASE_URL = f"mysql+mysqldb://{_user}:{_password}@{_host}:{_port}/{_name}"

engine = create_engine(DATABASE_URL, future=True)
