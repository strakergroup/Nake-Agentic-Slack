import contextvars
import inspect
import logging
import re

from sqlalchemy import text

from app.slack.buglog_notifier import notify_exception

from .database import engines

SELF_CLOSING_X_TAG = "<x id={index}/>"
LEGACY_X_TAG = "<x id={index}>"


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

    def translate(self, input: str, max_length: int = 0) -> tuple[str, bool]:
        if self.lang.lower().startswith(("en", "gb", "us")):
            return input, True
        if input in self.cache:
            return self.cache[input], True
        translation = input
        # check redis for translation
        # input_hash = hashlib.sha256(input.encode()).hexdigest()
        # cached_translation = redis_conn.get(f"translation:{self.lang}:{input_hash}")
        # if cached_translation:
        #     return cached_translation
        # prepare input for translation by replacing emojis and python varible expansion with x tags
        replacements = {}
        legacy_label = input
        for i, match in enumerate(re.finditer(r":\w+:|\{.*?\}", input)):
            index = i + 1
            tag = SELF_CLOSING_X_TAG.format(index=index)
            legacy_tag = LEGACY_X_TAG.format(index=index)
            replacements[match.group()] = (tag, legacy_tag)
            translation = translation.replace(match.group(), tag)
            legacy_label = legacy_label.replace(match.group(), legacy_tag)

        # get translation from db
        with engines["sitemanager_readonly"].connect() as conn:
            sql = text(
                """
                SELECT langstring
                FROM obj_stringtranslator
                WHERE lang = :lang
                AND label = :input
                order by created desc
                """,
            ).bindparams(lang=self.lang, input=translation)
            translation_row = conn.execute(sql).fetchone()
            if not translation_row and legacy_label != translation:
                legacy_sql = text(
                    """
                    SELECT langstring
                    FROM obj_stringtranslator
                    WHERE lang = :lang
                    AND label = :input
                    order by created desc
                    """,
                ).bindparams(lang=self.lang, input=legacy_label)
                translation_row = conn.execute(legacy_sql).fetchone()
            if translation_row:
                translation = translation_row[0]
                if max_length and len(translation) > max_length:
                    logging.warning(
                        f"WARNING Translation for {self.lang}: {input} exceeds max length {max_length}"
                    )
                    return translation, False
            else:
                # log error missing translation
                logging.warning(f"WARNING Missing translation for {self.lang}: {input}")
                return input, False
        # place back the emojis and python variable expansion from the input
        for original, (tag, legacy_tag) in replacements.items():
            translation = translation.replace(tag, original)
            translation = translation.replace(legacy_tag, original)
            # cache in redis
        # if translation != input:
        # redis_conn.set(f"translation:{self.lang}:{input_hash}", translation)
        self.cache[input] = translation

        return translation, True


translator_var = contextvars.ContextVar("translator", default=Translator("en"))


def _(input: str, max_length: int = 0) -> str:
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
        input, success = translator.translate(input, max_length)
        return input.format(**all_vars)
    except Exception as e:
        notify_exception(e)
        return input
