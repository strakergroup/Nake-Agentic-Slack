import hashlib
import inspect
import contextvars
import re
import logging

from buglog import notify_exception
from .database import engines
from sqlalchemy import text
from .redis import redis_sync as redis_conn
from .config import Environment, config


class Translator:
    BCP_47_TO_SHORTNAME = None

    def __init__(self, lang: str):
        if Translator.BCP_47_TO_SHORTNAME is None:
            Translator.BCP_47_TO_SHORTNAME = self.generate_language_map()
        self.lang: str = Translator.BCP_47_TO_SHORTNAME.get(lang, lang)
        self.cache: dict[str, str] = {}

    @classmethod
    def generate_language_map(cls):
        language_map = {}
        with engines["translators_readonly"].connect() as conn:
            result = conn.execute(
                text(
                    "SELECT bcp_47, shortname FROM obj_m_langs WHERE bcp_47 IS NOT NULL AND bcp_47 != ''"
                )
            )
            for row in result:
                language_map[row[0]] = row[1]
        return language_map

    def translate(self, input: str):

        if self.lang.lower().startswith(("en", "gb", "us")):
            return input
        if input in self.cache:
            return self.cache[input]
        translation = input
        # check redis for translation
        input_hash = hashlib.sha256(input.encode()).hexdigest()
        # cached_translation = redis_conn.get(f"translation:{self.lang}:{input_hash}")
        # if cached_translation:
        #     return cached_translation
        # prepare input for translation by replacing emojis and python varible expansion with x tags
        replacements = {}
        for i, match in enumerate(re.finditer(r":\w+:|\{.*?\}", input)):
            tag = f"<x id={i+1}>"
            replacements[match.group()] = tag
            translation = translation.replace(match.group(), tag)

        # get translation from db
        with engines["sitemanager_readonly"].connect() as conn:
            sql = text(
                """
                SELECT langstring
                FROM obj_stringtranslator
                WHERE lang = :lang
                AND label = :input
                """,
            ).bindparams(lang=self.lang, input=translation)
            translation_row = conn.execute(sql).fetchone()
            if translation_row:
                translation = translation_row[0]
            else:
                # log error missing translation
                logging.warning(f"WARNING Missing translation for {self.lang}: {input}")
                return input
        # place back the emojis and python variable expansion from the input
        for original, tag in replacements.items():
            translation = translation.replace(tag, original)
            # cache in redis
        # if translation != input:
        # redis_conn.set(f"translation:{self.lang}:{input_hash}", translation)
        self.cache[input] = translation
        return translation


translator_var = contextvars.ContextVar("translator", default=Translator("en"))


def _(input: str):
    translator = translator_var.get()
    frame = inspect.currentframe()
    try:
        outer_locals = {}
        outer_globals = {}
        if frame and frame.f_back:
            outer_locals = frame.f_back.f_locals
            outer_globals = frame.f_back.f_globals
    finally:
        del frame  # Avoid a reference cycle
    try:
        all_vars = {**outer_globals, **outer_locals}
        input = translator.translate(input)
        return input.format(**all_vars)
    except Exception as e:
        notify_exception(e)
        return input
