#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
再平衡偏离度计算与调仓金额测算。

规则（见 references/rebalance_rules.md §1.1）：
  - 绝对阈值按目标权重大小分档：≥25% 用 ±5pp，10%–25% 用 ±3pp，<10% 用 ±2pp。
    统一 5pp 会让小仓位形同没有规则——A股目标 8% 时要偏离 62.5% 才触发。
  - 相对阈值：偏离超过目标值的 20%。小仓位通常先由这条触发。
  - 任一满足即调仓；优先用增量资金补低配，不足部分才卖出超配。
  - 金额单位：万元。

示例：
  python3 rebalance.py --target "a:8,hk:10,ov:22,jp:13,bond:38,gold:9" \
      --current "a:12,hk:10,ov:9,jp:8,bond:52,gold:9" --total 155 --cash-in 2
"""

import argparse
import sys

REL_THRESHOLD = 0.20       # 相对偏离阈值（占目标值的比例）


def abs_threshold(target_pct):
    """绝对偏离阈值（百分点），按目标权重大小分档。"""
    if target_pct >= 25:
        return 5.0
    if target_pct >= 10:
        return 3.0
    return 2.0


def parse_pairs(raw, label):
    out = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"{label} 格式错误: '{part}'")
        k, v = part.split(":", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError:
            raise SystemExit(f"{label} 中 '{v}' 不是数字")
    return out


def main():
    ap = argparse.ArgumentParser(description="再平衡偏离度测算（金额单位：万元）")
    ap.add_argument("--target", required=True, help='目标权重，如 "a:45,hk:25,ov:10,bond:15,gold:5"')
    ap.add_argument("--current", required=True, help='当前权重，格式同上')
    ap.add_argument("--total", type=float, required=True, help="当前组合总金额（万元）")
    ap.add_argument("--cash-in", type=float, default=0.0, help="可用增量资金（万元），默认 0")
    ap.add_argument("--fixed-abs", type=float, default=None,
                    help="用统一绝对阈值（百分点）覆盖分档规则，如 5；默认按权重重分档")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    target = parse_pairs(args.target, "--target")
    current = parse_pairs(args.current, "--current")

    keys = sorted(set(target) | set(current))
    for k in keys:
        target.setdefault(k, 0.0)
        current.setdefault(k, 0.0)

    t_sum = sum(target.values())
    c_sum = sum(current.values())
    if abs(t_sum - 100) > 1.0:
        raise SystemExit(f"目标权重合计 {t_sum:.1f}%，应约为 100%")
    if abs(c_sum - 100) > 1.0:
        raise SystemExit(f"当前权重合计 {c_sum:.1f}%，应约为 100%")

    total_after = args.total + args.cash_in
    rows, triggered = [], []
    for k in keys:
        dev_pp = current[k] - target[k]
        rel = abs(dev_pp) / target[k] if target[k] > 0 else float("inf")
        abs_thr = args.fixed_abs if args.fixed_abs is not None else abs_threshold(target[k])
        need_abs = abs(dev_pp) >= abs_thr - 1e-9
        need_rel = target[k] > 0 and rel >= REL_THRESHOLD - 1e-9
        is_trigger = need_abs or need_rel
        if is_trigger:
            triggered.append(k)
        cur_amt = args.total * current[k] / 100.0
        tgt_amt = total_after * target[k] / 100.0
        rows.append({
            "asset": k,
            "target_pct": round(target[k], 1),
            "current_pct": round(current[k], 1),
            "dev_pp": round(dev_pp, 1),
            "abs_threshold_pp": abs_thr,
            "rel_dev_pct": round(rel * 100, 1) if target[k] > 0 else None,
            "triggered": is_trigger,
            "by": "绝对" if need_abs and not need_rel else ("相对" if need_rel and not need_abs else ("绝对+相对" if is_trigger else "")),
            "current_amt": round(cur_amt, 1),
            "target_amt": round(tgt_amt, 1),
            "trade_amt": round(tgt_amt - cur_amt, 1),
        })

    buys = [r for r in rows if r["trade_amt"] > 0]
    sells = [r for r in rows if r["trade_amt"] < 0]
    buy_need = sum(r["trade_amt"] for r in buys)
    sell_need = -sum(r["trade_amt"] for r in sells)
    cash_covers = args.cash_in >= buy_need

    abs_rule = (f"统一绝对阈值 {args.fixed_abs:g}pp" if args.fixed_abs is not None
                else "绝对偏离分档（≥25%→5pp / 10–25%→3pp / <10%→2pp）")
    result = {
        "rule": f"{abs_rule} 或 相对偏离 >{REL_THRESHOLD*100:.0f}% 触发，任一满足即调仓",
        "total_before": args.total,
        "total_after": round(total_after, 1),
        "cash_in": args.cash_in,
        "triggered": triggered,
        "action_needed": bool(triggered),
        "rows": rows,
        "summary": {
            "buy_need": round(buy_need, 1),
            "sell_need": round(sell_need, 1),
            "net_trade": round(buy_need - sell_need, 1),
            "cash_covers_all": cash_covers,
            "cash_shortfall": round(max(buy_need - args.cash_in, 0), 1),
        },
    }

    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("=" * 74)
    print("再平衡偏离度测算")
    print("=" * 74)
    print(f"规则：{result['rule']}")
    print(f"组合总额 {args.total:.1f} 万" + (f" + 增量 {args.cash_in:.1f} 万 → {total_after:.1f} 万"
                                          if args.cash_in else ""))
    print("-" * 74)
    hdr = (f"{'资产':<6}{'目标%':>7}{'当前%':>7}{'偏离pp':>8}{'阈值':>6}"
           f"{'相对%':>8}{'当前额':>9}{'目标额':>9}{'调仓额':>9}")
    print(hdr)
    for r in rows:
        flag = f" ←触发({r['by']})" if r["triggered"] else ""
        rel = f"{r['rel_dev_pct']:.0f}" if r["rel_dev_pct"] is not None else "—"
        print(f"{r['asset']:<6}{r['target_pct']:>7.1f}{r['current_pct']:>7.1f}"
              f"{r['dev_pp']:>8.1f}{r['abs_threshold_pp']:>6.0f}{rel:>8}{r['current_amt']:>9.1f}"
              f"{r['target_amt']:>9.1f}{r['trade_amt']:>9.1f}{flag}")
    print("-" * 74)
    if not triggered:
        print("判定：未触发任何阈值，不动手。")
        return 0
    print(f"判定：触发 {len(triggered)} 项 → {', '.join(triggered)}")
    print(f"需买入 {buy_need:.1f} 万，需卖出 {sell_need:.1f} 万")
    if args.cash_in > 0:
        if cash_covers:
            print(f"增量资金 {args.cash_in:.1f} 万足以覆盖买入需求，零卖出，摩擦成本最低。")
        else:
            print(f"增量资金 {args.cash_in:.1f} 万不足，缺口 {buy_need-args.cash_in:.1f} 万，需卖出补足。")
    else:
        print("无增量资金，需卖出超配资产。优先卖持有满 1 年的份额以省赎回费。")
    print("执行顺序：增量资金补低配 → 分红/利息再投 → 卖出超配 → 同类内部调整。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
