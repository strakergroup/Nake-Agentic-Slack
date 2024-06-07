from uuid import uuid4
from urllib.parse import urlparse
import datetime

import app  # Bug - circular import
import app.ray.utils
from app.config import domains
from app.translate import _, translator_var, Translator


def test_get_job_url():
    job_id = str(uuid4())
    client_id = str(uuid4())
    url = app.ray.utils.get_job_url(job_id, client_id)
    parsed_url = urlparse(url)
    assert url.startswith(domains.languagecloud)
    assert f"j={job_id}" in parsed_url.query
    assert f"member_id={client_id}" in parsed_url.query


def test_get_job_url_no_client_id():
    job_id = str(uuid4())
    url = app.ray.utils.get_job_url(job_id)
    url2 = app.ray.utils.get_job_url(job_id, "")
    parsed_url = urlparse(url)
    assert url.startswith(domains.languagecloud)
    assert f"j={job_id}" in parsed_url.query
    assert url == url2


def test_format_currency_symbol():
    assert app.ray.utils.format_currency_symbol("USD") == "USD"
    assert app.ray.utils.format_currency_symbol("USD_Other") == "USD"
    assert app.ray.utils.format_currency_symbol("EUR") == "EUR"
    assert app.ray.utils.format_currency_symbol("EUR_DE") == "EUR"
    assert app.ray.utils.format_currency_symbol("NZD") == "NZD"
    assert app.ray.utils.format_currency_symbol("AUD") == "AUD"


def test_format_currency():
    assert app.ray.utils.format_currency(3.5, "USD") == "US$3.50"
    assert app.ray.utils.format_currency(3.5, "USD_Other") == "US$3.50"
    assert app.ray.utils.format_currency(3.5, "NZD") == "NZ$3.50"
    assert app.ray.utils.format_currency(2.5, "EUR") == "€2.50"
    assert app.ray.utils.format_currency(2.501, "GBP") == "£2.50"
    assert app.ray.utils.format_currency(10, "AUD") == "A$10.00"
    assert app.ray.utils.format_currency(-420, "JPY") == "-JP¥420"


def test_format_job_status():
    assert app.ray.utils.format_job_status("LEAD") == "Quote Requested"
    assert app.ray.utils.format_job_status("IN_PROGRESS") == "In Progress"
    assert app.ray.utils.format_job_status("COMPLETED") == "Completed"
    assert app.ray.utils.format_job_status("OTHER_STATUS") == "OTHER_STATUS"


def test_format_datetime_slack():
    naive_date = datetime.datetime.now()
    aware_utc_date = naive_date.replace(tzinfo=datetime.timezone.utc)
    timestamp = int(aware_utc_date.timestamp())
    fallback = naive_date.strftime("%Y-%m-%d %H:%M UTC")
    assert (
        app.ray.utils.format_datetime_slack(naive_date)
        == f"<!date^{timestamp}^{{date}} {{time}}|{fallback}>"
    )
    assert (
        app.ray.utils.format_datetime_slack(aware_utc_date)
        == f"<!date^{timestamp}^{{date}} {{time}}|{fallback}>"
    )


def test_format_job_due_date_slack():
    current_date = datetime.datetime.utcnow()
    tomorrow = current_date + datetime.timedelta(days=1)
    yesterday = current_date - datetime.timedelta(days=1)
    next_week = current_date + datetime.timedelta(days=7)

    # Show "in X hour(s)" if date within 48 hours.
    assert app.ray.utils.format_job_due_date_slack(tomorrow).endswith("hour(s)")
    assert app.ray.utils.format_job_due_date_slack(current_date).endswith(">")
    assert app.ray.utils.format_job_due_date_slack(yesterday).endswith(">")
    assert app.ray.utils.format_job_due_date_slack(next_week).endswith(">")

    # Show traffic lights if status is "IN_PROGRESS".
    assert app.ray.utils.format_job_due_date_slack(
        target_date=yesterday, job_status="IN_PROGRESS", traffic_light=True
    ).startswith("<!date")
    assert app.ray.utils.format_job_due_date_slack(
        target_date=tomorrow, job_status="IN_PROGRESS", traffic_light=True
    ).startswith("in")
    assert not app.ray.utils.format_job_due_date_slack(
        target_date=yesterday, traffic_light=True
    ).startswith(":")
    # Don't show lights if the traffic_light argument is False.
    assert not app.ray.utils.format_job_due_date_slack(
        target_date=yesterday, job_status="IN_PROGRESS", traffic_light=False
    ).startswith(":")


def test_translations():
    translator = translator_var.set(Translator("jp"))
    user_details = "test"
    user_link = "test"
    input = "Your LanguageCloud account {user_details} is now disconnected from {user_link}."
    assert _(input) != input
