import math
import datetime
from urllib.parse import urlencode
from babel.numbers import format_currency as babel_format_currency

from ..config import domains


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
        domain=domains.deltaray,
        params=urlencode({"j": job_uuid, "member_id": client_id or ""}),
    )


def format_currency_symbol(currency: str) -> str:
    """Format the currency property from RAY event. Returns a valid
    currency symbol for babel.currency().
    """
    if currency.startswith("USD_"):
        return "USD"
    if currency.startswith("EUR_"):
        return "EUR"
    return currency


def format_currency(number: str | float, currency: str):
    """Format a currency value to display to users."""
    currency = format_currency_symbol(currency)
    return babel_format_currency(number, currency, locale="en_GB")


def format_job_status(status: str) -> str:
    """Formats the job status returned from the API to a human-readable string.

    Args:
        status (str): The job status value, e.g. IN_PROGRESS.

    Returns:
        str: The formatted job status string, e.g. In Progress.
    """
    if not status:
        return ""
    match status.strip().upper():
        case "LEAD":
            return "Quote Requested"
        case "IN_PROGRESS":
            return "In Progress"
        case "VALIDATION":
            return "In Validation"
        case "CANCELLED":
            return "Cancelled"
        case "CLIENT_CANCELLED":
            return "Client Cancelled"
        case "CLOSED":
            return "Closed"
        case "WAITING":
            return "Waiting"
        case "REFUNDED":
            return "Refunded"
        case "COMPLETED":
            return "Completed"
        # Derived statuses.
        case "PENDING_QUOTES":  # status = "LEAD" + quote <= 1
            return "Quote Requested"
        case "ORDER_NOW":  # status = "LEAD" + quote > 1
            return "Order Now"
        case _:
            return status.strip()


def format_datetime_slack(date: datetime.datetime) -> str:
    """Format a datetime to a Slack formatted string, this displays the time
    in the Slack user's timezone. Naive datetimes are assumed to be UTC.
    """
    aware_date = date
    if date.tzinfo is None:
        aware_date = date.replace(tzinfo=datetime.timezone.utc)

    timestamp = int(aware_date.timestamp())
    fallback = aware_date.strftime("%Y-%m-%d %H:%M UTC")
    return f"<!date^{timestamp}^{{date}} {{time}}|{fallback}>"


def format_job_due_date_slack(
    target_date: datetime.datetime,
    job_status: str | None = None,
    traffic_light: bool = True,
) -> str:
    """Formats a job due date to display in Slack. Returns "in x hours" if the
    date is within 48 hours. Naive datetimes are assumed to be UTC.

    Args:
        target_date (datetime.datetime): The job's target date (due date).
        job_status (str | None, optional): The job status. Defaults to None.
        traffic_light (bool, optional): Adds a red or green light indicator
            when a job's due date is in the past and the job status is
            `IN_PROGRESS`. Defaults to True.

    Returns:
        str: The formatted string for the job's due date.
    """
    if target_date.tzinfo is None:
        target_date = target_date.replace(tzinfo=datetime.timezone.utc)
    date_delta = target_date - datetime.datetime.now(datetime.timezone.utc)
    formatted_date = (
        f"in {math.ceil(date_delta.total_seconds() / 3600)} hour(s)"
        if 0 < date_delta.total_seconds() < 48 * 3600
        else format_datetime_slack(target_date)
    )

    if traffic_light and job_status == "IN_PROGRESS":
        if datetime.datetime.now(datetime.timezone.utc) >= target_date:
            return f":red_circle: {formatted_date}"
        else:
            return f":large_green_circle: {formatted_date}"
    return formatted_date


def format_predictions(in_progress_count: int, predictions: dict) -> str:
    """Returns the progress text for the job."""
    status = f"*In Progress Jobs*\n{in_progress_count} job(s) currently in progress"
    if in_progress_count:
        aPredictions = []
        if predictions["on_time"]:
            aPredictions.append(
                f":large_green_circle: *{predictions['on_time']} job(s)* are predicted to be on-time"
            )
        if predictions["late"]:
            aPredictions.append(
                f":large_orange_circle: *{predictions['late']} job(s)* are behind schedule"
            )
        if predictions["over_due"]:
            aPredictions.append(
                f":red_circle: *{predictions['over_due']} job(s)* are overdue"
            )
        status = "*In Progress Jobs*\n" + "\n".join(aPredictions)
    return status
