"""Global constants."""

from __future__ import annotations

# --- 初期資金 ---
START_CASH_JPY = 1_000_000.0

# --- 使用する時間足 ---
TIMEFRAMES = ["3H", "6H", "12H", "1D"]

# --- MAクロス ---
MA_FAST = 20
MA_SLOW = 50

# --- 指標パラメータ ---
REGIME_MA_LEN = 200
RSI_LEN = 14
ATR_LEN = 14
ATR_Q_LOOKBACK = 200
ADX_LEN = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
DON_TERM = 20

# --- ドンチャン・エグジット（タートル式: 短い逆方向チャネル割れで決済）---
DON_EXIT_TERM = 10

# --- ボリンジャーバンド（スクイーズ・ブレイクアウト）---
BB_LEN = 20
BB_STD = 2.0
BB_WIDTH_Q_LOOKBACK = 200

# --- Supertrend（トレンド追従トレーリングストップ）---
SUPERTREND_ATR_LEN = 10

# --- bitFlyer Crypto CFD (FX_BTC_JPY) のコスト ---
# 取引手数料は無料。建玉には日次でレバレッジ手数料がかかり、加えて 8 時間ごとに
# ファンディングレートを授受する（乖離がないときロングが 0.01% を支払いショートが受け取る）。
# 実際のファンディングレートは乖離に応じて変動するため、ここでは基準値を使う。
CFD_LEVERAGE_FEE_DAILY = 0.0004  # 建玉金額 × 0.04% / 日
CFD_FUNDING_DAILY = 0.0003  # 0.01% × 3 回 / 日（ロング支払い・ショート受け取り）

# --- 表示 ---
SHOW_LAST_N_MONTHS = 24
TZ_DISPLAY = "Asia/Tokyo"
