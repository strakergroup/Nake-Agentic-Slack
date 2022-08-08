from typing import Any
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Entity:
    """An entity in the IBM Watson natural language processing model."""

    name: str
    value: str
    groups: list[str]
    confidence: float

    @classmethod
    def fromJSON(cls, entity: dict[str, Any], input: str) -> "Entity":
        """Create an Entity instance from the IBM Watson Assistant V2 API
        JSON reponse.
        """

        groups = [input[g["location"][0] : g["location"][1]] for g in entity["groups"]]
        return cls(
            name=entity["entity"],
            value=entity["value"],
            groups=groups,
            confidence=entity["confidence"],
        )


class WatsonResponse:
    """The response from IBM Watson Assistant after sending it a message
    to analyse. Contains the calculated text response, intents, and entities.
    """

    def __init__(self, input: str, output: dict[str, Any]) -> None:
        self._input = input
        self._output = output
        if not all(key in output for key in ("generic", "intents", "entities")):
            raise ValueError(
                "The Watson Assistant message response output has a missing key"
            )
        self._reply = (
            output["generic"][0]["text"]
            if output.get("generic")
            and output["generic"][0].get("response_type") == "text"
            else None
        )
        self._intent = self.get_intent(output["intents"])
        self._entities = [Entity.fromJSON(e, self.input) for e in output["entities"]]

    @property
    def input(self) -> str:
        return self._input

    @property
    def reply(self) -> str | None:
        return self._reply

    @property
    def intent(self) -> str | None:
        return self._intent

    @property
    def entities(self) -> list[Entity]:
        return self._entities

    def findEntity(self, entity: str) -> Entity | None:
        """Finds and returns the entity with the given entity name. Returns None
        if it doesn't exist in this response.
        """
        for e in self.entities:
            if e.name == entity:
                return e
        return None

    @staticmethod
    def get_intent(
        intents: list[dict[str, Any]], min_confidence: float = 0.2
    ) -> str | None:
        if intents:
            intent = intents[0]
            if intent.get("confidence", 0) >= min_confidence:
                return intent.get("intent")
        return None
