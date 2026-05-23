#!/usr/bin/env python3
"""Daily portfolio dashboard update: fetch prices, render index.html."""

import json
import urllib.request
import urllib.error
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "positions.json"
OUT = ROOT / "index.html"
TODAY = date.today()

UA = {"User-Agent": "Mozilla/5.0 (portfolio-dashboard)"}


def fetch_yahoo(symbol: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read())
    result = data["chart"]["result"][0]
    price = result["meta"].get("regularMarketPrice")
    if price is None:
        raise ValueError("no price")
    return float(price)


def fetch_coingecko_batch(ids):
    if not ids:
        return {}
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(ids)}&vs_currencies=usd"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def refresh_prices(data):
    failures = []
    # Crypto - one batch call
    coin_ids = [p["coingecko"] for p in data["positions"] if "coingecko" in p]
    try:
        coin_prices = fetch_coingecko_batch(coin_ids)
    except Exception as e:
        print(f"CoinGecko batch failed: {e}")
        coin_prices = {}

    for p in data["positions"]:
        if "yahoo" in p:
            try:
                p["currentPrice"] = round(fetch_yahoo(p["yahoo"]), 4)
                time.sleep(0.25)
            except Exception as e:
                failures.append((p["ticker"], str(e)))
        elif "coingecko" in p:
            cid = p["coingecko"]
            if cid in coin_prices and "usd" in coin_prices[cid]:
                p["currentPrice"] = coin_prices[cid]["usd"]
            else:
                failures.append((p["ticker"], "coingecko miss"))
    return failures


def xirr(cashflows, guess=0.4):
    d0 = cashflows[0][0]
    def npv(r):
        return sum(c / (1 + r) ** ((d - d0).days / 365.0) for d, c in cashflows)
    lo, hi = -0.999, 10.0
    for _ in range(300):
        mid = (lo + hi) / 2
        v = npv(mid)
        if abs(v) < 0.001:
            return mid
        if v > 0: lo = mid
        else: hi = mid
    return mid


def fmt_money(v, decimals=2):
    if v is None: return "-"
    s = f"{abs(v):,.{decimals}f}"
    return f"-${s}" if v < 0 else f"${s}"


def fmt_money_round(v):
    if v is None: return "-"
    return f"${abs(v):,.0f}" if v >= 0 else f"-${abs(v):,.0f}"


def fmt_shares(v):
    if v is None: return "-"
    if v >= 1000: return f"{v:,.2f}"
    if v >= 100: return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    if v >= 1: return f"{v:g}" if v == int(v) else f"{v:.2f}"
    return f"{v:.6f}".rstrip("0").rstrip(".")


def fmt_avg(v):
    if v is None: return "-"
    if v >= 100: return f"${v:,.2f}"
    if v >= 1: return f"${v:.2f}"
    return f"${v:.4f}".rstrip("0").rstrip(".") if v < 0.01 else f"${v:.2f}"


def render_row(p, totalVal):
    cat = p["category"]
    fixed = "fixedValue" in p
    if fixed:
        val = p["fixedValue"]
        ticker_html = p["ticker"]
        return (
            f'<tr data-category="{cat}">'
            f'<td class="tk">{ticker_html}</td>'
            f'<td class="cat">{cat}</td>'
            f'<td class="num">-</td>'
            f'<td class="num">-</td>'
            f'<td class="val">{fmt_money(val)}</td>'
            f'</tr>'
        )
    shares = p["shares"]
    avg = p["avgPrice"]
    cost = p["cost"]
    price = p["currentPrice"]
    value = shares * price
    pnl = value - cost
    pnl_pct = pnl / cost * 100 if cost else 0
    valcls = "val" if pnl >= 0 else "val neg"
    pnl_sign = "+" if pnl >= 0 else "-"
    pct_sign = "+" if pnl_pct >= 0 else "-"
    # Ticker cell may have subTicker
    if "subTicker" in p:
        tk_html = f'{p.get("displayName", p["ticker"])}<span class="sub">{p["subTicker"]}</span>'
    else:
        tk_html = p["ticker"]
    return (
        f'<tr data-category="{cat}">'
        f'<td class="tk">{tk_html}</td>'
        f'<td class="cat">{cat}</td>'
        f'<td class="num">{fmt_shares(shares)}</td>'
        f'<td class="num">{fmt_avg(avg)}<span class="cost">{fmt_money_round(cost)} inv.</span></td>'
        f'<td class="{valcls}">{fmt_money(value)}<span class="pnl">{pnl_sign}{fmt_money_round(abs(pnl))[1:] if pnl<0 else fmt_money_round(pnl)[1:]} ({pct_sign}{abs(pnl_pct):.0f}%)</span></td>'
        f'</tr>'
    )


