from cgi import parse_header
from typing import Literal
import math
import datetime
from urllib.parse import urlencode

from babel.numbers import format_currency as babel_format_currency
import requests

from app.translate import Translator, translator_var

from ..config import domains
from slack_sdk.web.async_client import AsyncWebClient
from io import BytesIO


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
        domain=domains.languagecloud,
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
            return f"{formatted_date}"
        else:
            return f"{formatted_date}"
    return formatted_date


def format_predictions(in_progress_count: int, predictions: dict[str, int]) -> str:
    """Returns the progress text for the job."""
    status = f"*In Progress Jobs*\n{in_progress_count} job(s) currently in progress"
    if in_progress_count:
        aPredictions = []
        if predictions["on_time"]:
            aPredictions.append(
                f":large_green_circle: *{predictions['on_time']} {'job is' if int(predictions['on_time']) == 1 else 'jobs are'} predicted to be on-time"
            )
        if predictions["late"] or predictions["over_due"]:
            aPredictions.append(
                f":large_orange_circle: *{int(predictions['late']) + int(predictions['over_due'])} {'job' if int(predictions['late']) + int(predictions['over_due']) == 1 else 'jobs'} may be behind schedule"
            )
        status = "*In Progress Jobs*\n" + "\n".join(aPredictions)
    return status


def format_job_prediction(prediction: str, target_date: datetime.datetime) -> str:
    if target_date.tzinfo is None:
        target_date = target_date.replace(tzinfo=datetime.timezone.utc)
    date_delta = target_date - datetime.datetime.now(datetime.timezone.utc)
    if date_delta.total_seconds() < 0 or prediction == "late":
        return ":large_orange_circle: May be tracking behind schedule."
    elif prediction == "on time":
        return ":large_green_circle: Tracking on time"
    else:
        return ""


def is_min_langugagecloud_plan(
    plan: str | None,
    min_plan: Literal["Free", "Essentials", "Growth", "Enterprise"] | None,
) -> bool:
    """Checks if the LanguageCloud subscription plan meets the minimum
    requirements.

    Args:
        plan (str | None): The plan to check
        min_plan: The minimum plan required, e.g. "Essentials", "Growth".

    Returns:
        bool: The plan meets the minimum requirements.
    """
    # TODO Allow all plans until bug (auth/connector.py) is fixed.
    return True
    # if not min_plan or min_plan.lower() == "free":
    #     return True
    # if not plan or plan.lower() == "free":
    #     return False
    # if min_plan.lower() == "essentials":
    #     return plan.lower() in ["essentials", "growth", "enterprise"]
    # if min_plan.lower() == "growth":
    #     return plan.lower() in ["growth", "enterprise"]
    # if min_plan.lower() == "enterprise":
    #     return plan.lower() == "enterprise"
    # # Unknown min plan.
    # return False


def download_from_file_server(file_id: str):
    """Downloads a file from the file server."""

    url = f"{domains.file_api}/files/{file_id}"
    # Download the file.
    response = requests.get(url)
    response.raise_for_status()
    # Get the Content-Disposition header
    content_disposition = response.headers.get("Content-Disposition")

    # Parse the header to get the filename
    value, params = parse_header(content_disposition)
    file_result = {
        "file_name": params.get("filename"),
        "file": BytesIO(response.content),
    }
    return file_result


def upload_to_file_server(file_path: str) -> str:
    """
    Upload the file to sup-file-api and return the file ID.
    """
    file_id = ""
    with open(file_path, "rb") as f:
        # Make the PUT request
        response = requests.put(domains.file_api + "/gridfs", files={"file": f})

    # If the request was successful
    if response.status_code == 200:
        # Parse the response as JSON
        data = response.json()

        # Extract the file ID from the response
        file_id = data.get("id")
        if not file_id:
            raise ValueError("Response does not contain a 'id' field")
    else:
        response.raise_for_status()

    return file_id


def set_user_language(user_info):
    user_locale = "en"
    if "user" in user_info and "locale" in user_info["user"]:
        user_locale = user_info["user"]["locale"]
    if (
        user_info["user"]["tz"] == "America/Chicago"
        or user_info["user"]["tz"] == "America/New_York"
        or user_info["user"]["tz"] == "America/Denver"
        or user_info["user"]["tz"] == "America/Los_Angeles"
        or user_info["user"]["tz"] == "America/Regina"
    ) and user_info["user"]["locale"] == "fr-FR":
        user_locale = "fr-CA"
    translator_var.set(Translator(user_locale))
