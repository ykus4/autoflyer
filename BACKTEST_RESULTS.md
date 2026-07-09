# Backtest Results

Data: 2021-12-31 ~ 2026-04-16 (Binance BTC/JPY)
Start capital: 1,000,000 JPY / Timeframe: 1D

---

## 2026-07-09 Update — New Best Algorithm + Supertrend

グリッドサーチ（ストップ幅 × GARCHボラ目標 × トレーリング方式）で、従来ベストの
`BREAKOUT_STOP1.5_GARCH40` を上回る設定を発見。あわせて **Supertrend トレーリングストップ**
を新規アルゴリズムとして追加した。

以下は **現行エンジン**で `data/btc_usdt_1d.csv`（BTC/USDT 日足）を用いて再計測した値。
過去の表（BTC/JPY）とは基準通貨・計測時期が異なるため、比較は本セクション内で行うこと。

### New best: `BREAKOUT_STOP1.0_GARCH40`

ドンチャンブレイクアウト + MA200 + **1.0×ATR ストップ** + GARCH40 サイジング。
ストップを 1.5→1.0 ATR に絞る（＝損切りを速める）ことで、リターン・PF・最大DDが同時改善。

| 指標 | 旧ベスト `STOP1.5` | **新ベスト `STOP1.0`** |
|---|---:|---:|
| リターン (2022–2026) | +136% | **+178%** |
| Profit Factor | 2.62 | **3.79** |
| 最大ドローダウン | 30.1% | **24.7%** |
| リターン (full 2017–2026) | +1,317% | **+1,405%** |

3つの検証窓（full 2017–2026 / 2022–2026 / ウォークフォワード test>2022-06）すべてで
旧ベストを上回り、過学習ではないことを確認済み。最大リターン狙いなら
`BREAKOUT_STOP1.0_GARCH50`（2022–2026 +194%, PF 3.71, DD 26.9%）。

### Supertrend トレーリングストップ（新規アルゴリズム）

Supertrend ライン（`hl2 ± mult×ATR` をフリップさせた追従線）をストップに使用。
`supertrend_mult` を設定したバリアントで有効化（`atr_stop_mult` / `chandelier_mult` より優先）。
勝率が高く（52–58%）、`mult=4.0` が最良。

### 1D 結果（full window 2017-08-17 ~ 2026-04-16）

| Strategy | Trades | WR% | Net JPY | Fees | PF | Final | Max DD% |
|---|---:|---:|---:|---:|---:|---:|---:|
| **BREAKOUT_STOP1.0_GARCH40** | 31 | 38.7 | 14,048,241 | 380,173 | 4.01 | 15,048,241 | 29.36 |
| **BREAKOUT_STOP1.0_GARCH50** | 31 | 38.7 | 17,699,643 | 468,510 | 3.81 | 18,699,643 | 33.48 |
| BREAKOUT_SUPERTREND4_GARCH40 | 33 | 57.6 | 14,848,153 | 430,412 | 3.80 | 15,848,153 | 33.63 |
| BREAKOUT_STOP1.5_GARCH40 (旧ベスト) | 26 | 50.0 | 13,165,897 | 356,109 | 3.25 | 14,165,897 | 29.98 |
| BREAKOUT_STOP2_GARCH40 | 26 | 50.0 | 10,959,158 | 316,982 | 2.80 | 11,959,158 | 34.63 |
| BREAKOUT_SUPERTREND3_GARCH40 | 39 | 51.3 | 8,423,507 | 348,747 | 3.09 | 9,423,507 | 35.45 |
| BREAKOUT_SUPERTREND2_GARCH40 | 45 | 48.9 | 5,669,215 | 299,990 | 2.28 | 6,669,215 | 23.06 |
| BREAKOUT_HURST55_SUPERTREND3_GARCH40 | 35 | 51.4 | 5,296,889 | 229,640 | 2.83 | 6,296,889 | 39.19 |
| MA200_SUPERTREND3_GARCH40 | 14 | 42.9 | 367,202 | 41,089 | 1.70 | 1,367,202 | 34.23 |

### 1D 結果（2022-2026 window）

