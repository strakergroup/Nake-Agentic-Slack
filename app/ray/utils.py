import asyncio
import datetime
import math
import os
import tempfile
from cgi import parse_header
from typing import Callable, Tuple
from urllib.parse import unquote, urlencode

import ffmpeg
import httpx
from babel.numbers import format_currency as babel_format_currency

from app.auth.connector import is_ibm_super_group
from app.constants import DEFAULT_UPLOAD_EXPIRY_DAYS, FILE_TRANSFER_TIMEOUT
from app.ray.file_validators import validate_json
from app.slack.buglog_notifier import notify_exception
from app.translate import Translator, _, translator_var

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


def get_filename_from_header(header):
    """
    Extract filename from content-disposition header and normalize it.
    """
    value, params = parse_header(header)
    filename = params.get("filename*")
    if filename:
        encoding, _, filename = filename.split("'", 2)
        filename = unquote(filename, encoding=encoding)
    else:
        filename = params.get("filename")
    # Normalize filename if it ends with 'quebecois'
    return replace_quebecois_with_french_canadian(filename) if filename else filename


async def download_from_file_server_async(file_id: str):
    """Downloads a file from the file server using async streaming to avoid loading entire file into memory."""
    url = f"{domains.file_api}/files/{file_id}"

    async with httpx.AsyncClient(timeout=FILE_TRANSFER_TIMEOUT) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()

            # Get the Content-Disposition header
            content_disposition = response.headers.get("Content-Disposition")
            filename = get_filename_from_header(content_disposition)

            # Create a temporary file to avoid loading entire file into memory
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
            try:
                # Stream content directly to temp file in chunks
                chunk_size = 8192  # 8KB chunks for better memory management
                async for chunk in response.aiter_bytes(chunk_size):
                    temp_file.write(chunk)
                temp_file.close()

                return {
                    "file_name": filename,
                    "file": temp_file.name,  # Return file path instead of BytesIO
                }
            except Exception:
                # Clean up temp file on error
                if os.path.exists(temp_file.name):
                    os.unlink(temp_file.name)
                raise


async def delete_from_file_server(file_id: str):
    """Deletes a file from the file server."""
    url = f"{domains.file_api}/files/{file_id}"
    async with httpx.AsyncClient(timeout=FILE_TRANSFER_TIMEOUT) as client:
        response = await client.delete(url)
        response.raise_for_status()


async def upload_to_file_server(
    file_path: str,
    expires_days: int = DEFAULT_UPLOAD_EXPIRY_DAYS,
) -> str:
    """Upload the file to sup-file-api and return the file ID.

    Args:
        file_path: Local path to the file to upload.
        expires_days: Number of days until the uploaded file expires.
            Defaults to ``DEFAULT_UPLOAD_EXPIRY_DAYS`` (30 days).

    Returns:
        The file metadata ID from sup-file-api.
    """
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=expires_days
    )

    file_id = ""
    with open(file_path, "rb") as f:
        async with httpx.AsyncClient(timeout=FILE_TRANSFER_TIMEOUT) as client:
            response = await client.put(
                domains.file_api + "/gridfs",
                files={"file": f},
                data={"expires_at": expires_at.isoformat()},
            )

    if response.status_code == 200:
        data = response.json()
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


VALID_FILE_TYPES: dict[str, Callable[[str], Tuple[bool, str]] | None] = {
    "csv": None,
    "dita": None,
    "docx": None,
    "html": None,
    "idml": None,
    "json": validate_json,
    "pdf": None,
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


def replace_quebecois_with_french_canadian(filename: str) -> str:
    """Replace 'quebecois' with 'french canadian' in filename if it ends with 'quebecois'.

    Args:
        filename: The filename to process.

    Returns:
        The filename with 'quebecois' replaced by 'french canadian' if it ends with 'quebecois',
        otherwise the original filename.
    """
    if not filename:
        return filename

    # Split filename into base name and extension
    name, ext = os.path.splitext(filename)
    name_lower = name.lower()

    # Check if the base name (without extension) ends with 'quebecois' (case-insensitive)
    if name_lower.endswith("quebecois"):
        # Find the position where 'quebecois' starts (case-insensitive)
        idx = name_lower.rfind("quebecois")
        if idx != -1:
            # Replace 'quebecois' with 'french canadian', preserving the rest and extension
            return name[:idx] + "french canadian" + name[idx + len("quebecois") :] + ext

    return filename


def validate_file(
    file_path: str, max_pdf_size_bytes: int | None = None
) -> Tuple[bool, bool, str]:
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
    _file_root, ext = os.path.splitext(file_path)
    ext = ext.lower().lstrip(".")
    # Check if file extension is valid
    if ext not in VALID_FILE_TYPES:
        return False, False, f"Unsupported file type: {ext}"

    if ext == "pdf" and max_pdf_size_bytes is not None:
        file_size = os.path.getsize(file_path)
        if file_size > max_pdf_size_bytes:
            max_size_mb = max_pdf_size_bytes / 1048576
            file_name = os.path.basename(file_path)
            return (
                True,
                False,
                _(
                    f"*{file_name}* exceeds the current PDF limit of {max_size_mb:.0f}MB "
                    f"(~{file_size / 1048576:.1f} MiB). "
                    "Please compress and re-upload according to the current limit."
                ),
            )

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


async def get_media_duration(download_url: str, token: str) -> int:
    """Fetch the duration of the media file using ffprobe."""
    try:
        probe = await asyncio.to_thread(
            ffmpeg.probe, download_url, headers=f"Authorization: Bearer {token}\n"
        )
        duration = float(probe["format"]["duration"])
        return int(duration * 1000)
    except ffmpeg.Error as e:
        notify_exception(e.stderr, "Failed to get media duration")
        return 0
