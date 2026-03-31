from dataclasses import dataclass
from typing import Any, cast

from ibm_watson import DetailedResponse


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

    def __init__(
        self,
        input: str,
        data: dict[str, Any],
        headers: dict[str, str],
        status_code: int,
    ) -> None:
        self._input = input
        self._data = data
        self._headers = headers
        self._status_code = status_code
        self._output = data["output"]
        if not all(key in self._output for key in ("generic", "intents", "entities")):
            raise ValueError(
                "The Watson Assistant message response output has a missing key"
            )
        self._reply = (
            self._output["generic"][0]["text"]
            if self._output.get("generic")
            and self._output["generic"][0].get("response_type") == "text"
            else None
        )
        self._intent = self.get_intent(self._output["intents"])
        self._entities = [
            Entity.fromJSON(e, self.input) for e in self._output["entities"]
        ]

    @property
    def input(self) -> str:
        """The message sent to Watson Assistant."""
        return self._input

    @property
    def data(self) -> dict[str, Any]:
        """The raw JSON data returned from the IBM Watson Assistant API."""
        return self._data

    @property
    def headers(self) -> dict[str, str]:
        """The headers returned from the IBM Watson Assistant API."""
        return self._headers

    @property
    def status_code(self) -> int:
        """The status code returned from the IBM Watson Assistant API."""
        return self._status_code

    @property
    def reply(self) -> str | None:
        """The parsed reply text from Watson Assistant."""
        return self._reply

    @property
    def intent(self) -> str | None:
        """The parsed and matched intent from Watson Assistant."""
        return self._intent

    @property
    def entities(self) -> list[Entity]:
        """The parsed entites from Watson Assistant."""
        return self._entities

    def findEntity(self, entity: str) -> Entity | None:
        """Finds and returns the entity with the given entity name. Returns None
        if it doesn't exist in this response.
        """
        for e in self.entities:
            if e.name == entity:
                return e
        return None

    @classmethod
    def from_assistant_v2(
        cls, input: str, watson_response: DetailedResponse
    ) -> "WatsonResponse":
        """Creates an instance from the response from the ibm_watson sdk."""
        data = cast(dict[str, Any], watson_response.get_result() or {})
        headers = cast(dict[str, str], watson_response.get_headers() or {})
        status_code = watson_response.get_status_code() or 0
        return cls(
            input=input,
            data=data,
            headers=headers,
            status_code=status_code,
        )

    @staticmethod
    def get_intent(
        intents: list[dict[str, Any]], min_confidence: float = 0.2
    ) -> str | None:
        """Parse the highest matched intent from the JSON response data."""
        if intents:
            intent = intents[0]
            if intent.get("confidence", 0) >= min_confidence:
                return intent.get("intent")
        return None
