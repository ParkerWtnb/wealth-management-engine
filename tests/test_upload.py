"""End-to-end tests for the upload pipeline. Run from the repo root:  python -m pytest tests -q"""
import logging
import os
import re
import sys
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)
import main  # noqa: E402

SECRET = "SECRET4111"  # Planted in malformed cells; must never reach a response, log or stdout
SAMPLE = {name: open(os.path.join(SRC, name), encoding="utf-8").read() for name in main.SAMPLE_FILES}
SAMPLE_TOTALS = {"Total Income": 1101.85, "Total Spending": 603.35, "Sent to Wealthsimple": 425.00, "Liquid Cash": 1098.50}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def capture_everything(caplog):
    caplog.set_level(logging.DEBUG)  # Capture every logger at every level for the leak checks


def upload(client, files):
    """files: list of (filename, str | bytes)."""
    payload = [("files", (name, body if isinstance(body, bytes) else body.encode(), "text/csv")) for name, body in files]
    return client.post("/upload", files=payload)


def totals(response):
    assert response.status_code == 200, response.text
    found = re.findall(r"<strong>([^<]+):</strong> \$([\d,]+\.\d\d)", response.text)
    return {label: float(value.replace(",", "")) for label, value in found}


def sample_files():
    return list(SAMPLE.items())


def chequing_rows():
    return SAMPLE["td_sept_chequing.csv"].splitlines(keepends=True)


# --- Routes -----------------------------------------------------------------

def test_root_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/dashboard"


