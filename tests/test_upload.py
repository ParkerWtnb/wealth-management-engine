# End-to-end tests for the upload pipeline
# Run from the repo root: python -m pytest tests -q

import logging
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src") # main.py lives in src/, not tests/
sys.path.insert(0, SRC)
import main  # noqa: E402

SECRET = "SECRET4111" # Fake private value planted in bad cells. It should never show up in a response, log, or print.

# Load the anonymized sample CSVs once so every test can reuse them
SAMPLE = {}
for filename in main.SAMPLE_FILES:
    with open(os.path.join(SRC, filename), encoding="utf-8") as f:
        SAMPLE[filename] = f.read()

# Dashboard totals the three sample files should always produce
SAMPLE_TOTALS = {"Total Income": 1101.85, "Total Spending": 603.35, "Sent to Wealthsimple": 425.00, "Liquid Cash": 1098.50}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False) # Return the 500 page instead of crashing the test


@pytest.fixture(autouse=True)
def capture_logs(caplog):
    caplog.set_level(logging.DEBUG) # Catch every log message at every level so the leak checks can search them


# Helpers:

def upload(client, files):
    # files is a list of (filename, contents), where contents can be a str or bytes
    payload = []
    for name, body in files:
        if isinstance(body, str):
            body = body.encode()
        payload.append(("files", (name, body, "text/csv")))
    return client.post("/upload", files=payload)


def totals(response):
    # Pull the "Label: $1,234.56" pairs out of the dashboard HTML into a dictionary
    assert response.status_code == 200, response.text
    found = re.findall(r"<strong>([^<]+):</strong> \$([\d,]+\.\d\d)", response.text)
    return {label: float(value.replace(",", "")) for label, value in found}


def sample_files():
    return list(SAMPLE.items())


def chequing_rows():
    return SAMPLE["td_sept_chequing.csv"].splitlines(keepends=True) # Keep the newlines so rows can be joined back into a CSV


def assert_no_leak(response, caplog, capsys):
    # SECRET can't appear anywhere the user or a server admin could see it
    out = capsys.readouterr()
    for where, text in [("response", response.text), ("logs", caplog.text), ("stdout", out.out), ("stderr", out.err)]:
        assert SECRET not in text, f"raw cell value leaked into {where}"


# Routes:

def test_root_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/dashboard"


