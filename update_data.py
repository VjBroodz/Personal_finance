"""Refresh both pages using NSE snapshots and official daily historical valuations.
The first run downloads five years; later runs refresh the last week and new dates.
Keep nse_valuation_history.json alongside this script. Needs: pip install requests.
Medians use daily positive P/Es, not yearly averages; unavailable/nonpositive readings are excluded.
"""
import calendar
import json
import math
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

NSE = {"Nifty 50": ["NIFTY 50"], "Nifty Next 50": ["NIFTY NEXT 50"],
       "Nifty Midcap 150": ["NIFTY MIDCAP 150"], "Nifty Smallcap 250": ["NIFTY SMALLCAP 250", "NIFTY SMLCAP 250"]}
CAPS = {"Nifty 50": "Large-cap benchmark", "Nifty Next 50": "Upper large caps",
        "Nifty Midcap 150": "Mid-cap benchmark", "Nifty Smallcap 250": "Small-cap benchmark"}
SECTORS = [
    {"name": "Nifty Auto", "index": "NIFTY AUTO", "cap": "Automobiles"},
    {"name": "Nifty Bank", "index": "NIFTY BANK", "cap": "Banks"},
    {"name": "Nifty Financial Services", "index": "NIFTY FINANCIAL SERVICES", "cap": "Financial services"},
    {"name": "Nifty FMCG", "index": "NIFTY FMCG", "cap": "Fast-moving consumer goods"},
    {"name": "Nifty IT", "index": "NIFTY IT", "cap": "Information technology"},
    {"name": "Nifty Pharma", "index": "NIFTY PHARMA", "cap": "Pharmaceuticals"},
    {"name": "Nifty Metal", "index": "NIFTY METAL", "cap": "Metals"},
    {"name": "Nifty Realty", "index": "NIFTY REALTY", "cap": "Real estate"},
    {"name": "Nifty Media", "index": "NIFTY MEDIA", "cap": "Media"},
    {"name": "Nifty PSU Bank", "index": "NIFTY PSU BANK", "cap": "Public sector banks"},
]
ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "nse_valuation_history.json"
SNAPSHOT_URL = "https://www.nseindia.com/api/allIndices"
BANK_CONSTITUENTS_URL = "https://www.nseindia.com/api/equity-stockIndices"
EQUITY_QUOTE_URL = "https://www.nseindia.com/api/quote-equity"
HISTORY_SOURCE = "https://www.niftyindices.com/reports/historical-data"
HISTORY_URL = "https://www.niftyindices.com/BackPage/getpepbHistoricaldataDBtoString"
MAPPING_URL = "https://liveindexsa.niftyindices.com/assets/json/IndexMapping.json"
THRESHOLD_PCT = 10


def create_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"], respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def get_json(session, url, query=None):
    if query is None:
        response = session.get(url, timeout=45)
    else:
        response = session.post(url, json={"cinfo": json.dumps(query)},
                                headers={"Referer": HISTORY_SOURCE}, timeout=45)
    response.raise_for_status()
    return json.loads(response.content.decode("utf-8-sig"))


def five_year_start(end):
    year = end.year - 5
    return date(year, end.month, min(end.day, calendar.monthrange(year, end.month)[1]))


def date_ranges(start, end):
    if start > end:
        raise ValueError("Historical start date cannot be after the end date")
    while start <= end:
        stop = min(start + timedelta(days=365), end)
        yield start, stop
        start = stop + timedelta(days=1)


def normalized_name(name):
    return re.sub(r"\s+", "", name).casefold()


def number(value, context):
    if value is None or str(value).strip().upper() in {"", "-", "--", "NA", "N/A"}:
        return None
    result = float(str(value).replace(",", ""))
    if not math.isfinite(result):
        raise ValueError(f"{context}: non-finite value {value!r}")
    return result


def parse_history(payload, name, trading_name, start, end):
    if not isinstance(payload, list):
        raise ValueError(f"{name}: expected a list of historical valuation records")
    rows = {}
    aliases = {normalized_name(name), normalized_name(trading_name)}
    for raw in payload:
        if normalized_name(raw["Index Name"]) not in aliases:
            raise ValueError(f"{name}: received history for {raw['Index Name']!r}")
        day = datetime.strptime(raw["DATE"], "%d %b %Y").date()
        if not start <= day <= end:
            raise ValueError(f"{name}: historical date {day} is outside {start} to {end}")
        row = {"date": day.isoformat(), "pe": raw["pe"], "pb": raw["pb"],
               "dy": raw["divYield"]}
        for key in ("pe", "pb", "dy"):
            number(row[key], f"{name} {day} {key}")
        if row["date"] in rows and rows[row["date"]] != row:
            raise ValueError(f"{name}: conflicting historical records on {day}")
        rows[row["date"]] = row
    if not rows and (end - start).days > 10:
        raise ValueError(f"{name}: no historical records from {start} to {end}")
    return rows


