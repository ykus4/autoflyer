"""Strategy variant definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    name: str

    # エントリーフィルター
    use_ma200_filter: bool = False
    rsi_min: float | None = None
    atr_high_avoid: bool = False
    require_don_break: bool = False
    adx_min: float | None = None

    # ショートサイド（MA200 より下でクロスダウン時にショート）
    enable_short: bool = False

    # エグジット: 固定ストップ（エントリー価格 ± ATR × mult）
    atr_stop_mult: float = 0.0

    # エグジット: チャンデリアトレーリングストップ（ピーク ∓ ATR × mult）
    chandelier_mult: float = 0.0

    # サイジング: 残高 × risk_pct% をストップ幅でリスク管理（0 = 全額投入）
    risk_pct: float = 0.0

    # 損切り後の再エントリー禁止バー数（0 = 制限なし）
    cooldown_bars: int = 0

    # GARCH(1,1) ボラティリティターゲット（0 = 無効、例: 0.20 = 年率20%を目標に動的サイジング）
    garch_target_vol: float = 0.0


VARIANTS: list[Variant] = [
    # ベースライン
    Variant("BASE"),
    # フィルター単体
    Variant("MA200_FILTER", use_ma200_filter=True),
    Variant("ADX20", adx_min=20.0),
    Variant("STOP_3ATR", atr_stop_mult=3.0),
    Variant("ATR_AVOID", atr_high_avoid=True),
    # 複合フィルター
    Variant("MA200_STOP3", use_ma200_filter=True, atr_stop_mult=3.0),
    Variant("MA200_STOP2", use_ma200_filter=True, atr_stop_mult=2.0),
    Variant("ADX20_STOP3", adx_min=20.0, atr_stop_mult=3.0),
    Variant("MA200_ADX20_STOP3", use_ma200_filter=True, adx_min=20.0, atr_stop_mult=3.0),
    Variant("MA200_ATRAVOID", use_ma200_filter=True, atr_high_avoid=True),
    # リスクベースサイジング（残高の N% をリスク、ストップ=3ATR）
    Variant("RISK1PCT_STOP3", atr_stop_mult=3.0, risk_pct=1.0),
    Variant("RISK2PCT_STOP3", atr_stop_mult=3.0, risk_pct=2.0),
    Variant("MA200_RISK1PCT_STOP3", use_ma200_filter=True, atr_stop_mult=3.0, risk_pct=1.0),
    Variant("MA200_RISK2PCT_STOP3", use_ma200_filter=True, atr_stop_mult=3.0, risk_pct=2.0),
    # チャンデリアトレーリングストップ
    Variant("CHAN_3ATR", chandelier_mult=3.0),
    Variant("CHAN_2ATR", chandelier_mult=2.0),
    Variant("MA200_CHAN3", use_ma200_filter=True, chandelier_mult=3.0),
    Variant("MA200_CHAN2", use_ma200_filter=True, chandelier_mult=2.0),
    # ドンチャンブレークアウト確認
    Variant("DON_BREAK", require_don_break=True),
    Variant("DON_BREAK_STOP3", require_don_break=True, atr_stop_mult=3.0),
    Variant("MA200_DON_STOP3", use_ma200_filter=True, require_don_break=True, atr_stop_mult=3.0),
    # OOS 検証済みベスト: MA200 + 固定ストップ + リスクサイジング
    Variant("MA200_STOP2_RISK2PCT", use_ma200_filter=True, atr_stop_mult=2.0, risk_pct=2.0),
    Variant("MA200_STOP2_RISK1PCT", use_ma200_filter=True, atr_stop_mult=2.0, risk_pct=1.0),
    # GARCH ボラティリティターゲットサイジング
    Variant("MA200_STOP2_GARCH20", use_ma200_filter=True, atr_stop_mult=2.0, garch_target_vol=0.20),
    Variant("MA200_STOP2_GARCH30", use_ma200_filter=True, atr_stop_mult=2.0, garch_target_vol=0.30),
    Variant("MA200_GARCH20", use_ma200_filter=True, garch_target_vol=0.20),
    Variant("MA200_GARCH30", use_ma200_filter=True, garch_target_vol=0.30),
    # グリッドサーチ最優秀（8年・Calmar 1.20）: 推奨本番設定
    Variant("MA200_STOP1.5ATR_GARCH40", use_ma200_filter=True, atr_stop_mult=1.5, garch_target_vol=0.40),
    # 広いストップ（1D 向け）
    Variant("STOP_4ATR", atr_stop_mult=4.0),
    Variant("STOP_5ATR", atr_stop_mult=5.0),
    # ショート両面対応
    Variant("SHORT_STOP3", enable_short=True, atr_stop_mult=3.0),
    Variant("MA200_SHORT_STOP3", use_ma200_filter=True, enable_short=True, atr_stop_mult=3.0),
]
