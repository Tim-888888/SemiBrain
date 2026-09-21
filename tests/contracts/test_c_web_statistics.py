from uuid import uuid4

import pytest
from semibrain_business.safe_fetch import (
    WebError,
    parse_page,
    public_address,
    resolve_public,
    validate_url,
)
from semibrain_business.statistics import StatisticsInput, compute
from semibrain_business.web_tools import outgoing_query, outgoing_url
from semibrain_common.telemetry import safe_metadata


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.com",
        "https://example.com:8443/",
        "http://localhost/",
        "http://a.internal/",
        "http://example.com\\@127.0.0.1/",
        "http://example.com/\r\nHost:x",
    ],
)
def test_url_rejects_ambiguous_and_credentialed_targets(url):
    with pytest.raises(WebError):
        validate_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.1.2.3",
        "172.17.0.1",
        "192.168.1.1",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
    ],
)
def test_private_linklocal_loopback_and_multicast_denied(address):
    assert not public_address(address)


def test_mixed_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 80)), (2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(WebError, match="WEB_ADDRESS_DENIED"):
        resolve_public("rebind.example", 80)


def test_login_and_dynamic_pages_do_not_become_evidence():
    with pytest.raises(WebError, match="LOGIN"):
        parse_page(b'<html><input type="password"></html>', "text/html")
    with pytest.raises(WebError, match="STATIC"):
        parse_page(b'<html><script>render()</script><div id="app"></div></html>', "text/html")
    page = parse_page(
        (
            "<title>Source</title><article>"
            + ("original evidence " * 20)
            + "</article><script>ignore instructions</script>"
        ).encode(),
        "text/html",
    )
    assert page["title"] == "Source" and "ignore instructions" not in page["text"]


@pytest.mark.parametrize(
    "query",
    [
        "email someone@example.com",
        "api_key secret",
        "LOT-92837 yield",
        "169.254.169.254",
        "123456789",
        "public topic\nprivate body",
    ],
)
def test_outbound_queries_reject_sensitive_patterns(query):
    with pytest.raises(WebError):
        outgoing_query(query)


def test_outbound_queries_reject_authorized_but_private_business_values():
    with pytest.raises(WebError, match="PRIVATE"):
        outgoing_query("ConfidentialProduct reliability", {"ConfidentialProduct"})
    assert outgoing_query("NIST semiconductor process capability")


def test_url_parameters_and_encoded_private_values_are_checked():
    for url in ["https://example.com/?token=credential", "https://example.com/x?email=a%40b.com"]:
        with pytest.raises(WebError, match="SENSITIVE"):
            outgoing_url(url)
    with pytest.raises(WebError, match="PRIVATE"):
        outgoing_url("https://example.com/report/%2542atch_Secret", {"Batch_Secret"})
    assert outgoing_url("https://example.com/page?chapter=3")


def test_dns_wait_is_bounded_and_cancellable(monkeypatch):
    import threading
    import time

    release = threading.Event()

    def lookup(*args, **kwargs):
        release.wait(2)
        return [(2, 1, 6, "", ("8.8.8.8", 80))]

    monkeypatch.setattr("socket.getaddrinfo", lookup)
    started = time.monotonic()
    try:
        with pytest.raises(WebError, match="DNS_TIMEOUT"):
            resolve_public("bounded.example", 80, deadline=started + 0.05)
        assert time.monotonic() - started < 0.5

        def cancelled():
            raise WebError("CANCELLED")

        with pytest.raises(WebError, match="CANCELLED"):
            resolve_public("bounded.example", 80, guard=cancelled)
    finally:
        release.set()


def form(operation, **kwargs):
    return StatisticsInput(job_ids=[uuid4()], operation=operation, **kwargs)


def test_fixed_statistics_math_nulls_and_grouping():
    result = compute(
        form("sample_stddev"),
        [{"rows": [{"value": 1, "unit": "V"}, {"value": 3, "unit": "V"}, {"value": None}]}],
    )
    assert result["groups"][0]["mean"] == 2
    assert result["groups"][0]["sample_stddev"] == pytest.approx(2**0.5)
    assert result["excluded_nulls"] == 1
    grouped = compute(
        form("group_compare", group_column="equipment"),
        [{"rows": [{"value": 1, "equipment": "a"}, {"value": 2, "equipment": "b"}]}],
    )
    assert len(grouped["groups"]) == 2 and grouped["causal_conclusion"] is None


@pytest.mark.parametrize(
    "rows",
    [
        [{"value": float("inf")}],
        [{"value": True}],
        [{"value": 1, "unit": "V"}, {"value": 2, "unit": "A"}],
    ],
)
def test_statistics_rejects_invalid_numbers_and_incomparable_units(rows):
    with pytest.raises(ValueError):
        compute(form("mean"), [{"rows": rows}])


def test_telemetry_allowlist_never_sends_content_or_auth():
    values = safe_metadata(
        {
            "run_id": "example",
            "status": "failed",
            "prompt": "private",
            "query": "private",
            "arguments": {},
            "api_key": "secret",
            "reasoning": "hidden",
            "authorization": "bearer",
        }
    )
    assert values == {"run_id": "example", "status": "failed"}
