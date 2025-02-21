from cgi import parse_header
from typing import Literal, Tuple, Union
import math
import datetime
from urllib.parse import urlencode, unquote
import os

from buglog import notify_exception

from app.auth.connector import is_ibm_super_group
from babel.numbers import format_currency as babel_format_currency
import requests
from app.translate import _

from app.translate import Translator, translator_var

from .file_validators import validate_json
from ..config import domains
from io import BytesIO
import ffmpeg


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
            return _("Quote Requested")
        case "IN_PROGRESS":
            return _("In Progress")
        case "VALIDATION":
            return _("In Validation")
        case "CANCELLED":
            return _("Cancelled")
        case "CLIENT_CANCELLED":
            return _("Client Cancelled")
        case "CLOSED":
            return _("Closed")
        case "WAITING":
            return _("Waiting")
        case "REFUNDED":
            return _("Refunded")
        case "COMPLETED":
            return _("Completed")
        # Derived statuses.
        case "PENDING_QUOTES":  # status = "LEAD" + quote <= 1
            return _("Quote Requested")
        case "ORDER_NOW":  # status = "LEAD" + quote > 1
            return _("Order Now")
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
    hourtime = math.ceil(date_delta.total_seconds() / 3600)
    formatted_date = (
        _("in {hourtime} hour(s)")
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


def get_filename_from_header(header):
    """
    Extract filename from content-disposition header
    """
    value, params = parse_header(header)
    filename = params.get("filename*")
    if filename:
        encoding, _, filename = filename.split("'", 2)
        filename = unquote(filename, encoding=encoding)
    else:
        filename = params.get("filename")
    return filename


def download_from_file_server(file_id: str):
    """Downloads a file from the file server."""

    url = f"{domains.file_api}/files/{file_id}"
    # Download the file.
    response = requests.get(url)
    response.raise_for_status()
    # Get the Content-Disposition header
    content_disposition = response.headers.get("Content-Disposition")

    # Parse the header to get the filename
    filename = get_filename_from_header(content_disposition)
    file_result = {
        "file_name": filename,
        "file": BytesIO(response.content),
    }
    return file_result


def delete_from_file_server(file_id: str):
    """Deletes a file from the file server."""
    url = f"{domains.file_api}/files/{file_id}"
    response = requests.delete(url)
    response.raise_for_status()


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


def set_user_language(user_info, context=None):
    user_locale = "en"
    if "user" in user_info and "locale" in user_info["user"]:
        user_locale = user_info["user"]["locale"]
    if user_locale == "fr-FR":
        if (
            user_info["user"]["tz"] == "America/Chicago"
            or user_info["user"]["tz"] == "America/New_York"
            or user_info["user"]["tz"] == "America/Denver"
            or user_info["user"]["tz"] == "America/Los_Angeles"
            or user_info["user"]["tz"] == "America/Regina"
            or user_info["user"]["tz"] == "America/Halifax"
        ):
            user_locale = "fr-CA"
    translator_var.set(Translator(user_locale))
    if context:
        context["locale"] = user_locale


# TODO: Maybe add to middleware
def is_ibm_enterprise(
    enterprise_id: str | None,
):
    """Check if the user is in ibm enterpirse or workspace."""
    if not enterprise_id:
        return False
    # if config.environment == Environment.production:
    e_id = "EUJJ37YFR"
    t_id = "T0360HUQKS9"
    # else:
    se_id = "E04RDMG8XP1"
    st_id = "T02FDFCGK"
    try:
        # TODO: Maybe add to middleware
        if is_ibm_super_group(enterprise_id):
            return True
    except Exception as e:
        print(f"Error checking ibm group{e}")
    return False


VALID_FILE_TYPES = {
    "csv": None,
    "dita": None,
    "docx": None,
    "html": None,
    "idml": None,
    "json": validate_json,
    "pptx": None,
    "properties": None,
    "srt": None,
    "strings": None,
    "ts": None,
    "txt": None,
    "vtt": None,
    "xlf": None,
    "xliff": None,
    "xlsx": None,
    "xml": None,
    "text": None,
}


def validate_file_type(filename: str) -> bool:
    """Validate the file type."""
    other, ext = os.path.splitext(filename)
    ext = ext.lower().lstrip(".")
    return ext in VALID_FILE_TYPES


def validate_file(file_path: str) -> Tuple[bool, bool, str]:
    """Checks if the file type is supported and validates content if applicable.

    Args:
        file_extension (str): The file extension to validate (with or without leading dot)
        content (Union[bytes, str, None]): The file content to validate if applicable

    Returns:
        Tuple[bool, bool, str]: A tuple containing:
            - bool: Whether the file extension is supported
            - bool: Whether the content is valid (True if no validation required)
            - str: Error message if validation fails, empty string otherwise

    Example:
        >>> validate_file('file.txt')
        (True, True, '')  # If txt is supported with no specific validation
        >>> validate_file('file.unsupported')
        (False, False, 'Unsupported file type: unsupported')
    """
    # Extract extension from file path
    other, ext = os.path.splitext(file_path)
    ext = ext.lower().lstrip(".")
    # Check if file extension is valid
    if ext not in VALID_FILE_TYPES:
        return False, False, f"Unsupported file type: {ext}"

    # Get the corresponding validator function (if any)
    content_validator = VALID_FILE_TYPES[ext]
    if content_validator:
        is_valid, error_message = content_validator(file_path)
        return True, is_valid, error_message  # Return content validation results

    return (
        True,
        True,
        "",
    )  # Supported file type but no specific content validation needed


def get_media_duration(download_url: str, token: str) -> int:
    """Fetch the duration of the media file using ffprobe."""
    try:
        probe = ffmpeg.probe(download_url, headers=f"Authorization: Bearer {token}\n")
        duration = float(probe["format"]["duration"])
        return int(duration * 1000)
    except ffmpeg.Error as e:
        notify_exception(e.stderr, "Failed to get media duration")
        return 0
