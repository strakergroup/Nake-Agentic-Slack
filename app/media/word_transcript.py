from enum import StrEnum


class WordTranscriptFormat(StrEnum):
    TEXT = "text"
    SPEAKERS = "speakers"
    TIMESTAMPS = "timestamps"
    SPEAKERS_AND_TIMESTAMPS = "speakers_and_timestamps"
