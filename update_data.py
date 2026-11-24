"""Daily refresh: pulls NSE valuations and rewrites the DATA blocks in both pages.
Broad-market medians are maintained manually in MEDIANS; sector history is unavailable.
Needs: pip install requests"""
import json, re, datetime, requests
MEDIANS = {"Nifty 50": 21.9, "Nifty Next 50": 22.4, "Nifty Midcap 150": 29.4, "Nifty Smallcap 250": 28.3}
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
HTML = "index.html"
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
s.get("https://www.nseindia.com", timeout=20)  # sets cookies
rows = {r["index"]: r for r in s.get("https://www.nseindia.com/api/allIndices", timeout=20).json()["data"]}
out = []
for name, keys in NSE.items():
    r = next(rows[k] for k in keys if k in rows)
    out.append({"name": name, "cap": CAPS[name], "pe": float(r["pe"]), "median": MEDIANS[name],
                "pb": float(r["pb"]), "dy": float(r["dy"])})
sector_rows = []
for sector in SECTORS:
    r = rows[sector["index"]]
    sector_rows.append({"name": sector["name"], "cap": sector["cap"], "pe": float(r["pe"]),
                        "pb": float(r["pb"]), "dy": float(r["dy"]), "level": float(r["last"]),
                        "change": float(r["variation"]), "changePct": float(r["percentChange"])})

as_of = datetime.date.today().isoformat()
data = {"asOf": as_of, "source": "NSE index data", "thresholdPct": 10, "indices": out}
sector_data = {"asOf": as_of, "source": "NSE India live index data", "sectors": sector_rows}

def update_data_block(path, payload):
    with open(path, encoding="utf-8") as file:
        html = file.read()
    updated, count = re.subn(
        r"/\*DATA_START\*/.*?/\*DATA_END\*/",
        lambda m: "/*DATA_START*/\nconst DATA=" + json.dumps(payload) + ";\n/*DATA_END*/",
        html, flags=re.S)
    if count != 1:
        raise ValueError(f"{path} must contain exactly one DATA block; found {count}")
    with open(path, "w", encoding="utf-8") as file:
        file.write(updated)

update_data_block(HTML, data)
update_data_block("sectors.html", sector_data)
print("updated", as_of)