def test_dashboard_page(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert 'action="/upload"' in r.text and 'action="/sample"' in r.text # Both forms are on the page
    assert "processed strictly in memory" in r.text and "never stored or logged" in r.text # Privacy notice
    assert "Tested on TD and Wealthsimple CSV exports" in r.text
    assert "overlapping date ranges" in r.text # Upload tip


def test_metrics_endpoint_removed(client):
    # /api/metrics used to serve one global set of totals to anyone, so it needs to stay gone
    assert client.get("/api/metrics").status_code == 404


def test_no_wildcard_cors(client):
    # Another website shouldn't be allowed to post files to /upload from a user's browser
    r = client.options("/upload", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


# Totals:

def test_sample_and_upload_give_same_totals(client):
    sample = client.post("/sample")
    assert totals(sample) == SAMPLE_TOTALS
    assert 'TD labels all Wealthsimple transfers as "WS Investments"' in sample.text # Disclaimer under "Sent to Wealthsimple"
    assert totals(upload(client, sample_files())) == SAMPLE_TOTALS # Uploading the same files should match the sample button


def test_requests_do_not_share_state(client):
    upload(client, sample_files()) # Bigger upload first
    only_ws = [("ws.csv", SAMPLE["ws_sept_spend_card.csv"])]
    first = totals(upload(client, only_ws))
    assert first == totals(upload(client, only_ws)) # Same input, same answer
    assert first["Liquid Cash"] == 41.15 # Only the Wealthsimple balance, nothing carried over from the earlier upload


# Deduplication:

# Aug export = rows 0-7, Sept export = rows 5 onward, so rows 5-7 are in both files
@pytest.mark.parametrize("files", [
    pytest.param(lambda: sample_files() * 2, id="everything-twice"),
    pytest.param(lambda: [("chequing_aug.csv", "".join(chequing_rows()[:8])),
                          ("chequing_sep.csv", "".join(chequing_rows()[5:]))] + sample_files()[1:], id="overlap-new-names"),
    pytest.param(lambda: sample_files()[1:] + [("chequing_sep.csv", "".join(chequing_rows()[5:])),
                                               ("chequing_aug.csv", "".join(chequing_rows()[:8]))], id="overlap-reversed"),
])
def test_overlapping_exports_count_once(client, files):
    assert totals(upload(client, files())) == SAMPLE_TOTALS


TWO_COFFEES = '"2026-09-10","COFFEE",4.5,,95.5\n"2026-09-10","COFFEE",4.5,,91\n' # Same day and price, different balance = two real purchases


def test_same_day_coffees_kept_but_not_doubled(client):
    # Both coffees are real, but uploading the file twice shouldn't turn them into four
    assert totals(upload(client, [("a.csv", TWO_COFFEES), ("b.csv", TWO_COFFEES)]))["Total Spending"] == 9.0


def test_keeps_highest_count_from_one_file(client):
    # A blank balance makes identical rows share a key. 3 in one file and 2 in another -> keep 3, not 5
    row = '"2026-09-10","COFFEE",4.5,,\n'
    assert totals(upload(client, [("a.csv", row * 3), ("b.csv", row * 2)]))["Total Spending"] == 13.5


def test_description_spacing_and_case_ignored(client):
    files = [("a.csv", '"2026-09-10","Coffee  Shop",4.5,,95.5\n'), ("b.csv", '"2026-09-10","COFFEE SHOP",4.5,,95.5\n')]
    assert totals(upload(client, files))["Total Spending"] == 4.5


# Scotiabank format:

# Has extra Filter and Sub-description columns, and mixes MM/DD/YYYY with YYYY/MM/DD dates
SCOTIA = (
    '"Filter","Date","Description","Sub-description","Type of Transaction","Amount","Balance"\n'
    '"","11/20/2026","PAYROLL","ACME CORP","Credit","788.95","788.95"\n'
    '"","11/23/2026","GROCERY","LOBLAWS 123","Debit","-211.66","577.29"\n'
    '"","2026/11/24","WS INVESTMENTS","TFR-TO 1234","Debit","-100.00","477.29"\n'
)


def test_scotia_uses_description_not_sub_description():
    rows = main.parse_csv("scotia.csv", SCOTIA)
    assert [t.transaction_desc for t in rows] == ["PAYROLL", "GROCERY", "WS INVESTMENTS"]


def test_scotia_upload_totals(client):
    # Liquid Cash only lands on 477.29 if both date formats parse and sort correctly.
    # Sent to Wealthsimple only gets the $100 if "TFR-TO" in the Sub-description is ignored.
    assert totals(upload(client, [("scotia.csv", SCOTIA)])) == {
        "Total Income": 788.95, "Total Spending": 211.66, "Sent to Wealthsimple": 100.00, "Liquid Cash": 477.29}


# Malformed input:

WS_HEADER = '"date","transaction","description","amount","balance","currency"\n'

# Every file here should get a friendly 400, and SECRET should never leak out
MALFORMED = {
    "bad_date.csv": f'"2026-09-02","Coffee","4.5",,"195.5"\n"{SECRET}","Coffee","4.5",,"191"\n',
    "bad_amount.csv": f'"2026-09-02","Coffee","{SECRET}",,"195.5"\n',
    "nan_amount.csv": '"2026-09-02","Coffee","nan",,"195.5"\n', # float() accepts "nan", the parser shouldn't
    "inf_balance.csv": '"2026-09-02","Coffee","4.5",,"inf"\n',
    "short_row.csv": f'"2026-09-02","{SECRET}","4.5"\n',
    "wrong_header.csv": f"name,email\n{SECRET},someone@example.com\n", # A CSV, just not a bank statement
    "ws_bad_amount.csv": WS_HEADER + f'"2026-09-01","SPEND","Tim","{SECRET}","1","CAD"\n',
    "ws_bad_date.csv": WS_HEADER + f'"{SECRET}","SPEND","Tim","-1","1","CAD"\n',
    "header_only.csv": WS_HEADER,
    "empty.csv": "",
    "binary.csv": b"\xff\xfe\x00\x01" + SECRET.encode(), # Not UTF-8 text
}


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_csv_friendly_error_without_leaks(client, caplog, capsys, name):
    r = upload(client, [(name, MALFORMED[name])])
    assert r.status_code == 400
    assert "look like a bank statement we can read" in r.text
    assert name in r.text # Tells the user which file was the problem
    assert_no_leak(r, caplog, capsys)


def test_bad_file_rejects_whole_upload(client, caplog, capsys):
    # One bad file means no totals at all, since partial totals would be misleading
    r = upload(client, sample_files() + [("bad.csv", MALFORMED["bad_date.csv"])])
    assert r.status_code == 400
    assert "Total Income" not in r.text
    assert_no_leak(r, caplog, capsys)


# File size limits:

def test_upload_cap_stays_below_starlette_spool_threshold():
    from starlette.formparsers import MultiPartParser
    assert main.MAX_FILE_BYTES == 1_000_000
    assert main.MAX_FILE_BYTES < MultiPartParser.spool_max_size # Anything over this gets written to a temp file on disk


def test_oversized_file(client):
    r = upload(client, [("big.csv", b"x" * (main.MAX_FILE_BYTES + 1))])
    assert r.status_code == 413
    assert "That file is too large" in r.text
    assert "1 MB or smaller" in r.text


def test_file_exactly_at_cap_is_accepted(client):
    row = b'"2026-09-10","COFFEE",4.5,,95.5\n'
    body = row + b"\n" * (main.MAX_FILE_BYTES - len(row)) # Pad with blank lines (the parser skips them) to hit exactly 1 MB
    assert len(body) == main.MAX_FILE_BYTES
    assert totals(upload(client, [("edge.csv", body)]))["Total Spending"] == 4.5


# Error pages:

def test_filename_is_html_escaped(client):
    r = upload(client, [("<script>alert(1)</script>.csv", "")]) # Empty file forces an error page that shows the filename
    assert r.status_code == 400
    assert "<script>alert(1)" not in r.text


def test_server_error_has_own_heading_and_no_leak(client, caplog, capsys, monkeypatch):
    def boom(files):
        raise KeyError(SECRET) # Fake bug whose message holds private data
    monkeypatch.setattr(main, "build_report", boom)

    r = upload(client, sample_files())
    assert r.status_code == 500
    assert "Something went wrong on our end" in r.text
    assert "look like a bank statement" not in r.text # Our bug, so don't blame the user's file
    assert "KeyError" in caplog.text # Exception type gets logged for debugging...
    assert_no_leak(r, caplog, capsys) # ...but never its message
