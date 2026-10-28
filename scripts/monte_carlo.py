#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
家庭资产配置——蒙特卡洛目标达成率测算。

用途：给定配置权重、初始金额、月度储蓄、年限与目标金额，估算达成概率与终值分布。

设计取舍：
  - 假设年度再平衡回到目标权重，故组合月度收益可直接用 (w'μ, sqrt(w'Σw)) 合成，
    无需逐资产模拟，速度快且结论一致。
  - 月度步长，正态月度收益。金额单位统一为「万元」。

示例：
  python3 monte_carlo.py --initial 130 --monthly 1.5 --years 20 --target 750 \
      --weights a:45,hk:25,ov:10,bond:15,gold:5 --paths 10000 --seed 42
"""

import argparse
import json
import math
import random
import sys

# 长期假设（名义年化，人民币计价）。与 references/asset_classes.md 保持一致，改动须同步。
ASSUMPTIONS = {
    "a":    {"name": "A股宽基",   "mu": 0.070, "sigma": 0.22},
    "hk":   {"name": "港股",      "mu": 0.075, "sigma": 0.25},
    "ov":   {"name": "海外权益",  "mu": 0.060, "sigma": 0.16},
    "jp":   {"name": "日本股市",  "mu": 0.055, "sigma": 0.20},
    "bond": {"name": "中国债券",  "mu": 0.025, "sigma": 0.025},
    "gold": {"name": "黄金",      "mu": 0.040, "sigma": 0.16},
    "cash": {"name": "现金/货基", "mu": 0.013, "sigma": 0.001},
}

CORR = {
    ("a", "a"): 1.00, ("a", "hk"): 0.65, ("a", "ov"): 0.25,
    ("a", "bond"): -0.10, ("a", "gold"): 0.10, ("a", "cash"): 0.00,
    ("hk", "hk"): 1.00, ("hk", "ov"): 0.40, ("hk", "bond"): -0.05,
    ("hk", "gold"): 0.12, ("hk", "cash"): 0.00,
    ("ov", "ov"): 1.00, ("ov", "bond"): 0.05, ("ov", "gold"): 0.10,
    ("ov", "cash"): 0.00, ("ov", "jp"): 0.55,
    ("jp", "jp"): 1.00, ("jp", "bond"): 0.05, ("jp", "gold"): 0.15,
    ("jp", "cash"): 0.00, ("jp", "a"): 0.45, ("jp", "hk"): 0.50,
    ("bond", "bond"): 1.00, ("bond", "gold"): 0.20, ("bond", "cash"): 0.10,
    ("gold", "gold"): 1.00, ("gold", "cash"): 0.00,
    ("cash", "cash"): 1.00,
}


def corr(x, y):
    return CORR.get((x, y), CORR.get((y, x), 0.0))


# 外币计价资产：港股（HKD）、海外（USD）、日本（JPY）。
# 黄金虽以美元定价，但境内以人民币计价交易，购买力随人民币波动有限，故计入本币。
FOREIGN = {"hk", "ov", "jp"}


def fx_breakdown(weights):
    """返回 (外币敞口, 本币资产, 人民币升值 5% 时的组合冲击 pp)。"""
    fx = sum(w for k, w in weights.items() if k in FOREIGN)
    return fx, 1.0 - fx, -fx * 0.05


def parse_weights(raw):
    """'a:45,hk:25' -> {'a': 0.45, 'hk': 0.25}"""
    weights = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"权重格式错误: '{part}'，应为 资产代码:百分比，例如 a:45")
        key, val = part.split(":", 1)
        key = key.strip().lower()
        if key not in ASSUMPTIONS:
            raise SystemExit(f"未知资产代码 '{key}'，可选: {', '.join(ASSUMPTIONS)}")
        try:
            weights[key] = float(val) / 100.0
        except ValueError:
            raise SystemExit(f"权重不是数字: '{val}'")
    total = sum(weights.values())
    if not (0.99 <= total <= 1.01):
        raise SystemExit(f"权重合计 {total*100:.1f}%，必须为 100%")
    # 归一到 1，消除四舍五入误差
    return {k: v / total for k, v in weights.items()}


def portfolio_stats(weights):
    mu = sum(w * ASSUMPTIONS[k]["mu"] for k, w in weights.items())
    var = 0.0
    for i, wi in weights.items():
        for j, wj in weights.items():
            var += wi * wj * corr(i, j) * ASSUMPTIONS[i]["sigma"] * ASSUMPTIONS[j]["sigma"]
    sigma = math.sqrt(max(var, 0.0))
    return mu, sigma


def simulate(initial, monthly, years, weights, paths, seed):
    mu, sigma = portfolio_stats(weights)
    m_m = mu / 12.0
    s_m = sigma / math.sqrt(12.0)
    months = int(round(years * 12))
    rng = random.Random(seed)

    finals = []
    for _ in range(paths):
        v = initial
        for _ in range(months):
            r = rng.gauss(m_m, s_m)
            v = max(v * (1.0 + r), 0.0) + monthly
        finals.append(v)
    finals.sort()
    return finals, mu, sigma


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def main():
    ap = argparse.ArgumentParser(description="家庭资产配置蒙特卡洛达成率测算（金额单位：万元）")
    ap.add_argument("--initial", type=float, required=True, help="初始金额（万元）")
    ap.add_argument("--monthly", type=float, default=0.0, help="每月新增投入（万元），默认 0")
    ap.add_argument("--years", type=float, required=True, help="投资年限")
    ap.add_argument("--target", type=float, required=True, help="目标金额（万元）")
    ap.add_argument("--weights", type=str, required=True,
                    help="配置权重，如 a:45,hk:25,ov:10,bond:15,gold:5（合计 100）")
    ap.add_argument("--paths", type=int, default=10000, help="模拟路径数，默认 10000")
    ap.add_argument("--seed", type=int, default=42, help="随机种子，默认 42（保证可复现）")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    weights = parse_weights(args.weights)
    finals, mu, sigma = simulate(args.initial, args.monthly, args.years,
                                 weights, args.paths, args.seed)

    hit = sum(1 for v in finals if v >= args.target) / len(finals)
    total_in = args.initial + args.monthly * args.years * 12
    fx, local, fx_impact = fx_breakdown(weights)
    result = {
        "assumptions_note": "收益率与波动率为长期假设值，非预测",
        "portfolio": {
            "exp_return": round(mu * 100, 2),
            "volatility": round(sigma * 100, 2),
            "sharpe_like": round((mu - 0.013) / sigma, 2) if sigma else None,
        },
        "currency": {
            "foreign_exposure_pct": round(fx * 100, 1),
            "local_assets_pct": round(local * 100, 1),
            "rmb_up_5pct_impact_pp": round(fx_impact * 100, 2),
            "note": "港股/美股/日经计为外币；黄金按人民币计价计入本币",
            "limit_check": "超限（>45%）" if fx > 0.45 else ("需报汇率敏感性（>40%）" if fx > 0.40 else "正常"),
        },
        "inputs": {
            "initial": args.initial, "monthly": args.monthly,
            "years": args.years, "target": args.target,
            "paths": args.paths, "seed": args.seed,
            "total_contributed": round(total_in, 1),
        },
        "success_rate": round(hit * 100, 1),
        "p5_veto": {
            "line": round(args.target * 0.85, 1),
            "p5": round(pct(finals, 5), 1),
            "pass": pct(finals, 5) >= args.target * 0.85,
            "note": "p5 低于目标 85% 时，加权益/降债的方案不成立（见 framework.md 4.6）",
        },
        "final_value_pct": {
            "p5": round(pct(finals, 5), 1),
            "p25": round(pct(finals, 25), 1),
            "p50": round(pct(finals, 50), 1),
            "p75": round(pct(finals, 75), 1),
            "p95": round(pct(finals, 95), 1),
        },
        "shortfall_p5": round(max(args.target - pct(finals, 5), 0), 1),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("=" * 62)
    print("家庭资产配置 · 目标达成率测算")
    print("=" * 62)
    print(f"配置权重      : " + " / ".join(
        f"{ASSUMPTIONS[k]['name']} {w*100:.0f}%" for k, w in weights.items()))
    print(f"组合预期收益  : {mu*100:.2f}%/年（假设值）")
    print(f"组合波动率    : {sigma*100:.2f}%/年")
    if fx > 0:
        warn = " ⚠ 超 40%，须在报告中单列汇率敏感性" if fx > 0.40 else ""
        print(f"外币敞口      : {fx*100:.0f}%（本币资产 {local*100:.0f}%）"
              f"  人民币升值 5% → 组合 {fx_impact*100:+.2f}pp{warn}")
    print(f"期初 {args.initial:.1f} 万 + 月投 {args.monthly:.2f} 万 × {args.years:g} 年"
          f" = 累计投入 {total_in:.1f} 万")
    print(f"目标金额      : {args.target:.1f} 万")
    print("-" * 62)
    print(f"达成概率      : {hit*100:.1f}%   ({args.paths} 条路径, seed={args.seed})")
    print(f"终值分位      : p5 {pct(finals,5):.0f} / p25 {pct(finals,25):.0f} / "
          f"p50 {pct(finals,50):.0f} / p75 {pct(finals,75):.0f} / p95 {pct(finals,95):.0f} 万")
    print(f"p5 缺口       : {max(args.target - pct(finals,5), 0):.1f} 万")
    p5_line = args.target * 0.85
    if pct(finals, 5) < p5_line:
        print(f"⚠ p5 否决      : p5 {pct(finals,5):.1f} 万 < 目标 85%（{p5_line:.1f} 万），"
              f"该方案以保底换中位数，不成立")
    print("-" * 62)
    if hit < 0.70:
        print("判定：达成率 <70%，方案不可交付。")
        print("处置顺序：① 延长年限 → ② 提高月储蓄 → ③ 下调目标。禁止调高预期收益率凑数。")
    elif pct(finals, 5) < p5_line:
        print("判定：达成率达标但 p5 破线，方案不成立——以保底换中位数，代价过大。")
        print("处置：降低权益或提高债券，把 p5 拉回目标 85% 以上，再谈收益。")
    elif hit < 0.85:
        print("判定：达成率 70%–85%，可交付但须明确压力情景（见 p5）。")
    else:
        print("判定：达成率 ≥85%，方案稳健。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
