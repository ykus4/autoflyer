"""Lightweight web dashboard for monitoring the live bot."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .bot import BitFlyerClient

app = FastAPI(title="autoflyer dashboard")
_security = HTTPBasic()
_security_dep = Depends(_security)

_STATE_FILE = Path("state.json")
_EQUITY_FILE = Path("equity.jsonl")
_LOG_FILE: Path | None = None
_SYMBOL = "FX_BTC_JPY"
_ex: BitFlyerClient | None = None
_DASHBOARD_USER = ""
_DASHBOARD_PASS = ""


def set_paths(
    state: Path,
    log: Path | None = None,
    symbol: str = "FX_BTC_JPY",
    api_key: str = "",
    api_secret: str = "",
    dashboard_user: str = "",
    dashboard_pass: str = "",
) -> None:
    global _STATE_FILE, _EQUITY_FILE, _LOG_FILE, _SYMBOL, _ex, _DASHBOARD_USER, _DASHBOARD_PASS
    _STATE_FILE = state
    _EQUITY_FILE = state.with_name("equity.jsonl")
    _LOG_FILE = log
    _SYMBOL = symbol
    _ex = BitFlyerClient(api_key, api_secret)
    _DASHBOARD_USER = dashboard_user
    _DASHBOARD_PASS = dashboard_pass


def _auth(credentials: HTTPBasicCredentials = _security_dep) -> str:
    if not _DASHBOARD_USER:
        return credentials.username
    ok = secrets.compare_digest(credentials.username, _DASHBOARD_USER) and secrets.compare_digest(
        credentials.password, _DASHBOARD_PASS
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _read_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _read_logs(n: int = 80) -> list[str]:
    if _LOG_FILE is None or not _LOG_FILE.exists():
        return []
    try:
        lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


@app.get("/api/state")
def api_state(_: str = Depends(_auth)) -> dict:
    return _read_state()


@app.get("/api/ticker")
def api_ticker(_: str = Depends(_auth)) -> dict:
    """現在価格・含み損益・残高をccxtで取得して返す。"""
    state = _read_state()
    result: dict = {
        "last_price": None,
        "bid": None,
        "ask": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
        "jpy_balance": None,
        "btc_balance": None,
        "error": None,
    }

    if _ex is None:
        result["error"] = "exchange not initialized"
        return result

    try:
        ticker = _ex.fetch_ticker(_SYMBOL)
        last = float(ticker["last"])
        result["last_price"] = last

        # 含み損益（ポジション保有中のみ）
        if state.get("in_pos") and state.get("entry_price") and state.get("btc"):
            entry = float(state["entry_price"])
            btc = float(state["btc"])
            pnl = (last - entry) * btc
            pnl_pct = (last / entry - 1) * 100
            result["unrealized_pnl"] = round(pnl)
            result["unrealized_pnl_pct"] = round(pnl_pct, 2)
    except Exception as e:
        result["error"] = f"ticker: {e}"

    # 残高
    if _ex._key:
        try:
            bal = _ex.fetch_balance()
            result["jpy_balance"] = bal.get("JPY", {}).get("free")
            result["btc_balance"] = bal.get("BTC", {}).get("free")
        except Exception as e:
            result["error"] = (result["error"] or "") + f" balance: {e}"

    return result


@app.get("/api/equity")
def api_equity(n: int = 500, _: str = Depends(_auth)) -> dict:
    """直近n件の資産推移を返す。"""
    if not _EQUITY_FILE.exists():
        return {"labels": [], "values": []}
    try:
        lines = _EQUITY_FILE.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines[-n:] if line.strip()]
        labels = [r["dt"][:16].replace("T", " ") for r in rows]
        values = [round(r["equity"]) for r in rows]
        return {"labels": labels, "values": values}
    except Exception:
        return {"labels": [], "values": []}


@app.get("/api/logs")
def api_logs(n: int = 80, _: str = Depends(_auth)) -> dict:
    return {"lines": _read_logs(n)}


@app.get("/", response_class=HTMLResponse)
def index(_: str = Depends(_auth)) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>autoflyer bot</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
          background: #0f1117; color: #e2e8f0; padding: 16px; }}
  h1 {{ font-size: 1.15rem; font-weight: 700; margin-bottom: 4px; color: #f7fafc; }}
  h2 {{ font-size: .8rem; color: #4a5568; font-weight: 500;
        text-transform: uppercase; letter-spacing: .06em; margin: 18px 0 8px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 6px; }}
  .card {{ background: #1a1d27; border-radius: 10px; padding: 14px 16px; }}
  .card .label {{ font-size: .68rem; color: #718096; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 5px; }}
  .card .value {{ font-size: 1.3rem; font-weight: 700; line-height: 1.2; }}
  .card .sub   {{ font-size: .7rem; color: #718096; margin-top: 3px; }}
  .pos  {{ color: #68d391; }}
  .neg  {{ color: #fc8181; }}
  .neu  {{ color: #e2e8f0; }}
  .badge {{ display:inline-block; padding:3px 12px; border-radius:999px;
            font-size:.75rem; font-weight:700; }}
  .badge.long {{ background:#276749; color:#9ae6b4; }}
  .badge.flat {{ background:#2d3748; color:#a0aec0; }}
  pre {{ background:#1a1d27; border-radius:10px; padding:14px;
         font-size:.7rem; line-height:1.6; overflow-x:auto;
         white-space:pre-wrap; word-break:break-all;
         max-height:380px; overflow-y:auto; color:#718096; }}
  .ts  {{ font-size:.68rem; color:#4a5568; margin: 6px 0 14px; }}
  .err {{ font-size:.7rem; color:#fc8181; margin-top:4px; }}
  .btn {{ background:#2d3748; border:none; color:#a0aec0; padding:6px 14px;
          border-radius:6px; cursor:pointer; font-size:.8rem; }}
  .btn:hover {{ background:#3d4a5c; }}
  .chart-wrap {{ background:#1a1d27; border-radius:10px; padding:16px; margin-bottom:6px; }}
</style>
</head>
<body>
<h1>autoflyer bot</h1>
<div class="ts" id="ts">最終更新: {now}</div>
<button class="btn" onclick="load()">↻ 更新</button>

<h2>ポジション</h2>
<div class="grid" id="pos-cards">読み込み中...</div>

<h2>現在価格 / 残高</h2>
<div class="grid" id="mkt-cards">読み込み中...</div>

<div class="err" id="err"></div>

<h2>資産推移</h2>
<div class="chart-wrap">
  <canvas id="equityChart" height="90"></canvas>
</div>

<h2>ログ</h2>
<pre id="logs">読み込み中...</pre>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
let equityChart = null;

function initChart(labels, values) {{
  const ctx = document.getElementById('equityChart').getContext('2d');
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels,
      datasets: [{{
        label: '資産 (円)',
        data: values,
        borderColor: '#68d391',
        backgroundColor: 'rgba(104,211,145,0.08)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        tension: 0.2,
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => Math.round(ctx.parsed.y).toLocaleString('ja-JP') + ' 円'
          }}
        }}
      }},
      scales: {{
        x: {{ ticks: {{ color:'#4a5568', maxTicksLimit:8 }}, grid: {{ color:'#1e2535' }} }},
        y: {{
          ticks: {{ color:'#4a5568', callback: v => (v/10000).toFixed(0)+'万' }},
          grid: {{ color:'#1e2535' }}
        }}
      }}
    }}
  }});
}}

function fmt(n) {{
  if (n == null) return '—';
  return Math.round(n).toLocaleString('ja-JP');
}}
function fmtBtc(n) {{
  if (n == null) return '—';
  return Number(n).toFixed(6);
}}
function fmtPct(n) {{
  if (n == null) return '';
  const cls = n >= 0 ? 'pos' : 'neg';
  const sign = n >= 0 ? '+' : '';
  return `<span class="${{cls}}">${{sign}}${{n.toFixed(2)}}%</span>`;
}}

async function load() {{
  const [sr, tr, lr, eq] = await Promise.all([
    fetch('/api/state').then(r => r.json()),
    fetch('/api/ticker').then(r => r.json()),
    fetch('/api/logs').then(r => r.json()),
    fetch('/api/equity').then(r => r.json()),
  ]);

  const inPos = sr.in_pos;
  const badge = inPos
    ? '<span class="badge long">LONG</span>'
    : '<span class="badge flat">FLAT</span>';

  const pnlCls = (tr.unrealized_pnl ?? 0) >= 0 ? 'pos' : 'neg';
  const pnlSign = (tr.unrealized_pnl ?? 0) >= 0 ? '+' : '';

  // ポジション情報
  document.getElementById('pos-cards').innerHTML = `
    <div class="card">
      <div class="label">ポジション</div>
      <div class="value">${{badge}}</div>
      <div class="sub">${{sr.entry_dt ? sr.entry_dt.slice(0,10) : '—'}}</div>
    </div>
    <div class="card">
      <div class="label">エントリー価格</div>
      <div class="value neu">${{fmt(sr.entry_price)}} 円</div>
    </div>
    <div class="card">
      <div class="label">保有 BTC</div>
      <div class="value neu">${{fmtBtc(sr.btc)}}</div>
    </div>
    <div class="card">
      <div class="label">ストップ価格</div>
      <div class="value neg">${{fmt(sr.stop_px)}} 円</div>
      ${{sr.entry_price && sr.stop_px
        ? `<div class="sub">下落幅 ${{(((sr.stop_px - sr.entry_price) / sr.entry_price)*100).toFixed(1)}}%</div>`
        : ''}}
    </div>
    <div class="card">
      <div class="label">含み損益</div>
      <div class="value ${{pnlCls}}">${{tr.unrealized_pnl != null ? pnlSign + fmt(tr.unrealized_pnl) + ' 円' : '—'}}</div>
      <div class="sub">${{fmtPct(tr.unrealized_pnl_pct)}}</div>
    </div>
    <div class="card">
      <div class="label">ピーク資産</div>
      <div class="value pos">${{fmt(sr.peak_cash)}} 円</div>
    </div>
  `;

  // 市場情報
  document.getElementById('mkt-cards').innerHTML = `
    <div class="card">
      <div class="label">現在価格</div>
      <div class="value neu">${{fmt(tr.last_price)}} 円</div>
      <div class="sub">Bid ${{fmt(tr.bid)}} / Ask ${{fmt(tr.ask)}}</div>
    </div>
    <div class="card">
      <div class="label">JPY 残高</div>
      <div class="value neu">${{fmt(tr.jpy_balance)}} 円</div>
    </div>
    <div class="card">
      <div class="label">BTC 残高</div>
      <div class="value neu">${{fmtBtc(tr.btc_balance)}}</div>
    </div>
  `;

  if (eq.labels.length > 1) initChart(eq.labels, eq.values);
  document.getElementById('err').textContent = tr.error ? '⚠ ' + tr.error : '';
  document.getElementById('logs').textContent = lr.lines.join('\\n');
  document.getElementById('ts').textContent = '最終更新: ' + new Date().toLocaleString('ja-JP');
}}

load();
setInterval(load, 15000);  // 15秒自動更新
</script>
</body>
</html>"""
