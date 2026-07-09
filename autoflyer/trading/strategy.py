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

    # エグジット: 利確ターゲット（エントリー価格 + ATR × tp_atr_mult, 0 = 無効）
    tp_atr_mult: float = 0.0

    # エグジット: 利確後トレーリングへ移行（tp到達後、chandelier的にストップを追従）
    tp_trail_mult: float = 0.0

    # エントリー: ドンチャンブレイクアウトエントリー（MA クロスではなくブレイクアウトでエントリー）
    breakout_entry: bool = False

    # サイジング: 残高 × risk_pct% をストップ幅でリスク管理（0 = 全額投入）
    risk_pct: float = 0.0

    # 損切り後の再エントリー禁止バー数（0 = 制限なし）
    cooldown_bars: int = 0

    # GARCH(1,1) ボラティリティターゲット（0 = 無効、例: 0.20 = 年率20%を目標に動的サイジング）
    garch_target_vol: float = 0.0

    # --- 統計フィルター ---
    # Hurst指数フィルター: H > hurst_min のときのみエントリー（0 = 無効）
    hurst_min: float = 0.0

    # HMMレジームフィルター: bull(2)のときのみエントリー（MA200の代替）
    use_hmm_regime: bool = False

    # Kelly基準サイジング: GARCHの代わりにKellyでサイズ決定
    use_kelly_sizing: bool = False

    # ブレイクアウトz-scoreフィルター: z > zscore_min のときのみエントリー（0 = 無効）
    zscore_min: float = 0.0

    # MAEベースストップ: 過去の逆行統計からストップ幅を動的計算（atr_stop_multの代替）
    use_mae_stop: bool = False

    # Supertrend トレーリングストップ: Supertrend ラインをストップに使う（0 = 無効）。
    # atr_stop_mult / chandelier_mult より優先。一般的な既定は 3.0。
    supertrend_mult: float = 0.0


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
    Variant(
        "MA200_STOP1.5ATR_GARCH40", use_ma200_filter=True, atr_stop_mult=1.5, garch_target_vol=0.40
    ),
    # 広いストップ（1D 向け）
    Variant("STOP_4ATR", atr_stop_mult=4.0),
    Variant("STOP_5ATR", atr_stop_mult=5.0),
    # ショート両面対応
    Variant("SHORT_STOP3", enable_short=True, atr_stop_mult=3.0),
    Variant("MA200_SHORT_STOP3", use_ma200_filter=True, enable_short=True, atr_stop_mult=3.0),
    # === 利確ロジック付きバリアント ===
    # ATRベース利確ターゲット（ストップの2〜4倍のR倍率で利確）
    Variant(
        "MA200_STOP1.5_TP3ATR_GARCH40",
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=3.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "MA200_STOP1.5_TP4ATR_GARCH40",
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=4.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "MA200_STOP1.5_TP6ATR_GARCH40",
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=6.0,
        garch_target_vol=0.40,
    ),
    # 利確到達後トレーリングに移行（利益を伸ばす）
    Variant(
        "MA200_STOP1.5_TP3_TRAIL2_GARCH40",
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=3.0,
        tp_trail_mult=2.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "MA200_STOP1.5_TP4_TRAIL2_GARCH40",
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=4.0,
        tp_trail_mult=2.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "MA200_STOP2_TP4_TRAIL2_GARCH30",
        use_ma200_filter=True,
        atr_stop_mult=2.0,
        tp_atr_mult=4.0,
        tp_trail_mult=2.0,
        garch_target_vol=0.30,
    ),
    # === ドンチャンブレイクアウトエントリー ===
    Variant(
        "BREAKOUT_STOP2_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=2.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "BREAKOUT_STOP1.5_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
    ),
    Variant(
        "BREAKOUT_STOP2_TP4_TRAIL2_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=2.0,
        tp_atr_mult=4.0,
        tp_trail_mult=2.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "BREAKOUT_STOP1.5_TP3_TRAIL2_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=3.0,
        tp_trail_mult=2.0,
        garch_target_vol=0.40,
    ),
    # === ハイブリッド: MAクロス + ブレイクアウト確認 + 利確トレーリング ===
    Variant(
        "HYBRID_DON_STOP1.5_TP4_TRAIL2_GARCH40",
        use_ma200_filter=True,
        require_don_break=True,
        atr_stop_mult=1.5,
        tp_atr_mult=4.0,
        tp_trail_mult=2.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "HYBRID_DON_STOP2_TP6_TRAIL3_GARCH40",
        use_ma200_filter=True,
        require_don_break=True,
        atr_stop_mult=2.0,
        tp_atr_mult=6.0,
        tp_trail_mult=3.0,
        garch_target_vol=0.40,
    ),
    # リスクベース + 利確
    Variant(
        "MA200_RISK2_STOP2_TP4_GARCH30",
        use_ma200_filter=True,
        atr_stop_mult=2.0,
        tp_atr_mult=4.0,
        risk_pct=2.0,
        garch_target_vol=0.30,
    ),
    Variant(
        "MA200_RISK2_STOP1.5_TP3_TRAIL2_GARCH40",
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        tp_atr_mult=3.0,
        tp_trail_mult=2.0,
        risk_pct=2.0,
        garch_target_vol=0.40,
    ),
    # === 統計フィルター バリアント ===
    # Hurst指数: トレンド確認済みのみエントリー
    Variant(
        "BREAKOUT_HURST55_STOP1.5_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
        hurst_min=0.55,
    ),
    Variant(
        "BREAKOUT_HURST60_STOP1.5_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
        hurst_min=0.60,
    ),
    # HMMレジーム: MA200の代わりに統計的レジーム判定
    Variant(
        "BREAKOUT_HMM_STOP1.5_GARCH40",
        breakout_entry=True,
        use_hmm_regime=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
    ),
    Variant(
        "BREAKOUT_HMM_HURST55_STOP1.5_GARCH40",
        breakout_entry=True,
        use_hmm_regime=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
        hurst_min=0.55,
    ),
    # Kelly基準サイジング: GARCHの代わりにKelly
    Variant(
        "BREAKOUT_MA200_STOP1.5_KELLY",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        use_kelly_sizing=True,
    ),
    # z-scoreフィルター: 統計的に有意なブレイクアウトのみ
    Variant(
        "BREAKOUT_Z2_MA200_STOP1.5_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
        zscore_min=2.0,
    ),
    Variant(
        "BREAKOUT_Z1.5_MA200_STOP1.5_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
        zscore_min=1.5,
    ),
    # MAEベースストップ: 統計的に最適なストップ幅
    Variant(
        "BREAKOUT_MAE_MA200_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        garch_target_vol=0.40,
        use_mae_stop=True,
    ),
    # フル統計: 全部入り
    Variant(
        "STAT_FULL_BREAKOUT",
        breakout_entry=True,
        use_hmm_regime=True,
        garch_target_vol=0.40,
        hurst_min=0.55,
        zscore_min=1.5,
        use_mae_stop=True,
    ),
    # Kelly + Hurst + MA200
    Variant(
        "BREAKOUT_HURST55_MA200_KELLY",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        use_kelly_sizing=True,
        hurst_min=0.55,
    ),
    # GARCH + Hurst + z-score (統計サイジング + 統計フィルター)
    Variant(
        "BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.5,
        garch_target_vol=0.40,
        hurst_min=0.55,
        zscore_min=1.5,
    ),
    # === 新推奨: タイトな 1.0ATR ストップ（損切りを速め、PF/DD/リターンを同時改善）===
    # グリッドサーチで発見（2022–2026: +178%, PF 3.79, DD 24.7% / full: +1405%, PF 4.01）
    Variant(
        "BREAKOUT_STOP1.0_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.0,
        garch_target_vol=0.40,
    ),
    # 最大リターン狙い（GARCH50 でボラ目標を引き上げ: 2022–2026 +194%, PF 3.71, DD 26.9%）
    Variant(
        "BREAKOUT_STOP1.0_GARCH50",
        breakout_entry=True,
        use_ma200_filter=True,
        atr_stop_mult=1.0,
        garch_target_vol=0.50,
    ),
    # === Supertrend トレーリングストップ バリアント ===
    # Supertrend ラインでトレンドを追従し、フリップで決済（利を伸ばしつつ守る）
    # ST4.0 が最良（2022–2026: +166%, PF 3.17, WR 52.6%）
    Variant(
        "BREAKOUT_SUPERTREND4_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        supertrend_mult=4.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "BREAKOUT_SUPERTREND3_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        supertrend_mult=3.0,
        garch_target_vol=0.40,
    ),
    Variant(
        "BREAKOUT_SUPERTREND2_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        supertrend_mult=2.0,
        garch_target_vol=0.40,
    ),
    # MA クロスエントリー + Supertrend トレーリング
    Variant(
        "MA200_SUPERTREND3_GARCH40",
        use_ma200_filter=True,
        supertrend_mult=3.0,
        garch_target_vol=0.40,
    ),
    # Supertrend + Hurst フィルター（トレンド確認済みのみ）
    Variant(
        "BREAKOUT_HURST55_SUPERTREND3_GARCH40",
        breakout_entry=True,
        use_ma200_filter=True,
        supertrend_mult=3.0,
        garch_target_vol=0.40,
        hurst_min=0.55,
    ),
]
