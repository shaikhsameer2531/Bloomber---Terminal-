from flask import Flask, render_template
import yfinance as yf

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


def fmt_vol(v):
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(v)


def fetch_data():
    # Build ticker list: stocks (.NS suffix) + indices
    stock_tickers = [f"{s}.NS" for s in STOCKS]
    index_tickers = list(INDICES.values())
    all_tickers = stock_tickers + index_tickers

    data = yf.download(all_tickers, period="1d", group_by="ticker", threads=True)

    # Parse stock cards
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

    # Parse indices
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
    stocks, indices = fetch_data()
    return render_template("index.html", stocks=stocks, indices=indices)


if __name__ == "__main__":
    app.run(debug=True)
