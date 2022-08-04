from typing import Any


class WatsonResponse:
    """The response from IBM Watson Assistant after sending it a message
    to analyse. Contains the calculated text response, intents, and entities.
    """

    def __init__(self, output: dict[str, Any]) -> None:
        self._output = output
        print(output)
        if not all(key in output for key in ("generic", "intents", "entities")):
            raise ValueError(
                "The Watson Assistant message response output has a missing key"
            )

        self._reply = output["generic"][0]["text"]
        self._intent = self.get_intent(output["intents"])
        self._entities = output["entities"]

    @property
    def reply(self) -> str | None:
        # TODO: Default message if somehow no reply
        return self._reply

    @property
    def intent(self) -> str | None:
        return self._intent

    @property
    def entities(self) -> list[dict[str, Any]]:
        return self._entities

    @staticmethod
    def get_intent(
        intents: list[dict[str, Any]], min_confidence: float = 0.2
    ) -> str | None:
        if intents:
            intent = intents[0]
            if intent.get("confidence", 0) >= min_confidence:
                return intent.get("intent")
        return None