def test_dashboard_shows_form_notices_and_tip(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert 'action="/upload"' in r.text and 'action="/sample"' in r.text
    assert "processed strictly in memory" in r.text and "never stored or logged" in r.text
    assert "Tested on TD and Wealthsimple CSV exports" in r.text
    assert "overlapping date ranges" in r.text


def test_metrics_endpoint_removed(client):
    assert client.get("/api/metrics").status_code == 404


def test_no_wildcard_cors(client):
    r = client.options("/upload", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_sample_endpoint_totals(client):
    r = client.post("/sample")
    assert totals(r) == SAMPLE_TOTALS
    assert 'TD labels all Wealthsimple transfers as "WS Investments"' in r.text


def test_upload_matches_sample(client):
    assert totals(upload(client, sample_files())) == SAMPLE_TOTALS


def test_requests_do_not_share_state(client):
    upload(client, sample_files())
    only_ws = [("ws.csv", SAMPLE["ws_sept_spend_card.csv"])]
    first = totals(upload(client, only_ws))
    assert first == totals(upload(client, only_ws))
    assert first["Liquid Cash"] == 41.15  # Nothing left over from the earlier, larger upload


# --- Deduplication ----------------------------------------------------------

@pytest.mark.parametrize("files", [
    pytest.param(lambda: sample_files() + [("td_sept_chequing (1).csv", SAMPLE["td_sept_chequing.csv"])], id="same-file-twice"),
    pytest.param(lambda: sample_files() * 2, id="everything-twice"),
    pytest.param(lambda: [("chequing_aug.csv", "".join(chequing_rows()[:8])),
                          ("chequing_sep.csv", "".join(chequing_rows()[5:]))] + sample_files()[1:], id="overlap-new-names"),
    pytest.param(lambda: sample_files()[1:] + [("chequing_sep.csv", "".join(chequing_rows()[5:])),
                                               ("chequing_aug.csv", "".join(chequing_rows()[:8]))], id="overlap-reversed"),
])
def test_overlapping_exports_count_once(client, files):
    assert totals(upload(client, files())) == SAMPLE_TOTALS


TWO_COFFEES = '"2026-09-10","COFFEE",4.5,,95.5\n"2026-09-10","COFFEE",4.5,,91\n'


def test_same_day_identical_coffees_are_kept(client):
    assert totals(upload(client, [("a.csv", TWO_COFFEES)]))["Total Spending"] == 9.0


def test_same_day_coffees_in_two_overlapping_files_count_once(client):
    assert totals(upload(client, [("a.csv", TWO_COFFEES), ("b.csv", TWO_COFFEES)]))["Total Spending"] == 9.0


def test_counter_keeps_max_per_file_count(client):
    # Blank balances make identical rows share a key: 3 in one file, 2 in the other -> keep 3, not 5
    row = '"2026-09-10","COFFEE",4.5,,\n'
    assert totals(upload(client, [("a.csv", row * 3)]))["Total Spending"] == 13.5
    assert totals(upload(client, [("a.csv", row * 3), ("b.csv", row * 2)]))["Total Spending"] == 13.5


def test_description_spacing_and_case_ignored(client):
    files = [("a.csv", '"2026-09-10","Coffee  Shop",4.5,,95.5\n'), ("b.csv", '"2026-09-10","COFFEE SHOP",4.5,,95.5\n')]
    assert totals(upload(client, files))["Total Spending"] == 4.5


# --- Scotiabank format -------------------------------------------------------

SCOTIA = (
    '"Filter","Date","Description","Sub-description","Type of Transaction","Amount","Balance"\n'
    '"","11/20/2026","PAYROLL","ACME CORP","Credit","788.95","788.95"\n'
    '"","11/23/2026","GROCERY","LOBLAWS 123","Debit","-211.66","577.29"\n'
    '"","2026/11/24","WS INVESTMENTS","TFR-TO 1234","Debit","-100.00","477.29"\n'
)


def test_scotia_slash_dates_and_sub_description():
    rows = main.parse_csv("scotia.csv", SCOTIA)
    assert [t.date.strftime("%Y-%m-%d") for t in rows] == ["2026-11-20", "2026-11-23", "2026-11-24"]
    assert [t.transaction_desc for t in rows] == ["PAYROLL", "GROCERY", "WS INVESTMENTS"]  # Never the Sub-description
    assert (rows[1].withdrawal, rows[1].balance) == (Decimal("211.66"), Decimal("577.29"))
    assert rows[2].account_type == "ws_investments"  # Classified by Description, not the "TFR-TO" Sub-description


def test_scotia_upload_totals(client):
    assert totals(upload(client, [("scotia.csv", SCOTIA)])) == {
        "Total Income": 788.95, "Total Spending": 211.66, "Sent to Wealthsimple": 100.00, "Liquid Cash": 477.29}


# --- Money is exact Decimal -------------------------------------------------

def test_amounts_are_exact_decimals():
    rows = "".join(f'"2026-09-{d:02}","INTEREST",,0.10,{d / 10:.2f}\n' for d in range(1, 11))
    report = main.build_report([("savings.csv", rows)])
    assert report.total_income == Decimal("1.00")  # Ten float 0.1s would sum to 0.9999999999999999
    assert all(isinstance(v, Decimal) for v in (report.total_income, report.total_spending,
                                                report.total_ws_investments, report.liquid_cash))


def test_empty_totals_are_decimal_zero():
    empty = main.FinancialTotals()
    assert isinstance(empty.liquid_cash, Decimal) and empty.liquid_cash == 0


# --- Malformed input --------------------------------------------------------

WS_HEADER = '"date","transaction","description","amount","balance","currency"\n'
MALFORMED = {
    "bad_date.csv": f'"2026-09-02","Coffee","4.5",,"195.5"\n"{SECRET}","Coffee","4.5",,"191"\n',
    "bad_amount.csv": f'"2026-09-02","Coffee","{SECRET}",,"195.5"\n',
    "nan_amount.csv": '"2026-09-02","Coffee","nan",,"195.5"\n',
    "inf_balance.csv": '"2026-09-02","Coffee","4.5",,"inf"\n',
    "huge_amount.csv": '"2026-09-02","Coffee","1e30",,"195.5"\n',
    "snan_amount.csv": '"2026-09-02","Coffee","sNaN",,"195.5"\n',
    "short_row.csv": f'"2026-09-02","{SECRET}","4.5"\n',
    "wrong_header.csv": f"name,email\n{SECRET},someone@example.com\n",
    "ws_bad_amount.csv": WS_HEADER + f'"2026-09-01","SPEND","Tim","{SECRET}","1","CAD"\n',
    "ws_bad_date.csv": WS_HEADER + f'"{SECRET}","SPEND","Tim","-1","1","CAD"\n',
    "header_only.csv": WS_HEADER,
    "empty.csv": "",
    "binary.csv": b"\xff\xfe\x00\x01" + SECRET.encode(),
}


def assert_no_leak(response, caplog, capsys):
    out = capsys.readouterr()
    for where, text in [("response", response.text), ("logs", caplog.text), ("stdout", out.out), ("stderr", out.err)]:
        assert SECRET not in text, f"raw cell value leaked into {where}"


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_csv_friendly_error_without_leaks(client, caplog, capsys, name):
    r = upload(client, [(name, MALFORMED[name])])
    assert r.status_code == 400
    assert "look like a bank statement we can read" in r.text
    assert name in r.text  # Tells the user which file was the problem
    assert_no_leak(r, caplog, capsys)


def test_bad_file_rejects_whole_upload(client, caplog, capsys):
    r = upload(client, sample_files() + [("bad.csv", MALFORMED["bad_date.csv"])])
    assert r.status_code == 400
    assert "Total Income" not in r.text
    assert_no_leak(r, caplog, capsys)


def test_upload_cap_stays_below_starlette_spool_threshold():
    from starlette.formparsers import MultiPartParser
    assert main.MAX_FILE_BYTES == 1_000_000
    assert main.MAX_FILE_BYTES < MultiPartParser.spool_max_size  # Accepted files never roll over to disk


def test_oversized_file(client):
    r = upload(client, [("big.csv", b"x" * (main.MAX_FILE_BYTES + 1))])
    assert r.status_code == 413
    assert "That file is too large" in r.text
    assert "1 MB or smaller" in r.text


def test_file_exactly_at_cap_is_accepted(client):
    row = b'"2026-09-10","COFFEE",4.5,,95.5\n'
    body = row + b"\n" * (main.MAX_FILE_BYTES - len(row))  # Blank lines are skipped by the parser
    assert len(body) == main.MAX_FILE_BYTES
    assert totals(upload(client, [("edge.csv", body)]))["Total Spending"] == 4.5


def test_filename_is_html_escaped(client):
    r = upload(client, [("<script>alert(1)</script>.csv", "")])
    assert r.status_code == 400
    assert "<script>alert(1)" not in r.text


def test_server_error_has_own_heading_and_no_leak(client, caplog, capsys, monkeypatch):
    def boom(files):
        raise KeyError(SECRET)
    monkeypatch.setattr(main, "build_report", boom)
    r = upload(client, sample_files())
    assert r.status_code == 500
    assert "Something went wrong on our end" in r.text
    assert "look like a bank statement" not in r.text  # Don't blame the user's file for our bug
    assert "KeyError" in caplog.text  # Exception type is logged for debugging...
    assert_no_leak(r, caplog, capsys)  # ...but never its message