def render(data):
    positions = data["positions"]
    meta = data["meta"]
    cat_colors = data["categoryColors"]

    # Compute values
    rows_with_val = []
    total = 0.0
    for p in positions:
        if "fixedValue" in p:
            v = p["fixedValue"]
        else:
            v = p["shares"] * p["currentPrice"]
        rows_with_val.append((p, v))
        total += v

    invested = meta["totalInvested"]
    pnl = total - invested
    pnl_pct = pnl / invested * 100

    # XIRR
    cf = []
    monthly = invested / meta["monthsActive"]
    y, m = 2023, 1
    for _ in range(meta["monthsActive"]):
        cf.append((date(y, m, 1), -monthly))
        m += 1
        if m > 12: m = 1; y += 1
    cf.append((TODAY, total))
    xirr_r = xirr(cf) * 100

    # Category allocations
    cat_totals = {}
    for p, v in rows_with_val:
        cat_totals[p["category"]] = cat_totals.get(p["category"], 0) + v

    # Build conic-gradient (sorted by descending percentage)
    cat_sorted_donut = sorted(cat_totals.items(), key=lambda x: -x[1])
    gradient_parts = []
    cum = 0.0
    cat_pcts = {}
    for cat, val in cat_sorted_donut:
        pct = val / total * 100
        cat_pcts[cat] = pct
        color = cat_colors.get(cat, "#888")
        gradient_parts.append(f"{color} {cum:.2f}% {cum+pct:.2f}%")
        cum += pct
    gradient = ",\n      ".join(gradient_parts)

    # Position rows split into 2 columns
    sorted_positions = sorted(rows_with_val, key=lambda x: -x[1])
    half = (len(sorted_positions) + 1) // 2
    col1 = sorted_positions[:half]
    col2 = sorted_positions[half:]
    col1_html = "\n        ".join(render_row(p, total) for p, _ in col1)
    col2_html = "\n        ".join(render_row(p, total) for p, _ in col2)

    # Legend (in a fixed visual order for two-column layout)
    legend_order = ["Tech akcijos", "Sveikata", "Finansai", "Nasdaq 100 ETF",
                    "Pinigai", "Azijos rinka", "Kripto", "Mažos akcijos",
                    "Kinų akcijos", "Dividendai", "Kripto akcijos"]
    legend_html_parts = []
    for cat in legend_order:
        if cat not in cat_totals:
            continue
        color = cat_colors[cat]
        pct = cat_pcts[cat]
        legend_html_parts.append(
            f'<div class="legend-item" data-category="{cat}" data-color="{color}">'
            f'<span class="dot" style="background:{color}"></span>'
            f'<span class="name">{cat}</span>'
            f'<span class="pct">{pct:.1f}%</span></div>'
        )
    legend_html = "\n        ".join(legend_html_parts)

    # Active position count (everything except Pinigai + Dividendai)
    active_count = sum(1 for p in positions if "fixedValue" not in p)
    cat_count = len(cat_totals)

    # Donut center
    total_short = f"${total/1000:,.0f}K"

    # S&P 500 benchmark comparison (DCA $1,332/mėn Jan 2023 - May 2026)
    # Based on validated returns: 2023 +26.3%, 2024 +25.0%, 2025 +14%, 2026 YTD +18.7%
    # Matches finviz 1Y trailing +28.79% and 3Y annualized +23.18%
    spx_value = 82666
    spx_return_pct = 51.4
    spx_xirr = 25.7
    spx_profit = spx_value - invested
    your_profit = total - invested
    diff_dollar = your_profit - spx_profit
    diff_multiple = your_profit / spx_profit if spx_profit > 0 else 0
    # Bars now compare PROFIT (same capital in, profit is the real diff)
    your_bar_pct = 100.0
    spx_bar_pct = (spx_profit / your_profit * 100) if your_profit > 0 else 0

    # 10-year projection: continue DCA $1,332/mėn at current XIRRs
    def fv_dca(pv, pmt, annual_rate, months):
        mr = (1 + annual_rate) ** (1/12) - 1
        fv_lump = pv * (1 + mr) ** months
        fv_ann = pmt * (((1 + mr) ** months - 1) / mr) * (1 + mr)
        return fv_lump + fv_ann

    proj_months = 120
    proj_pmt = meta['monthlyDCA']
    your_proj = fv_dca(total, proj_pmt, xirr_r/100, proj_months)
    spx_proj = fv_dca(spx_value, proj_pmt, spx_xirr/100, proj_months)
    proj_total_invested_future = invested + proj_pmt * proj_months
    your_proj_profit = your_proj - proj_total_invested_future
    spx_proj_profit = spx_proj - proj_total_invested_future
    proj_diff_dollar = your_proj - spx_proj
    proj_diff_pct = (your_proj / spx_proj - 1) * 100 if spx_proj > 0 else 0
    spx_proj_bar = (spx_proj / your_proj * 100) if your_proj > 0 else 0

    def fmt_big(v):
        if v >= 1_000_000:
            return f"${v/1_000_000:.2f}M"
        if v >= 1_000:
            return f"${v/1_000:.0f}K"
        return f"${v:,.0f}"

    template = f"""<!DOCTYPE html>
<html lang="lt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>daug_pinigu - Portfelio inventorius</title>
<style>
  :root {{
    --bg: #0b1729;
    --bg-card: #0f1f36;
    --line: #1a2d4a;
    --accent: #2dd4bf;
    --text: #e6edf6;
    --muted: #6b8197;
    --green: #2dd4bf;
    --red: #ef6a6a;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Helvetica, Arial, sans-serif; font-feature-settings: "tnum" 1, "lnum" 1; -webkit-font-smoothing: antialiased; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 56px 48px 40px; }}
  .eyebrow {{ color: var(--accent); font-size: 13px; letter-spacing: 4px; text-transform: uppercase; font-weight: 600; margin-bottom: 10px; }}
  h1 {{ font-size: 56px; font-weight: 800; letter-spacing: -1px; margin-bottom: 8px; color: #fff; }}
  .subtitle {{ color: var(--muted); font-size: 14px; margin-bottom: 32px; }}
  .perf {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; margin: 0 0 32px; padding: 28px 0; border-top: 1.5px solid var(--accent); border-bottom: 1.5px solid var(--accent); background: linear-gradient(180deg, rgba(45,212,191,0.04), rgba(45,212,191,0.0)); }}
  .perf-item {{ text-align: center; padding: 0 24px; border-right: 1px solid var(--line); }}
  .perf-item:last-child {{ border-right: none; }}
  .perf-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600; margin-bottom: 10px; }}
  .perf-value {{ font-size: 32px; font-weight: 800; letter-spacing: -0.5px; color: #fff; font-variant-numeric: tabular-nums; line-height: 1; margin-bottom: 6px; }}
  .perf-value.pos {{ color: var(--green); }}
  .perf-value.neg {{ color: var(--red); }}
  .perf-sub {{ color: var(--muted); font-size: 12px; }}
  .benchmark {{ margin-bottom: 32px; padding: 28px 32px; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--line); }}
  .benchmark-title {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 2px; font-weight: 600; margin-bottom: 6px; }}
  .benchmark-hint {{ color: var(--muted); font-size: 11.5px; opacity: 0.7; margin-bottom: 24px; max-width: 700px; line-height: 1.5; }}
  .benchmark-rows {{ display: flex; flex-direction: column; gap: 14px; margin-bottom: 20px; }}
  .benchmark-row {{ display: grid; grid-template-columns: 160px 1fr 140px; align-items: center; gap: 20px; }}
  .benchmark-row .bk-label {{ color: #cfd8e3; font-size: 13px; font-weight: 600; }}
  .benchmark-row.you .bk-label {{ color: var(--accent); }}
  .benchmark-row .bk-bar-wrap {{ height: 52px; background: #14253e; border-radius: 6px; position: relative; overflow: hidden; }}
  .benchmark-row .bk-bar {{ position: absolute; top: 0; bottom: 0; left: 0; border-radius: 6px; transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1); animation: barGrow 1s cubic-bezier(0.4, 0, 0.2, 1); }}
  @keyframes barGrow {{ from {{ width: 0 !important; }} }}
  .benchmark-row.you .bk-bar {{ background: linear-gradient(90deg, var(--accent) 0%, #5eead4 50%, #99f6e4 100%); box-shadow: 0 0 32px rgba(45,212,191,0.55), inset 0 1px 0 rgba(255,255,255,0.2); }}
  .benchmark-row.you .bk-bar::after {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 50%; background: linear-gradient(180deg, rgba(255,255,255,0.18), transparent); border-radius: 6px 6px 0 0; }}
  .benchmark-row.vwce .bk-bar {{ background: linear-gradient(90deg, #475569 0%, #64748b 100%); opacity: 0.7; }}
  .benchmark-row .bk-right {{ text-align: right; }}
  .benchmark-row .bk-value {{ font-size: 34px; font-weight: 900; color: #fff; font-variant-numeric: tabular-nums; line-height: 1; letter-spacing: -1.2px; }}
  .benchmark-row.you .bk-value {{ background: linear-gradient(135deg, #2dd4bf 0%, #5eead4 60%, #fff 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }}
  .benchmark-row .bk-meta {{ display: block; font-size: 11px; color: var(--muted); font-weight: 500; margin-top: 6px; letter-spacing: 0.4px; }}
  .benchmark-row {{ grid-template-columns: 170px 1fr 180px !important; gap: 24px !important; }}
  .benchmark-rows {{ gap: 18px !important; }}
  .hero-callout {{ margin-top: 24px; padding: 22px 24px; background: linear-gradient(135deg, rgba(45,212,191,0.12), rgba(45,212,191,0.04)); border-radius: 8px; border: 1px solid rgba(45,212,191,0.25); display: grid; grid-template-columns: auto 1fr; gap: 28px; align-items: center; }}
  .hero-multiplier {{ text-align: center; }}
  .hero-multiplier .x-big {{ font-size: 68px; font-weight: 900; line-height: 0.9; letter-spacing: -3px; background: linear-gradient(135deg, #2dd4bf 0%, #5eead4 50%, #99f6e4 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; text-shadow: 0 0 32px rgba(45,212,191,0.4); }}
  .hero-multiplier .x-label {{ font-size: 10px; letter-spacing: 2.5px; text-transform: uppercase; color: var(--accent); font-weight: 700; margin-top: 4px; }}
  .hero-text {{ font-size: 14px; color: #cfd8e3; line-height: 1.55; }}
  .hero-text .hero-dollar {{ display: block; font-size: 28px; font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; letter-spacing: -0.8px; line-height: 1; margin-bottom: 6px; }}
  .hero-text .hero-sub {{ color: var(--muted); font-size: 12.5px; }}
  .benchmark-rows {{ position: relative; }}
  .benchmark-row.vwce .bk-value {{ color: #94a3b8; }}
  .benchmark-row.vwce .bk-label {{ color: #94a3b8; }}
  .benchmark.projection {{ border-color: rgba(45,212,191,0.3); background: linear-gradient(135deg, var(--bg-card) 0%, #0d2240 100%); }}
  .benchmark.projection .hero-multiplier .x-big {{ font-size: 80px; }}
  .proj-badge {{ display: inline-block; padding: 3px 10px; margin-left: 10px; background: rgba(45,212,191,0.2); color: var(--accent); font-size: 10px; letter-spacing: 1.5px; text-transform: uppercase; font-weight: 700; border-radius: 4px; vertical-align: middle; }}
  /* Interactive projection */
  .slider-section {{ display: grid; grid-template-columns: 220px 1fr; gap: 32px; align-items: center; margin: 24px 0 12px; padding: 20px 24px; background: rgba(45,212,191,0.04); border-radius: 8px; border: 1px solid rgba(45,212,191,0.15); }}
  .slider-display {{ text-align: center; }}
  .slider-display .slider-num {{ font-size: 56px; font-weight: 900; letter-spacing: -2.5px; line-height: 1; background: linear-gradient(135deg, #2dd4bf 0%, #5eead4 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; display: block; }}
  .slider-display .slider-unit {{ display: block; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--accent); font-weight: 700; margin-top: 4px; }}
  .slider-wrap {{ width: 100%; }}
  input[type=range].slider {{ -webkit-appearance: none; appearance: none; width: 100%; height: 8px; background: linear-gradient(to right, var(--accent) 0%, var(--accent) 31%, #1a2d4a 31%, #1a2d4a 100%); border-radius: 4px; outline: none; cursor: pointer; }}
  input[type=range].slider::-webkit-slider-thumb {{ -webkit-appearance: none; appearance: none; width: 24px; height: 24px; background: var(--accent); border-radius: 50%; cursor: grab; box-shadow: 0 0 0 4px rgba(45,212,191,0.2), 0 0 24px rgba(45,212,191,0.4); transition: transform 0.1s; }}
  input[type=range].slider::-webkit-slider-thumb:active {{ cursor: grabbing; transform: scale(1.15); }}
  input[type=range].slider::-moz-range-thumb {{ width: 24px; height: 24px; background: var(--accent); border-radius: 50%; cursor: grab; border: none; box-shadow: 0 0 0 4px rgba(45,212,191,0.2), 0 0 24px rgba(45,212,191,0.4); }}
  .slider-marks {{ display: flex; justify-content: space-between; margin-top: 12px; font-size: 10px; color: var(--muted); letter-spacing: 0.5px; }}
  .slider-marks span {{ cursor: pointer; transition: color 0.15s; padding: 2px 4px; }}
  .slider-marks span:hover {{ color: var(--accent); }}
  .chart-wrap {{ margin: 28px 0 20px; padding: 16px 8px 8px; background: rgba(11,23,41,0.4); border-radius: 8px; border: 1px solid var(--line); }}
  .chart {{ width: 100%; height: auto; display: block; }}
  .chart-legend {{ display: flex; gap: 24px; justify-content: center; margin-top: 12px; font-size: 12px; color: #cfd8e3; }}
  .chart-legend .leg {{ display: flex; align-items: center; gap: 8px; }}
  .chart-legend .leg .dot {{ width: 12px; height: 12px; border-radius: 50%; }}
  .chart-legend .leg.you .dot {{ background: var(--accent); box-shadow: 0 0 8px rgba(45,212,191,0.6); }}
  .chart-legend .leg.spx .dot {{ background: #94a3b8; }}
  @media (max-width: 880px) {{
    .slider-section {{ grid-template-columns: 1fr; gap: 16px; }}
    .slider-display .slider-num {{ font-size: 42px; }}
  }}
  .alloc-section {{ margin-bottom: 40px; padding: 32px; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--line); }}
  .alloc-title {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 2px; font-weight: 600; margin-bottom: 6px; }}
  .alloc-hint {{ color: var(--muted); font-size: 11.5px; margin-bottom: 24px; opacity: 0.7; }}
  .alloc-grid {{ display: grid; grid-template-columns: 240px 1fr; gap: 48px; align-items: center; }}
  .donut-wrap {{ position: relative; width: 240px; height: 240px; }}
  .donut {{ width: 100%; height: 100%; border-radius: 50%; background: conic-gradient(
      {gradient}
    ); position: relative; }}
  .donut::after {{ content: ''; position: absolute; inset: 42px; background: var(--bg-card); border-radius: 50%; }}
  .donut-center {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 2; text-align: center; pointer-events: none; transition: opacity 0.15s; }}
  .donut-center .big {{ font-size: 28px; font-weight: 800; color: #fff; letter-spacing: -0.5px; line-height: 1; }}
  .donut-center .sml {{ font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 1.2px; margin-top: 6px; }}
  .legend {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 24px; }}
  .legend-item {{ display: flex; align-items: center; gap: 10px; font-size: 13px; padding: 6px 10px; border-radius: 5px; cursor: pointer; transition: background 0.12s, transform 0.12s; user-select: none; }}
  .legend-item:hover {{ background: rgba(255,255,255,0.05); }}
  .legend-item.active {{ background: rgba(45,212,191,0.12); box-shadow: inset 0 0 0 1px rgba(45,212,191,0.3); }}
  .legend-item.active .name {{ color: #fff; font-weight: 600; }}
  .legend-item.active .pct {{ color: var(--accent); }}
  .legend-item .dot {{ width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }}
  .legend-item .name {{ flex: 1; color: #cfd8e3; transition: color 0.12s; }}
  .legend-item .pct {{ color: var(--muted); font-variant-numeric: tabular-nums; font-weight: 600; transition: color 0.12s; }}
  .filter-reset {{ display: none; background: transparent; border: 1px solid var(--line); color: var(--muted); font-size: 11px; padding: 5px 12px; border-radius: 4px; cursor: pointer; letter-spacing: 1px; text-transform: uppercase; font-weight: 600; transition: all 0.12s; }}
  .filter-reset:hover {{ color: #fff; border-color: var(--accent); }}
  .filter-reset.show {{ display: inline-block; }}
  .section-header {{ color: #fff; font-size: 22px; font-weight: 700; letter-spacing: -0.3px; margin-bottom: 18px; display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }}
  .section-header .meta-inline {{ color: var(--muted); font-size: 12px; font-weight: 500; letter-spacing: 0.5px; display: flex; align-items: center; gap: 12px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 32px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  thead th {{ text-align: left; color: var(--muted); font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; font-weight: 600; padding: 10px 0 14px; border-bottom: 1px solid var(--line); }}
  thead th.num {{ text-align: right; }}
  tbody td {{ padding: 9px 0; border-bottom: 1px solid #14253e; color: #cfd8e3; vertical-align: top; }}
  tbody td.tk {{ color: #fff; font-weight: 700; letter-spacing: 0.3px; }}
  tbody td.tk .sub {{ color: var(--muted); font-weight: 500; font-size: 10.5px; letter-spacing: 1px; display: block; margin-top: 1px; }}
  tbody td.cat {{ color: var(--muted); font-size: 12px; }}
  tbody td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tbody td.num .cost {{ display: block; color: var(--muted); font-size: 10.5px; font-weight: 500; margin-top: 2px; letter-spacing: 0.3px; }}
  tbody td.val {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; color: var(--green); }}
  tbody td.val.neg {{ color: var(--red); }}
  tbody td.val .pnl {{ display: block; font-size: 10.5px; font-weight: 500; margin-top: 2px; letter-spacing: 0.3px; opacity: 0.9; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr {{ transition: opacity 0.18s, background 0.18s; }}
  body.filtering tbody tr.dimmed {{ opacity: 0.18; }}
  body.filtering tbody tr.match td.cat {{ color: var(--accent); }}
  .meta {{ margin-top: 24px; display: flex; justify-content: space-between; color: var(--muted); font-size: 11.5px; letter-spacing: 0.5px; }}
  .live-badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; background: rgba(45,212,191,0.15); color: var(--accent); font-size: 10px; letter-spacing: 1.2px; text-transform: uppercase; font-weight: 700; margin-left: 8px; }}
  @media (max-width: 880px) {{
    .wrap {{ padding: 32px 20px; }}
    h1 {{ font-size: 36px; }}
    .benchmark {{ padding: 20px; }}
    .benchmark-row {{ grid-template-columns: 1fr; gap: 8px; }}
    .benchmark-row .bk-right {{ text-align: left; }}
    .perf {{ grid-template-columns: repeat(2, 1fr); gap: 24px 0; padding: 20px 0; }}
    .perf-item {{ padding: 0 16px; border-right: none; }}
    .perf-item:nth-child(odd) {{ border-right: 1px solid var(--line); }}
    .perf-value {{ font-size: 24px; }}
    .alloc-grid {{ grid-template-columns: 1fr; gap: 32px; justify-items: center; }}
    .legend {{ grid-template-columns: 1fr; gap: 4px; }}
    .grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">Portfelis</div>
  <h1>daug_pinigu</h1>
  <div class="subtitle">Asmeninis portfelio inventorius - DCA strategija nuo 2023 sausio<span class="live-badge">Auto-update</span></div>

  <div class="perf">
    <div class="perf-item">
      <div class="perf-label">Bendras pelnas</div>
      <div class="perf-value {'pos' if pnl >= 0 else 'neg'}">{'+' if pnl_pct >= 0 else ''}{pnl_pct:.1f}%</div>
      <div class="perf-sub">{'+' if pnl >= 0 else ''}{fmt_money_round(pnl)}</div>
    </div>
    <div class="perf-item">
      <div class="perf-label">Metinė grąža (XIRR)</div>
      <div class="perf-value {'pos' if xirr_r >= 0 else 'neg'}">{'+' if xirr_r >= 0 else ''}{xirr_r:.1f}%</div>
      <div class="perf-sub">per metus</div>
    </div>
    <div class="perf-item">
      <div class="perf-label">Investuota</div>
      <div class="perf-value">${invested:,.0f}</div>
      <div class="perf-sub">{meta['monthsActive']} mėn DCA, ~${meta['monthlyDCA']:,.0f}/mėn</div>
    </div>
    <div class="perf-item">
      <div class="perf-label">Vertė šiandien</div>
      <div class="perf-value">${total:,.0f}</div>
      <div class="perf-sub">2023.01 - {TODAY.strftime('%Y.%m')}</div>
    </div>
  </div>

  <div class="benchmark">
    <div class="benchmark-title">Pelnas vs S&amp;P 500 benchmark</div>
    <div class="benchmark-hint">Tą pačią ~${meta['monthlyDCA']:,.0f}/mėn DCA sumą investavus į S&amp;P 500 indeksą (pasyvi strategija). Įdėtas kapitalas tas pats - <strong style="color:#fff">{int(round(diff_multiple*10))/10}x didesnis pelnas</strong> dėl aktyvaus pick'inimo.</div>
    <div class="benchmark-rows">
      <div class="benchmark-row you">
        <div class="bk-label">Tavo pelnas</div>
        <div class="bk-bar-wrap"><div class="bk-bar" style="width: {your_bar_pct:.1f}%"></div></div>
        <div class="bk-right">
          <div class="bk-value">+${your_profit:,.0f}</div>
          <div class="bk-meta">+{pnl_pct:.1f}% &bull; vertė ${total:,.0f}</div>
        </div>
      </div>
      <div class="benchmark-row vwce">
        <div class="bk-label">S&amp;P 500 pelnas</div>
        <div class="bk-bar-wrap"><div class="bk-bar" style="width: {spx_bar_pct:.1f}%"></div></div>
        <div class="bk-right">
          <div class="bk-value">+${spx_profit:,.0f}</div>
          <div class="bk-meta">+{spx_return_pct:.1f}% &bull; vertė ${spx_value:,.0f}</div>
        </div>
      </div>
    </div>
    <div class="hero-callout">
      <div class="hero-multiplier">
        <div class="x-big">{diff_multiple:.1f}x</div>
        <div class="x-label">daugiau pelno</div>
      </div>
      <div class="hero-text">
        <span class="hero-dollar">+${diff_dollar:,.0f}</span>
        Tiek didesnis uždarbis nei pasyvi S&amp;P 500 strategija už tą pačią DCA sumą per tą patį laikotarpį.
        <span class="hero-sub">Aktyvus pick'inimas davė +{(diff_dollar/spx_profit*100):.0f}% extra grąžą virš S&amp;P 500.</span>
      </div>
    </div>
  </div>

  <div class="benchmark projection interactive">
    <div class="benchmark-title">Interaktyvi projekcija <span class="proj-badge">Hipotetinė</span></div>
    <div class="benchmark-hint">Pajudink slankiklį - kaip skirtumas auga laikui bėgant. Skaičiavimai pagal dabartinį XIRR ({xirr_r:.0f}% portfelio vs {spx_xirr:.0f}% S&amp;P 500), tęsiant DCA ~${meta['monthlyDCA']:,.0f}/mėn. Realiai grąža regresuos į vidurkį - matematinė vizualizacija, ne prognozė.</div>

    <div class="slider-section">
      <div class="slider-display">
        <span class="slider-num" id="yearVal">10</span>
        <span class="slider-unit">metų horizontas</span>
      </div>
      <div class="slider-wrap">
        <input type="range" min="1" max="30" value="10" step="1" id="yearSlider" class="slider"/>
        <div class="slider-marks">
          <span data-y="1">1m</span><span data-y="5">5m</span><span data-y="10">10m</span><span data-y="15">15m</span><span data-y="20">20m</span><span data-y="25">25m</span><span data-y="30">30m</span>
        </div>
      </div>
    </div>

    <div class="chart-wrap">
      <svg viewBox="0 0 820 380" class="chart" id="chart" preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id="youGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#2dd4bf" stop-opacity="0.45"/>
            <stop offset="100%" stop-color="#2dd4bf" stop-opacity="0"/>
          </linearGradient>
          <linearGradient id="spxGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#64748b" stop-opacity="0.25"/>
            <stop offset="100%" stop-color="#64748b" stop-opacity="0"/>
          </linearGradient>
        </defs>
        <g id="gridGroup"></g>
        <g id="yLabels"></g>
        <g id="xLabels"></g>
        <path id="spxArea" fill="url(#spxGrad)"></path>
        <path id="yourArea" fill="url(#youGrad)"></path>
        <path id="spxLine" stroke="#94a3b8" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"></path>
        <path id="yourLine" stroke="#2dd4bf" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" filter="drop-shadow(0 0 8px rgba(45,212,191,0.5))"></path>
        <circle id="spxDot" r="5" fill="#94a3b8" stroke="#0b1729" stroke-width="2"></circle>
        <circle id="yourDot" r="7" fill="#2dd4bf" stroke="#0b1729" stroke-width="2" filter="drop-shadow(0 0 8px rgba(45,212,191,0.7))"></circle>
      </svg>
      <div class="chart-legend">
        <span class="leg you"><span class="dot"></span>Tavo portfelis (XIRR {xirr_r:.0f}%)</span>
        <span class="leg spx"><span class="dot"></span>S&amp;P 500 (XIRR {spx_xirr:.0f}%)</span>
      </div>
    </div>

    <div class="hero-callout">
      <div class="hero-multiplier">
        <div class="x-big" id="heroMult">2.7x</div>
        <div class="x-label">daugiau po <span id="heroYears">10</span> m.</div>
      </div>
      <div class="hero-text">
        <span class="hero-dollar" id="heroDiff">+$2.42M</span>
        Portfelio vertė <span id="heroYourVal">$3.86M</span> vs S&amp;P 500 scenarijus <span id="heroSpxVal">$1.44M</span> - skirtumas auga eksponentiškai dėl compound efekto.
        <span class="hero-sub">Aktyvus pick'inimas duoda <span id="heroPct">168%</span> didesnį rezultatą po <span id="heroYearsLabel">10</span> metų.</span>
      </div>
    </div>
  </div>

  <div class="alloc-section">
    <div class="alloc-title">Alokacija pagal kategorijas</div>
    <div class="alloc-hint">Paspausk kategoriją - lentelėje pasišvies tik tos pozicijos</div>
    <div class="alloc-grid">
      <div class="donut-wrap">
        <div class="donut"></div>
        <div class="donut-center">
          <div class="big" id="donutCenterBig">{total_short}</div>
          <div class="sml" id="donutCenterSml">portfelio vertė</div>
        </div>
      </div>
      <div class="legend" id="legend">
        {legend_html}
      </div>
    </div>
  </div>

  <div class="section-header">
    <span>Dabartinės pozicijos</span>
    <span class="meta-inline">
      <span id="positionCounter">{active_count} pozicijos &bull; {cat_count} kategorijų</span>
      <button class="filter-reset" id="resetBtn">Rodyti viską</button>
    </span>
  </div>

  <div class="grid">
    <table>
      <thead>
        <tr><th>Tikeris</th><th>Kategorija</th><th class="num">Kiekis</th><th class="num">Avg / Inv.</th><th class="num">Vertė / +/-</th></tr>
      </thead>
      <tbody>
        {col1_html}
      </tbody>
    </table>
    <table>
      <thead>
        <tr><th>Tikeris</th><th>Kategorija</th><th class="num">Kiekis</th><th class="num">Avg / Inv.</th><th class="num">Vertė / +/-</th></tr>
      </thead>
      <tbody>
        {col2_html}
      </tbody>
    </table>
  </div>

  <div class="meta">
    <div>daug_pinigu - asmeninio portfelio inventorius</div>
    <div>Atnaujinta {TODAY.isoformat()} (auto)</div>
  </div>
</div>

<script>
(function() {{
  const legend = document.getElementById('legend');
  const resetBtn = document.getElementById('resetBtn');
  const counter = document.getElementById('positionCounter');
  const centerBig = document.getElementById('donutCenterBig');
  const centerSml = document.getElementById('donutCenterSml');
  const allRows = document.querySelectorAll('tbody tr');
  const TOTAL = {total:.2f};
  const DEFAULT_COUNTER = '{active_count} pozicijos &bull; {cat_count} kategorijų';
  const DEFAULT_BIG = '{total_short}';
  let activeCategory = null;

  function applyFilter(cat) {{
    if (!cat) {{
      document.body.classList.remove('filtering');
      allRows.forEach(r => r.classList.remove('dimmed', 'match'));
      document.querySelectorAll('.legend-item').forEach(i => i.classList.remove('active'));
      resetBtn.classList.remove('show');
      counter.innerHTML = DEFAULT_COUNTER;
      centerBig.textContent = DEFAULT_BIG;
      centerSml.textContent = 'portfelio vertė';
      activeCategory = null;
      return;
    }}
    document.body.classList.add('filtering');
    let matchCount = 0;
    let matchValue = 0;
    allRows.forEach(r => {{
      if (r.dataset.category === cat) {{
        r.classList.add('match'); r.classList.remove('dimmed');
        matchCount++;
        const valCell = r.querySelector('td.val');
        if (valCell) {{
          const txt = valCell.childNodes[0].textContent.replace(/[$,]/g, '');
          matchValue += parseFloat(txt) || 0;
        }}
      }} else {{
        r.classList.add('dimmed'); r.classList.remove('match');
      }}
    }});
    document.querySelectorAll('.legend-item').forEach(i => {{
      i.classList.toggle('active', i.dataset.category === cat);
    }});
    resetBtn.classList.add('show');
    counter.innerHTML = '<strong style="color:#fff">' + cat + '</strong> &bull; ' + matchCount + ' poz.';
    const pct = (matchValue / TOTAL * 100).toFixed(1);
    centerBig.textContent = '$' + Math.round(matchValue).toLocaleString('en-US');
    centerSml.textContent = pct + '% portfelio';
    activeCategory = cat;
  }}

  legend.addEventListener('click', (e) => {{
    const item = e.target.closest('.legend-item');
    if (!item) return;
    const cat = item.dataset.category;
    if (activeCategory === cat) applyFilter(null);
    else applyFilter(cat);
  }});
  resetBtn.addEventListener('click', () => applyFilter(null));
  document.addEventListener('keydown', (e) => {{
    if (e.key === 'Escape' && activeCategory) applyFilter(null);
  }});
}})();

/* Interactive projection chart */
(function() {{
  const CV = {total:.2f};
  const SV = {spx_value};
  const PMT = {meta['monthlyDCA']};
  const YR = {xirr_r/100};
  const SR = {spx_xirr/100};
  const W = 820, H = 380, PL = 70, PR = 30, PT = 20, PB = 40;
  const innerW = W - PL - PR;
  const innerH = H - PT - PB;
  const slider = document.getElementById('yearSlider');
  if (!slider) return;

  function fvDCA(pv, pmt, ar, months) {{
    if (months <= 0) return pv;
    const mr = Math.pow(1 + ar, 1/12) - 1;
    return pv * Math.pow(1 + mr, months) + pmt * (Math.pow(1 + mr, months) - 1) / mr * (1 + mr);
  }}

  function fmtBig(v) {{
    if (v >= 1e6) return '$' + (v/1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
    if (v >= 1e3) return '$' + Math.round(v/1e3).toLocaleString('en-US') + 'K';
    return '$' + Math.round(v).toLocaleString('en-US');
  }}

  function render(years) {{
    const months = years * 12;
    const steps = Math.min(160, Math.max(months, 40));
    const yourPts = [], spxPts = [];
    for (let i = 0; i <= steps; i++) {{
      const m = (months / steps) * i;
      yourPts.push({{x: m/12, y: fvDCA(CV, PMT, YR, m)}});
      spxPts.push({{x: m/12, y: fvDCA(SV, PMT, SR, m)}});
    }}
    const yF = yourPts[yourPts.length-1].y;
    const sF = spxPts[spxPts.length-1].y;
    const yMax = yF * 1.08;

    const xS = x => PL + (x / years) * innerW;
    const yS = y => PT + innerH - (y / yMax) * innerH;

    const pathStr = pts => pts.map((p,i) => (i===0?'M':'L') + ' ' + xS(p.x).toFixed(1) + ' ' + yS(p.y).toFixed(1)).join(' ');
    const areaStr = pts => pathStr(pts) + ' L ' + xS(years).toFixed(1) + ' ' + yS(0).toFixed(1) + ' L ' + xS(0).toFixed(1) + ' ' + yS(0).toFixed(1) + ' Z';

    document.getElementById('yourLine').setAttribute('d', pathStr(yourPts));
    document.getElementById('spxLine').setAttribute('d', pathStr(spxPts));
    document.getElementById('yourArea').setAttribute('d', areaStr(yourPts));
    document.getElementById('spxArea').setAttribute('d', areaStr(spxPts));
    document.getElementById('yourDot').setAttribute('cx', xS(years));
    document.getElementById('yourDot').setAttribute('cy', yS(yF));
    document.getElementById('spxDot').setAttribute('cx', xS(years));
    document.getElementById('spxDot').setAttribute('cy', yS(sF));

    let gridH = '', yLab = '';
    const N = 5;
    for (let i = 0; i <= N; i++) {{
      const v = (yMax / N) * i;
      const y = yS(v);
      gridH += '<line x1="' + PL + '" y1="' + y + '" x2="' + (W-PR) + '" y2="' + y + '" stroke="#1a2d4a" stroke-width="0.5"/>';
      yLab += '<text x="' + (PL-10) + '" y="' + (y+4) + '" fill="#6b8197" font-size="11" text-anchor="end" font-family="-apple-system, BlinkMacSystemFont, sans-serif">' + fmtBig(v) + '</text>';
    }}
    document.getElementById('gridGroup').innerHTML = gridH;
    document.getElementById('yLabels').innerHTML = yLab;

    let xLab = '';
    const xN = Math.min(years, 6);
    for (let i = 0; i <= xN; i++) {{
      const yr = (years / xN) * i;
      const x = xS(yr);
      xLab += '<text x="' + x + '" y="' + (H-PB+22) + '" fill="#6b8197" font-size="11" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, sans-serif">' + yr.toFixed(0) + 'm</text>';
    }}
    document.getElementById('xLabels').innerHTML = xLab;

    const sp = ((years - 1) / 29 * 100).toFixed(1);
    slider.style.background = 'linear-gradient(to right, #2dd4bf 0%, #2dd4bf ' + sp + '%, #1a2d4a ' + sp + '%, #1a2d4a 100%)';

    document.getElementById('yearVal').textContent = years;
    document.getElementById('heroMult').textContent = (yF/sF).toFixed(1) + 'x';
    document.getElementById('heroYears').textContent = years;
    document.getElementById('heroYearsLabel').textContent = years;
    document.getElementById('heroDiff').textContent = '+' + fmtBig(yF - sF);
    document.getElementById('heroYourVal').textContent = fmtBig(yF);
    document.getElementById('heroSpxVal').textContent = fmtBig(sF);
    document.getElementById('heroPct').textContent = ((yF/sF - 1) * 100).toFixed(0) + '%';
  }}

  slider.addEventListener('input', e => render(parseInt(e.target.value)));
  document.querySelectorAll('.slider-marks span').forEach(s => {{
    s.addEventListener('click', () => {{
      const y = parseInt(s.dataset.y);
      slider.value = y;
      render(y);
    }});
  }});
  render(10);
}})();
</script>
</body>
</html>
"""
    return template


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    print(f"Starting refresh @ {TODAY}")
    failures = refresh_prices(data)
    if failures:
        print(f"WARN: {len(failures)} failures:")
        for tk, err in failures:
            print(f"  {tk}: {err}")
    data["meta"]["lastUpdated"] = TODAY.isoformat()
    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    html = render(data)
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
