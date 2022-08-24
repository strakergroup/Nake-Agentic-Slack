from urllib.parse import urlencode
from babel.numbers import format_currency as babel_format_currency

from ..config import config


def get_job_url(job_uuid: str, client_id: str | None = None) -> str:
    """Generates the URL of a specific job.

    Args:
        job_id (str): The job UUID (`obj_tp_job.obj_uuid`).
        client_id (str | None, optional): The client's UUID (member_id).
            Defaults to None.

    Returns:
        str: The URL of the job.
    """
    return "{domain}/job/detail?{params}".format(
        domain=config.deltaray_domain,
        params=urlencode({"j": job_uuid, "member_id": client_id or ""}),
    )


def format_currency(number: str | float, currency: str):
    """Format a currency value to display to users."""
    currency = format_currency_symbol(currency)
    return babel_format_currency(number, currency, locale="en_GB")


def format_currency_symbol(currency: str):
    """Format the currency property from RAY event. Returns a valid
    currency symbol for babel.currency()
    """
    if currency.startswith("USD_"):
        return "USD"
    if currency.startswith("EUR_"):
        return "EUR"
    return currency