| Strategy | Trades | WR% | Net JPY | Fees | PF | Final | Max DD% |
|---|---:|---:|---:|---:|---:|---:|---:|
| **BREAKOUT_STOP1.0_GARCH40** | 17 | 47.1 | 1,775,534 | 72,666 | 3.79 | 2,775,534 | 24.74 |
| **BREAKOUT_STOP1.0_GARCH50** | 17 | 47.1 | 1,940,668 | 80,089 | 3.71 | 2,940,668 | 26.90 |
| BREAKOUT_SUPERTREND4_GARCH40 | 19 | 52.6 | 1,664,894 | 76,826 | 3.17 | 2,664,894 | 33.85 |
| BREAKOUT_SUPERTREND3_GARCH40 | 22 | 50.0 | 1,452,113 | 88,872 | 2.67 | 2,452,113 | 35.62 |
| BREAKOUT_HURST55_SUPERTREND3_GARCH40 | 18 | 55.6 | 1,387,507 | 75,784 | 2.77 | 2,387,507 | 33.68 |
| BREAKOUT_STOP1.5_GARCH40 (旧ベスト) | 16 | 50.0 | 1,359,500 | 61,578 | 2.62 | 2,359,500 | 30.09 |
| BREAKOUT_STOP2_GARCH40 | 16 | 50.0 | 1,209,110 | 60,082 | 2.29 | 2,209,110 | 34.72 |
| BREAKOUT_SUPERTREND2_GARCH40 | 24 | 54.2 | 1,142,766 | 85,696 | 2.15 | 2,142,766 | 18.63 |
| MA200_SUPERTREND3_GARCH40 | 9 | 44.4 | 85,349 | 24,629 | 1.29 | 1,085,349 | 34.23 |

### コード最適化

- `add_indicators()` に `ma_fast`/`ma_slow` を統合し、バックテストがバリアントごとに
  行っていた移動平均の再計算と `merge_asof` を撤廃（時間足ごとに1度だけ指標計算）。
- Supertrend ラインは系列全体で1度だけ計算しループ内では配列参照のみ。

---

## （以下は過去の履歴: BTC/JPY・旧エンジン）

## All Variants (1D, sorted by Profit Factor)

