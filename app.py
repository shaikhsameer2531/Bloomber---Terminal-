from flask import Flask, render_template, request, jsonify
import yfinance as yf
import requests
import math

app = Flask(__name__)

STOCKS = [
    "RELIANCE", "HDFCBANK", "TCS", "SUNPHARMA", "HAL",
    "HINDUNILVR", "TATAMOTORS", "NTPC", "AXISBANK", "ICICIBANK",
]

INDICES = {
    "nifty":      "^NSEI",
    "banknifty":  "^NSEBANK",
    "sensex":     "^BSESN",
}

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}


def fmt_vol(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(v)


def fetch_data():
    stock_tickers = [f"{s}.NS" for s in STOCKS]
    index_tickers = list(INDICES.values())
    all_tickers = stock_tickers + index_tickers

    data = yf.download(all_tickers, period="1d", group_by="ticker", threads=True)

    stocks = []
    for name, ticker in zip(STOCKS, stock_tickers):
        try:
            row = data[ticker].iloc[-1]
            price = float(row["Close"])
            prev = float(row["Open"])
            change = price - prev
            pct = (change / prev) * 100 if prev else 0
            stocks.append({
                "symbol": name,
                "price":  round(price, 2),
                "change": round(change, 2),
                "pct":    round(pct, 2),
                "high":   round(float(row["High"]), 2),
                "low":    round(float(row["Low"]), 2),
                "vol":    fmt_vol(int(row["Volume"])),
            })
        except Exception:
            stocks.append({
                "symbol": name, "price": 0, "change": 0, "pct": 0,
                "high": 0, "low": 0, "vol": "0",
            })

    indices = {}
    for key, ticker in INDICES.items():
        try:
            row = data[ticker].iloc[-1]
            indices[key] = round(float(row["Close"]), 2)
        except Exception:
            indices[key] = 0

    return stocks, indices


def fetch_options_chain_nse():
    """Try fetching live Nifty options chain from NSE India."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    # Hit main page first to get cookies
    session.get("https://www.nseindia.com/option-chain", timeout=5)
    resp = session.get(
        "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def parse_nse_chain(raw):
    """Parse NSE API response into our format."""
    records = raw["records"]
    expiries = sorted(records["expiryDates"])
    nearest_expiry = expiries[0] if expiries else ""
    spot = records["underlyingValue"]

    # Round spot to nearest 50 for ATM
    atm_strike = round(spot / 50) * 50

    rows = []
    total_ce_oi = 0
    total_pe_oi = 0

    for item in records["data"]:
        if item.get("expiryDate") != nearest_expiry:
            continue
        strike = item["strikePrice"]
        ce = item.get("CE", {})
        pe = item.get("PE", {})
        ce_oi = ce.get("openInterest", 0)
        pe_oi = pe.get("openInterest", 0)
        total_ce_oi += ce_oi
        total_pe_oi += pe_oi
        rows.append({
            "strike":       strike,
            "ce_ltp":       ce.get("lastPrice", 0),
            "ce_oi":        ce_oi,
            "ce_oi_change": ce.get("changeinOpenInterest", 0),
            "pe_ltp":       pe.get("lastPrice", 0),
            "pe_oi":        pe_oi,
            "pe_oi_change": pe.get("changeinOpenInterest", 0),
            "is_atm":       strike == atm_strike,
        })

    rows.sort(key=lambda r: r["strike"])

    # Filter to +/- 10 strikes around ATM
    atm_idx = next((i for i, r in enumerate(rows) if r["is_atm"]), len(rows) // 2)
    start = max(0, atm_idx - 10)
    end = min(len(rows), atm_idx + 11)
    rows = rows[start:end]

    pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0
    return rows, pcr, nearest_expiry, spot


def dummy_options_chain(nifty_price):
    """Generate realistic dummy options chain data around given Nifty price."""
    spot = nifty_price if nifty_price > 0 else 23500
    atm_strike = round(spot / 50) * 50

    rows = []
    total_ce_oi = 0
    total_pe_oi = 0

    for i in range(-10, 11):
        strike = atm_strike + i * 50
        dist = abs(i)

        # Simulate realistic option prices — deeper ITM = higher premium
        if i < 0:  # strike below spot: CE is ITM, PE is OTM
            ce_ltp = round(spot - strike + max(0, 80 - dist * 12) + 5, 2)
            pe_ltp = round(max(2, 120 - dist * 15 + (dist ** 1.3) * 2), 2)
        elif i > 0:  # strike above spot: CE is OTM, PE is ITM
            ce_ltp = round(max(2, 120 - dist * 15 + (dist ** 1.3) * 2), 2)
            pe_ltp = round(strike - spot + max(0, 80 - dist * 12) + 5, 2)
        else:  # ATM
            ce_ltp = round(95 + spot * 0.002, 2)
            pe_ltp = round(88 + spot * 0.002, 2)

        # OI peaks around ATM and at round numbers
        base_oi = max(500, int(12000 - dist * 900 + (dist ** 0.5) * 200))
        round_bonus = 3000 if strike % 500 == 0 else (1500 if strike % 100 == 0 else 0)
        ce_oi = base_oi + round_bonus + (2000 if i > 0 else 0)
        pe_oi = base_oi + round_bonus + (2000 if i < 0 else 0)

        ce_oi_chg = int(ce_oi * 0.08 * (1 if i % 3 else -1))
        pe_oi_chg = int(pe_oi * 0.06 * (-1 if i % 2 else 1))

        total_ce_oi += ce_oi
        total_pe_oi += pe_oi

        rows.append({
            "strike":       strike,
            "ce_ltp":       ce_ltp,
            "ce_oi":        ce_oi,
            "ce_oi_change": ce_oi_chg,
            "pe_ltp":       pe_ltp,
            "pe_oi":        pe_oi,
            "pe_oi_change": pe_oi_chg,
            "is_atm":       i == 0,
        })

    pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0
    expiry = "27-Mar-2026"
    return rows, pcr, expiry, spot


def fetch_options_chain(nifty_price):
    """Fetch live chain from NSE; fall back to dummy data on failure."""
    try:
        raw = fetch_options_chain_nse()
        return parse_nse_chain(raw)
    except Exception:
        return dummy_options_chain(nifty_price)


def fetch_stock_data():
    stock_tickers = [f"{s}.NS" for s in STOCKS]
    index_tickers = list(INDICES.values())
    all_tickers = stock_tickers + index_tickers

    data = yf.download(all_tickers, period="1d", group_by="ticker", threads=True)

    stocks = []
    for name, ticker in zip(STOCKS, stock_tickers):
        try:
            row = data[ticker].iloc[-1]
            price = float(row["Close"])
            prev = float(row["Open"])
            change = price - prev
            pct = (change / prev) * 100 if prev else 0
            stocks.append({
                "symbol": name,
                "price":  round(price, 2),
                "change": round(change, 2),
                "pct":    round(pct, 2),
                "high":   round(float(row["High"]), 2),
                "low":    round(float(row["Low"]), 2),
                "vol":    fmt_vol(int(row["Volume"])),
            })
        except Exception:
            stocks.append({
                "symbol": name, "price": 0, "change": 0, "pct": 0,
                "high": 0, "low": 0, "vol": "0",
            })

    indices = {}
    for key, ticker in INDICES.items():
        try:
            row = data[ticker].iloc[-1]
            indices[key] = round(float(row["Close"]), 2)
        except Exception:
            indices[key] = 0

    return stocks, indices


@app.route("/")
def index():
    stocks, indices = fetch_stock_data()
    nifty_price = indices.get("nifty", 0)
    chain, pcr, expiry, spot = fetch_options_chain(nifty_price)
    return render_template(
        "index.html",
        stocks=stocks,
        indices=indices,
        chain=chain,
        pcr=pcr,
        expiry=expiry,
        spot=spot,
    )


SYSTEM_PROMPT = (
    "You are a senior Indian equity market analyst. You understand NSE/BSE, "
    "F&O mechanics, lot sizes, expiry cycles, India VIX, PCR, OI analysis, "
    "and technical indicators. Give concise, actionable analysis. Use ₹ for prices."
)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(force=True)
    api_key = body.get("api_key", "")
    user_msg = body.get("message", "")

    if not api_key:
        return jsonify({"error": "API key not set. Click the ⚙ icon to configure."}), 400
    if not user_msg:
        return jsonify({"error": "Empty message."}), 400

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        return jsonify({"reply": text})
    except requests.exceptions.HTTPError as e:
        err_body = e.response.text if e.response is not None else str(e)
        return jsonify({"error": f"Anthropic API error: {err_body}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