def refresh_history(session, names, start, end):
    cache = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else None
    if cache and cache["source"] != HISTORY_SOURCE:
        raise ValueError("Historical cache does not identify the official Nifty Indices source")
    mappings = get_json(session, MAPPING_URL)
    history = {}
    for name in names:
        matches = [m for m in mappings if normalized_name(name) in
                   {normalized_name(m["Index_long_name"]), normalized_name(m["Trading_Index_Name"])}]
        if len(matches) != 1:
            raise ValueError(f"{name}: expected exactly one official index mapping")
        trading_name = matches[0]["Trading_Index_Name"]
        old = cache["indices"].get(name, []) if cache else []
        rows = {}
        for row in old:
            if row["date"] in rows:
                raise ValueError(f"{name}: duplicate cached date {row['date']}")
            date.fromisoformat(row["date"])
            rows[row["date"]] = row
        query_start = start
        if old and date.fromisoformat(cache["start"]) <= start:
            query_start = max(start, min(end, date.fromisoformat(cache["through"])) - timedelta(days=7))
        for first, last in date_ranges(query_start, end):
            query = {"name": trading_name.upper(), "indexName": name,
                     "startDate": first.strftime("%d-%b-%Y"), "endDate": last.strftime("%d-%b-%Y")}
            rows.update(parse_history(get_json(session, HISTORY_URL, query),
                                      name, trading_name, first, last))
            time.sleep(0.2)
        history[name] = [rows[day] for day in sorted(rows) if start.isoformat() <= day <= end.isoformat()]
        print(f"Downloaded {name}: {len(history[name])} daily records", flush=True)
    return history


def calculate_medians(history, start, end):
    reference = {row["date"] for row in history["Nifty 50"]}
    days = sorted(date.fromisoformat(day) for day in reference)
    if not days or days[0] > start + timedelta(days=7) or days[-1] < end - timedelta(days=7):
        raise ValueError("Nifty 50 history does not cover the requested five-year period")
    if any((b - a).days > 10 for a, b in zip(days, days[1:])):
        raise ValueError("Nifty 50 historical calendar contains an unexplained gap")
    result = {}
    for name, rows in history.items():
        dates = {row["date"] for row in rows}
        if len(dates) != len(rows):
            raise ValueError(f"{name}: duplicate daily observations would bias the median")
        if dates != reference:
            raise ValueError(f"{name}: historical trading dates differ from Nifty 50 "
                             f"({len(reference - dates)} missing, {len(dates - reference)} extra)")
        values = []
        for row in rows:
            value = number(row["pe"], f"{name} {row['date']} P/E")
            if value is not None and value > 0:
                values.append(Decimal(str(row["pe"]).replace(",", "")))
        if not values:
            raise ValueError(f"{name}: no valid positive historical P/E observations")
        result[name] = {"median": float(median(values)), "medianSamples": len(values),
                        "excludedSamples": len(rows) - len(values)}
        print(f"{name}: median {result[name]['median']:.4f}x from {len(values)} daily P/Es; "
              f"{result[name]['excludedSamples']} unavailable/nonpositive excluded", flush=True)
    return result


def valuation_label(pe, historical_median):
    current = Decimal(str(pe)) * 100
    baseline = Decimal(str(historical_median))
    if current <= baseline * (100 - THRESHOLD_PCT):
        return "UNDERVALUED"
    if current >= baseline * (100 + THRESHOLD_PCT):
        return "OVERVALUED"
    return "FAIRLY VALUED"