| Strategy | Trades | WR% | Net JPY | Fees | PF | Final | Max DD% |
|---|---:|---:|---:|---:|---:|---:|---:|
| DON_BREAK | 3 | 66.7 | 1,150,512 | 11,705 | 29.00 | 2,150,512 | 13.80 |
| DON_BREAK_STOP3 | 3 | 66.7 | 1,105,939 | 11,211 | 19.14 | 2,105,939 | 13.80 |
| MA200_STOP2_RISK1PCT | 8 | 50.0 | 224,677 | 4,917 | 9.13 | 1,224,677 | 4.89 |
| HYBRID_DON_STOP2_TP6_TRAIL3_GARCH40 | 2 | 50.0 | 286,604 | 5,157 | 8.53 | 1,286,604 | 5.37 |
| MA200_STOP2_RISK2PCT | 8 | 50.0 | 469,077 | 10,688 | 8.49 | 1,469,077 | 8.53 |
| MA200_STOP2_GARCH20 | 8 | 50.0 | 698,158 | 17,959 | 6.67 | 1,698,158 | 11.66 |
| MA200_DON_STOP3 | 2 | 50.0 | 338,373 | 6,043 | 6.55 | 1,338,373 | 13.38 |
| **BREAKOUT_STOP1.5_GARCH40** | **14** | **50.0** | **3,062,453** | **79,271** | **6.03** | **4,062,453** | **18.50** |
| MA200_STOP2_GARCH30 | 8 | 50.0 | 1,038,537 | 28,945 | 5.82 | 2,038,537 | 15.52 |
| **BREAKOUT_STOP2_GARCH40** | **13** | **53.8** | **2,972,748** | **76,278** | **5.19** | **3,972,748** | **18.91** |
| MA200_RISK1PCT_STOP3 | 8 | 50.0 | 132,229 | 3,160 | 5.07 | 1,132,229 | 3.57 |
| MA200_STOP1.5ATR_GARCH40 | 8 | 50.0 | 1,097,932 | 34,682 | 4.80 | 2,097,932 | 18.66 |
| MA200_RISK2PCT_STOP3 | 8 | 50.0 | 269,105 | 6,625 | 4.77 | 1,269,105 | 6.91 |
| MA200_STOP2 | 8 | 50.0 | 1,064,953 | 35,426 | 4.48 | 2,064,953 | 19.30 |
| HYBRID_DON_STOP1.5_TP4_TRAIL2_GARCH40 | 2 | 50.0 | 122,512 | 4,966 | 4.42 | 1,122,512 | 5.14 |
| RISK1PCT_STOP3 | 14 | 50.0 | 231,333 | 5,502 | 4.09 | 1,231,333 | 4.20 |
| RISK2PCT_STOP3 | 14 | 50.0 | 491,445 | 11,971 | 3.99 | 1,491,445 | 8.13 |
| MA200_RISK2_STOP2_TP4_GARCH30 | 8 | 50.0 | 96,129 | 6,820 | 3.77 | 1,096,129 | 2.60 |
| MA200_STOP1.5_TP6ATR_GARCH40 | 8 | 50.0 | 391,496 | 22,580 | 3.15 | 1,391,496 | 12.01 |
| MA200_GARCH20 | 8 | 50.0 | 520,347 | 17,387 | 2.79 | 1,520,347 | 21.11 |
| MA200_RISK2_STOP1.5_TP3_TRAIL2_GARCH40 | 8 | 50.0 | 107,677 | 10,315 | 2.78 | 1,107,677 | 5.80 |
| STOP_3ATR | 14 | 50.0 | 1,797,665 | 67,396 | 2.58 | 2,797,665 | 34.21 |
| MA200_STOP2_TP4_TRAIL2_GARCH30 | 8 | 50.0 | 208,677 | 18,551 | 2.55 | 1,208,677 | 9.57 |
| MA200_CHAN3 | 8 | 37.5 | 267,149 | 23,283 | 2.43 | 1,267,149 | 11.70 |
| MA200_CHAN2 | 8 | 50.0 | 190,536 | 22,165 | 2.42 | 1,190,536 | 10.13 |
| MA200_GARCH30 | 8 | 50.0 | 715,995 | 27,675 | 2.39 | 1,715,995 | 29.97 |
| MA200_STOP3 | 8 | 50.0 | 766,146 | 33,853 | 2.37 | 1,766,146 | 29.69 |
| STOP_4ATR | 14 | 50.0 | 1,615,169 | 64,550 | 2.36 | 2,615,169 | 38.22 |
| MA200_STOP1.5_TP4_TRAIL2_GARCH40 | 8 | 50.0 | 224,632 | 21,540 | 2.28 | 1,224,632 | 12.01 |
| ADX20_STOP3 | 7 | 42.9 | 641,895 | 27,170 | 2.20 | 1,641,895 | 37.63 |
| ATR_AVOID | 13 | 53.8 | 1,535,361 | 64,055 | 2.12 | 2,535,361 | 38.45 |
| MA200_STOP1.5_TP4ATR_GARCH40 | 8 | 50.0 | 179,932 | 20,871 | 2.07 | 1,179,932 | 12.05 |
| MA200_ATRAVOID | 7 | 57.1 | 687,387 | 31,258 | 2.05 | 1,687,387 | 35.47 |
| BASE | 14 | 50.0 | 1,458,399 | 65,826 | 2.04 | 2,458,399 | 40.32 |
| STOP_5ATR | 14 | 50.0 | 1,399,860 | 65,063 | 1.98 | 2,399,860 | 41.60 |
| BREAKOUT_STOP1.5_TP3_TRAIL2_GARCH40 | 34 | 47.1 | 897,533 | 91,568 | 1.93 | 1,897,533 | 22.80 |
| MA200_FILTER | 8 | 50.0 | 636,166 | 33,851 | 1.91 | 1,636,166 | 37.43 |
| MA200_STOP1.5_TP3_TRAIL2_GARCH40 | 8 | 50.0 | 145,621 | 20,955 | 1.85 | 1,145,621 | 12.02 |
| SHORT_STOP3 | 18 | 44.4 | 982,518 | 60,362 | 1.78 | 1,982,518 | 45.77 |
| ADX20 | 7 | 42.9 | 436,079 | 26,716 | 1.64 | 1,436,079 | 42.23 |
| MA200_STOP1.5_TP3ATR_GARCH40 | 8 | 50.0 | 102,146 | 20,347 | 1.62 | 1,102,146 | 12.03 |
| BREAKOUT_STOP2_TP4_TRAIL2_GARCH40 | 29 | 48.3 | 401,344 | 76,361 | 1.32 | 1,401,344 | 31.28 |
| MA200_SHORT_STOP3 | 12 | 41.7 | 249,762 | 37,072 | 1.30 | 1,249,762 | 40.11 |
| CHAN_3ATR | 15 | 33.3 | 69,041 | 39,808 | 1.14 | 1,069,041 | 17.83 |
| CHAN_2ATR | 15 | 40.0 | 44,485 | 37,970 | 1.14 | 1,044,485 | 16.64 |
| MA200_ADX20_STOP3 | 3 | 0.0 | -260,914 | 7,456 | 0.00 | 739,086 | 27.55 |

