"""Daily refresh: pulls P/E, P/B, dividend yield from NSE and rewrites the DATA block
in index.html. Medians are yours to maintain (MEDIANS below; refresh quarterly).
Untested against live NSE; NSE may block bots or rename indices, so check the first run.
Needs: pip install requests"""
import json, re, datetime, requests
MEDIANS = {"Nifty 50": 21.9, "Nifty Next 50": 22.4, "Nifty Midcap 150": 29.4, "Nifty Smallcap 250": 28.3}
NSE = {"Nifty 50": ["NIFTY 50"], "Nifty Next 50": ["NIFTY NEXT 50"],
       "Nifty Midcap 150": ["NIFTY MIDCAP 150"], "Nifty Smallcap 250": ["NIFTY SMALLCAP 250", "NIFTY SMLCAP 250"]}
CAPS = {"Nifty 50": "Large-cap benchmark", "Nifty Next 50": "Upper large caps",
        "Nifty Midcap 150": "Mid-cap benchmark", "Nifty Smallcap 250": "Small-cap benchmark"}
HTML = "index.html"
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
s.get("https://www.nseindia.com", timeout=20)  # sets cookies
rows = {r["index"]: r for r in s.get("https://www.nseindia.com/api/allIndices", timeout=20).json()["data"]}
out = []
for name, keys in NSE.items():
    r = next(rows[k] for k in keys if k in rows)
    out.append({"name": name, "cap": CAPS[name], "pe": float(r["pe"]), "median": MEDIANS[name],
                "pb": float(r["pb"]), "dy": float(r["dy"])})
data = {"asOf": datetime.date.today().isoformat(), "source": "NSE index data", "thresholdPct": 10, "indices": out}
html = open(HTML, encoding="utf-8").read()
html = re.sub(r"/\*DATA_START\*/.*?/\*DATA_END\*/", lambda m: "/*DATA_START*/\nconst DATA=" + json.dumps(data) + ";\n/*DATA_END*/", html, flags=re.S)
open(HTML, "w", encoding="utf-8").write(html)
print("updated", data["asOf"])
