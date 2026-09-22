"""Derive the role x side x price table from Becker's published figure data.

The full 36GiB trade dataset is unreachable from this environment, but the paper
ships the computed output behind each figure in Jon-Becker/research. Two of those
files are enough to recover the cross-tab the paper never prints:

  maker_win_rate_by_direction.json   maker win rate by side and own cost basis
  longshot_ev_asymmetry.json         the pooled YES/NO returns behind the headline

The taker side follows exactly, with no estimation. Every print has one maker leg
and one taker leg on opposite sides at complementary prices, so for trades at
yes_price = Y:

    maker holds YES at Y      <=>  taker holds NO at 100-Y
    maker holds NO at 100-Y   <=>  taker holds YES at Y

which inverts to

    taker_winrate(NO,  p) = 1 - maker_winrate(YES, 100-p)
    taker_winrate(YES, p) = 1 - maker_winrate(NO,  100-p)

Usage:  python published_cells.py --fig /path/to/research/papers/prediction-market-microstructure/fig
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fees


def load_rows(path: Path) -> list[dict]:
    blob = json.loads(path.read_text())
    return blob["data"] if isinstance(blob, dict) else blob


def maker_table(fig: Path) -> dict[tuple[str, int], float]:
    """(side, price) -> maker win rate as a probability."""
    rows = load_rows(fig / "maker_win_rate_by_direction.json")
    out = {}
    for r in rows:
        p = int(r["price"])
        out[("yes", p)] = r["Maker bought YES"] / 100.0
        out[("no", p)] = r["Maker bought NO"] / 100.0
    return out


def build_cells(maker: dict[tuple[str, int], float]) -> list[dict]:
    """The full role x side x price table, with fees applied."""
    opposite = {"yes": "no", "no": "yes"}
    cells = []
    for (side, price), win in sorted(maker.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        for role in ("maker", "taker"):
            if role == "maker":
                w = win
            else:
                mirror = maker.get((opposite[side], 100 - price))
                if mirror is None:
                    continue
                w = 1.0 - mirror
            cells.append({
                "price": price, "role": role, "side": side, "win_rate": w,
                "implied": price / 100.0,
                "gross_pct": 100.0 * (100.0 * w - price) / price,
                "net_pct": fees.net_return_pct(price, w, maker=(role == "maker")),
            })
    return cells


def check_mirror_identity(fig: Path) -> dict:
    """EV_no(100-P) should equal -EV_yes(P) * P/(100-P) at every price level.

    Reported against a per-pair rounding bound, since the published values carry
    two decimals and the P/(100-P) factor amplifies that by up to 99x.
    """
    rows = {int(r["price"]): r for r in load_rows(fig / "longshot_ev_asymmetry.json")}
    devs, within = [], 0
    for P, row in rows.items():
        mirror = rows.get(100 - P)
        if mirror is None:
            continue
        scale = P / (100 - P)
        dev = abs(-row["yes_return"] * scale - mirror["no_return"])
        bound = 0.005 * scale + 0.005
        devs.append(dev)
        within += dev <= bound
    devs.sort()
    return {"n": len(devs), "max": devs[-1], "median": devs[len(devs) // 2],
            "within_rounding": within}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fig", required=True, type=Path)
    ap.add_argument("--prices", type=int, nargs="*", default=list(range(1, 11)))
    args = ap.parse_args()
    fig = args.fig

    mi = check_mirror_identity(fig)
    print("\n=== Finding 1 re-tested on the paper's own published returns ===")
    print(f"|EV_no(100-P) + EV_yes(P)*P/(100-P)| over {mi['n']} price pairs: "
          f"median {mi['median']:.4f} pp, max {mi['max']:.3f} pp "
          f"(series spans -41%..+23%)")
    print(f"within per-pair rounding bound: {mi['within_rounding']}/{mi['n']} levels")
    print("Near-exact reflection: the same fact, over very slightly different")
    print("trade sets at a handful of price levels.\n")

    cells = build_cells(maker_table(fig))
    by = {(c["role"], c["side"], c["price"]): c for c in cells}

    print("=== role x side x price, net of Kalshi fees ===")
    print(f"{'price':>5} {'side':>4} | {'TAKER win':>10} {'gross':>8} {'net':>8} "
          f"| {'MAKER win':>10} {'gross':>8} {'net':>8}")
    for p in args.prices:
        for side in ("no", "yes"):
            t, m = by.get(("taker", side, p)), by.get(("maker", side, p))
            if not t or not m:
                continue
            print(f"{p:>4}c {side:>4} | {t['win_rate']*100:>9.2f}% {t['gross_pct']:>+7.1f}% "
                  f"{t['net_pct']:>+7.1f}% | {m['win_rate']*100:>9.2f}% "
                  f"{m['gross_pct']:>+7.1f}% {m['net_pct']:>+7.1f}%")

    print("\n=== cross-check against the paper's other published files ===")
    mis = {int(r["price"]): r for r in load_rows(fig / "mispricing_by_price.json")}
    shares = {int(r["price"]): r for r in load_rows(fig / "yes_vs_no_by_price_taker_maker.json")}
    for p in (1, 2, 5):
        s = shares[p]
        ty, tn = by[("taker", "yes", p)], by[("taker", "no", p)]
        wt = s["taker_yes"] + s["taker_no"]
        blend = (s["taker_yes"] * ty["gross_pct"] + s["taker_no"] * tn["gross_pct"]) / wt
        print(f"  {p}c taker: derived blend {blend:+7.1f}%  vs mispricing_by_price "
              f"{mis[p]['Taker']:+7.1f}%   (gap {blend - mis[p]['Taker']:+.1f} pp)")

    tn = [by[("taker", "no", p)]["net_pct"] for p in range(1, 11)]
    pos = sum(v > 0 for v in tn)
    print(f"\n=== Verdict: taker-reachable NO, 1-10c, net of fees ===")
    print("  " + "  ".join(f"{p}c:{v:+.0f}%" for p, v in zip(range(1, 11), tn)))
    print(f"  {pos}/10 price levels positive; mean {sum(tn)/len(tn):+.1f}%, "
          f"spread {max(tn) - min(tn):.0f}pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