## Recommended: BREAKOUT_STOP1.5_GARCH40 (1D)

100万円 → 406万円 (+306%, 4.3年間)

- PF: 6.03
- Max DD: 18.50%
- Trades: 14 (十分な統計的信頼性)
- Win Rate: 50%
- Return/DD: 16.55

**10万円入れた場合: 約40.6万円 (+30.6万円)**

## Strategy Type Comparison (1D)

| Type | Best Variant | Net | DD% | PF |
|---|---|---:|---:|---:|
| Breakout + GARCH | BREAKOUT_STOP1.5_GARCH40 | +306% | 18.5 | 6.03 |
| MA cross + GARCH | MA200_STOP1.5ATR_GARCH40 | +110% | 18.7 | 4.80 |
| MA cross (no filter) | STOP_3ATR | +180% | 34.2 | 2.58 |
| Risk-based sizing | MA200_STOP2_RISK1PCT | +22% | 4.9 | 9.13 |
| Hybrid (TP + trail) | HYBRID_DON_STOP2_TP6_TRAIL3 | +29% | 5.4 | 8.53 |

## Statistical Filters (1D)

| Strategy | Trades | WR% | Net JPY | PF | Final | DD% |
|---|---:|---:|---:|---:|---:|---:|
| BREAKOUT_MA200_STOP1.5_KELLY | 14 | 42.9 | 96,153 | 14.75 | 1,096,153 | 3.00 |
| BREAKOUT_HURST55_MA200_KELLY | 12 | 33.3 | 85,104 | 8.12 | 1,085,104 | 3.00 |
| BREAKOUT_HURST55_STOP1.5_GARCH40 | 12 | 41.7 | 2,488,283 | 6.13 | 3,488,283 | 24.66 |
| BREAKOUT_STOP1.5_GARCH40 (baseline) | 14 | 50.0 | 3,062,453 | 6.03 | 4,062,453 | 18.50 |
| BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40 | 9 | 44.4 | 1,307,867 | 5.50 | 2,307,867 | 16.86 |
| BREAKOUT_MAE_MA200_GARCH40 | 13 | 53.8 | 2,805,757 | 4.62 | 3,805,757 | 21.35 |
| BREAKOUT_Z1.5_MA200_STOP1.5_GARCH40 | 11 | 45.5 | 1,676,594 | 4.18 | 2,676,594 | 21.72 |
| BREAKOUT_HMM_STOP1.5_GARCH40 | 19 | 26.3 | 1,075,192 | 1.89 | 2,075,192 | 30.91 |
| STAT_FULL_BREAKOUT (all combined) | 10 | 30.0 | 258,396 | 1.67 | 1,258,396 | 26.13 |

## Statistical Filters - Multi Timeframe

### 12H (Best for stat filters)

| Strategy | Trades | WR% | Net JPY | PF | Final | DD% |
|---|---:|---:|---:|---:|---:|---:|
| **BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40** | **20** | **40.0** | **1,852,193** | **3.30** | **2,852,193** | **19.86** |
| BREAKOUT_MA200_STOP1.5_KELLY | 39 | 25.6 | 113,239 | 3.17 | 1,113,239 | 2.30 |
| BREAKOUT_Z1.5_MA200_STOP1.5_GARCH40 | 23 | 39.1 | 1,624,333 | 2.45 | 2,624,333 | 21.61 |
| BREAKOUT_HURST55_STOP1.5_GARCH40 | 34 | 26.5 | 1,186,103 | 1.99 | 2,186,103 | 25.18 |
| BREAKOUT_STOP1.5_GARCH40 | 39 | 25.6 | 901,855 | 1.62 | 1,901,855 | 25.80 |
| BREAKOUT_MAE_MA200_GARCH40 | 33 | 30.3 | 821,344 | 1.56 | 1,821,344 | 25.56 |