def prepare_data_block(html, payload):
    updated, count = re.subn(
        r"/\*DATA_START\*/.*?/\*DATA_END\*/",
        lambda m: "/*DATA_START*/\nconst DATA=" + json.dumps(payload, allow_nan=False) + ";\n/*DATA_END*/",
        html, flags=re.S)
    if count != 1:
        raise ValueError(f"Expected exactly one DATA block; found {count}")
    return updated


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def fetch_bank_stocks(session):
    session.get("https://www.nseindia.com", timeout=45).raise_for_status()
    nse_headers = {"Referer": "https://www.nseindia.com/market-data/live-equity-market"}
    response = session.get(BANK_CONSTITUENTS_URL, params={"index": "NIFTY BANK"},
                           headers=nse_headers, timeout=45)
    response.raise_for_status()
    payload = response.json()
    constituents = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(constituents, list):
        raise ValueError("NIFTY BANK: expected a list of constituent stocks from NSE")

    stocks = []
    seen = set()
    for raw in constituents:
        symbol = raw.get("symbol")
        if not symbol or symbol == "NIFTY BANK":
            continue
        if symbol in seen:
            raise ValueError(f"NIFTY BANK: duplicate constituent {symbol}")
        seen.add(symbol)

        cmp = number(raw.get("lastPrice"), f"{symbol} current CMP")
        if cmp is None or cmp <= 0:
            raise ValueError(f"{symbol}: current CMP is unavailable")

        quote_response = session.get(EQUITY_QUOTE_URL, params={"symbol": symbol},
                                     headers=nse_headers, timeout=45)
        quote_response.raise_for_status()
        quote = quote_response.json()
        metadata = quote.get("metadata", {}) if isinstance(quote, dict) else {}
        quote_info = quote.get("info") or {}
        quote_symbol = metadata.get("symbol") or quote_info.get("symbol")
        if quote_symbol != symbol:
            raise ValueError(f"{symbol}: NSE returned quote data for {quote_symbol!r}")
        pe = number(metadata.get("pdSymbolPe"), f"{symbol} P/E")
        if pe == 0:
            pe = None

        meta = raw.get("meta") or {}
        company = meta.get("companyName") or raw.get("companyName") or symbol
        stocks.append({"symbol": symbol, "name": company, "cmp": cmp, "pe": pe})

    if not stocks:
        raise ValueError("NIFTY BANK: NSE returned no constituent stocks")
    return stocks


def main():
    with create_session() as session:
        snapshot = get_json(session, SNAPSHOT_URL)
        as_of = datetime.strptime(snapshot["timestamp"], "%d-%b-%Y %H:%M").date()
        start = five_year_start(as_of)
        names = list(NSE) + [sector["name"] for sector in SECTORS]
        history = refresh_history(session, names, start, as_of)
        bank_stocks = fetch_bank_stocks(session)
    medians = calculate_medians(history, start, as_of)
    rows = {row["index"]: row for row in snapshot["data"]}

    def current_row(name, keys, cap):
        matched = [rows[key] for key in keys if key in rows]
        if len(matched) != 1:
            raise ValueError(f"{name}: expected exactly one current NSE index")
        raw = matched[0]
        result = {"name": name, "cap": cap, **medians[name]}
        for key in ("pe", "pb", "dy"):
            value = number(raw[key], f"{name} current {key}")
            if value is None or value < 0 or (key == "pe" and value == 0):
                raise ValueError(f"{name}: current {key} is unavailable")
            result[key] = value
        result["label"] = valuation_label(result["pe"], result["median"])
        return result, raw

    indices = [current_row(name, keys, CAPS[name])[0] for name, keys in NSE.items()]
    sectors = []
    for sector in SECTORS:
        row, raw = current_row(sector["name"], [sector["index"]], sector["cap"])
        for key, field in (("level", "last"), ("change", "variation"), ("changePct", "percentChange")):
            value = number(raw[field], f"{sector['name']} current {field}")
            if value is None:
                raise ValueError(f"{sector['name']}: current {field} is unavailable")
            row[key] = value
        sectors.append(row)
    common = {"asOf": as_of.isoformat(), "source": "NSE India and Nifty Indices historical valuations",
              "thresholdPct": THRESHOLD_PCT, "historyStart": start.isoformat(), "historyEnd": as_of.isoformat(),
              "historySource": HISTORY_SOURCE}
    pages = {
        ROOT / "index.html": {**common, "indices": indices},
        ROOT / "sectors.html": {**common, "sectors": sectors},
        ROOT / "bank_stocks.html": {**common, "stocks": bank_stocks},
    }
    prepared = {path: prepare_data_block(path.read_text(encoding="utf-8"), payload)
                for path, payload in pages.items()}
    cache = {"source": HISTORY_SOURCE, "start": start.isoformat(), "through": as_of.isoformat(),
             "downloadedAt": datetime.now(timezone.utc).isoformat(), "indices": history}
    cache_text = json.dumps(cache, allow_nan=False, separators=(",", ":")) + "\n"
    atomic_write(HISTORY, cache_text)
    for path, content in prepared.items():
        atomic_write(path, content)
    print(f"Updated pages: {start} to {as_of}")


if __name__ == "__main__":
    main()
