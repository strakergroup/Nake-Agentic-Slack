from uuid import uuid4
from urllib.parse import urlparse

import app  # Bug - circular import
from app.config import domains


def test_get_job_url():
    job_id = str(uuid4())
    client_id = str(uuid4())
    url = app.ray.utils.get_job_url(job_id, client_id)
    parsed_url = urlparse(url)
    assert url.startswith(domains.deltaray)
    assert f"j={job_id}" in parsed_url.query
    assert f"member_id={client_id}" in parsed_url.query


def test_get_job_url_no_client_id():
    job_id = str(uuid4())
    url = app.ray.utils.get_job_url(job_id)
    url2 = app.ray.utils.get_job_url(job_id, "")
    parsed_url = urlparse(url)
    assert url.startswith(domains.deltaray)
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