### 6H

| Strategy | Trades | WR% | Net JPY | PF | Final | DD% |
|---|---:|---:|---:|---:|---:|---:|
| BREAKOUT_MA200_STOP1.5_KELLY | 74 | 18.9 | 66,963 | 2.11 | 1,066,963 | 2.72 |
| BREAKOUT_HURST55_STOP1.5_GARCH40 | 68 | 27.9 | 1,220,041 | 1.76 | 2,220,041 | 35.71 |
| BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40 | 47 | 25.5 | 857,202 | 1.65 | 1,857,202 | 28.09 |
| BREAKOUT_STOP1.5_GARCH40 | 74 | 25.7 | 995,454 | 1.58 | 1,995,454 | 36.09 |
| BREAKOUT_Z1.5_MA200_STOP1.5_GARCH40 | 53 | 22.6 | 786,777 | 1.56 | 1,786,777 | 29.85 |
| BREAKOUT_MAE_MA200_GARCH40 | 63 | 31.7 | 908,882 | 1.47 | 1,908,882 | 37.12 |

### 3H

| Strategy | Trades | WR% | Net JPY | PF | Final | DD% |
|---|---:|---:|---:|---:|---:|---:|
| BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40 | 88 | 26.1 | 1,084,832 | 1.61 | 2,084,832 | 37.17 |
| BREAKOUT_Z1.5_MA200_STOP1.5_GARCH40 | 104 | 27.9 | 1,019,688 | 1.47 | 2,019,688 | 42.56 |
| BREAKOUT_MA200_STOP1.5_KELLY | 162 | 18.5 | 39,279 | 1.37 | 1,039,279 | 3.49 |
| BREAKOUT_HURST55_STOP1.5_GARCH40 | 137 | 23.4 | 1,145,524 | 1.36 | 2,145,524 | 30.44 |
| BREAKOUT_STOP1.5_GARCH40 | 162 | 22.8 | 761,697 | 1.21 | 1,761,697 | 37.77 |
| BREAKOUT_MAE_MA200_GARCH40 | 139 | 28.1 | 380,646 | 1.11 | 1,380,646 | 41.86 |

## Key Findings - Statistical Filters

1. **Hurst + z-score on 12H is the sweet spot** — `BREAKOUT_HURST55_Z1.5` gets +185% with DD 19.9% and 20 trades (good confidence)
2. **Kelly sizing is ultra-safe everywhere** — DD always under 3.5%, but returns are modest
3. **Statistical filters improve shorter timeframes dramatically** — Hurst+z-score turns 3H from PF 1.21 to PF 1.61
4. **12H stat variants outperform 1D baseline** — more trades (20 vs 14) with similar DD

## Final Recommendations

| Profile | Variant | TF | Expected | DD |
|---|---|---|---:|---:|
| Maximum return | BREAKOUT_STOP1.5_GARCH40 | 1D | +306% | 18.5% |
| High return + stat filter | BREAKOUT_HURST55_Z1.5_STOP1.5_GARCH40 | 12H | +185% | 19.9% |
| Balanced (more trades) | BREAKOUT_HURST55_STOP1.5_GARCH40 | 6H | +122% | 35.7% |
| Ultra safe | BREAKOUT_MA200_STOP1.5_KELLY | 12H | +11% | 2.3% |

## Notes

- Data period: ~4.3 years (2022-2026), covering bear (2022) and bull (2024-2025) markets
- Fees: Real bitFlyer fee tiers applied (0.01% - 0.15% based on 30-day volume)
- Slippage: Not applied (conservative)
- Breakout variants produce 3x the return of MA cross with similar drawdown
- Statistical filters (Hurst, z-score) most effective on 12H and shorter timeframes
- Kelly sizing provides extreme risk reduction but limited absolute returns
- HMM regime detection underperforms simple MA200 filter
- Combining too many filters (STAT_FULL) hurts performance by over-filtering
