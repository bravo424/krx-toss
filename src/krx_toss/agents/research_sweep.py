from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from krx_toss.agents.handoff import agents_root, read_json, write_json
from krx_toss.backtest.cache import MarketCache
from krx_toss.backtest.engine import BacktestResult, SymbolHistory, run_backtest
from krx_toss.config import Settings
from krx_toss.cost.model import CostModel
from krx_toss.strategy.risk import RiskLimits
from krx_toss.toss.decimal_utils import to_decimal

MIN_ALPHA_DELTA = Decimal("0.005")
DEFAULT_NAV = Decimal("100000000")


def load_histories(settings: Settings, cache: MarketCache) -> tuple[dict[str, SymbolHistory], list, dict[str, Any]]:
    signals = cache.read_json("signals.json") or {}
    universe = signals.get("universe") or []
    histories: dict[str, SymbolHistory] = {}
    skipped = 0
    for row in universe:
        symbol = row["symbol"]
        candles = cache.candles(symbol)
        if len(candles) < 30:
            skipped += 1
            continue
        histories[symbol] = SymbolHistory(
            market=row.get("market") or "KOSPI",
            candles=candles,
            flow=cache.flow(symbol),
            credit=cache.credit(symbol),
            shares_outstanding=to_decimal(row["shares_outstanding"]) if row.get("shares_outstanding") else None,
        )
    kospi = cache.candles("KOSPI")
    meta = {
        "universe_size": len(universe),
        "history_count": len(histories),
        "skipped_short_history": skipped,
        "kospi_bars": len(kospi),
    }
    return histories, kospi, meta


def _metrics(result: BacktestResult) -> dict[str, Any]:
    return {
        "trades": len(result.trades),
        "win_rate": str(result.win_rate),
        "total_return": str(result.total_return),
        "start_nav": str(result.start_nav),
        "end_nav": str(result.end_nav),
    }


def run_variant_backtest(
    settings: Settings,
    *,
    nav: Decimal,
    histories: Mapping[str, SymbolHistory],
    kospi: list,
    strategy: dict[str, Any],
) -> BacktestResult:
    cost = CostModel.from_strategy(strategy)
    limits = RiskLimits.from_strategy(strategy)
    exit_cfg = strategy.get("exit") or {}
    signal_cfg = strategy.get("signal") or {}
    cost_cfg = strategy.get("cost") or {}
    return run_backtest(
        histories,
        kospi=kospi,
        cost=cost,
        limits=limits,
        signal_params=signal_cfg,
        start_nav=nav,
        slippage_ticks=int(cost_cfg.get("slippage_ticks", 1)),
        take_profit=to_decimal(exit_cfg.get("take_profit", "0.08")),
        stop_loss=to_decimal(exit_cfg.get("stop_loss", "0.04")),
        lock_profit=to_decimal(exit_cfg.get("lock_profit", "0.06")),
        time_stop=int(exit_cfg.get("time_stop_sessions", 5)),
    )


def _grid_values(start: float, end: float, baseline: float) -> list[float]:
    mid = round((start + end) / 2, 4)
    values = sorted({start, mid, end, baseline})
    return values


def build_sweep_variants(base_strategy: dict[str, Any], brief: dict[str, Any]) -> list[dict[str, Any]]:
    signal = base_strategy.get("signal") or {}
    exit_cfg = base_strategy.get("exit") or {}
    variants: list[dict[str, Any]] = [{"label": "baseline", "patch": {}}]

    for sweep in brief.get("suggested_param_sweeps") or []:
        section = str(sweep.get("section") or "")
        key = str(sweep.get("key") or "")
        rng = sweep.get("range") or []
        if section not in {"signal", "exit"} or not key or len(rng) != 2:
            continue
        baseline_val = (base_strategy.get(section) or {}).get(key)
        try:
            lo, hi = float(rng[0]), float(rng[1])
            baseline_num = float(baseline_val) if baseline_val is not None else lo
        except (TypeError, ValueError):
            continue
        for val in _grid_values(lo, hi, baseline_num):
            label = f"{section}.{key}={val}"
            variants.append({"label": label, "patch": {section: {key: val}}})

    require_both = signal.get("require_both_flows")
    if require_both is not None:
        alt = not bool(require_both)
        variants.append(
            {
                "label": f"signal.require_both_flows={alt}",
                "patch": {"signal": {"require_both_flows": alt}},
            }
        )
    return variants


def apply_strategy_patch(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for section, keys in patch.items():
        if not isinstance(keys, dict):
            continue
        node = merged.setdefault(section, {})
        if isinstance(node, dict):
            node.update(keys)
    return merged


def signal_snapshot_analysis(signals_path: Path, strategy: dict[str, Any]) -> dict[str, Any]:
    import json

    if not signals_path.exists():
        return {"error": "signals.json missing"}
    payload = json.loads(signals_path.read_text(encoding="utf-8"))
    accepted = payload.get("accepted") or []
    rejected = payload.get("rejected") or {}
    max_3d = float((strategy.get("signal") or {}).get("max_3d_return", 0.20))
    thresholds = [0.13, 0.16, max_3d]
    momentum = [row for row in accepted if "dip_reversal" not in (row.get("reasons") or [])]
    dip = [row for row in accepted if "dip_reversal" in (row.get("reasons") or [])]
    overextended_accepted = []
    for row in momentum:
        try:
            if float(row.get("ret_3d") or 0) > max_3d:
                overextended_accepted.append(row["symbol"])
        except (TypeError, ValueError):
            continue
    tighten_counts = {}
    for thr in thresholds:
        tighten_counts[str(thr)] = sum(
            1
            for row in momentum
            if float(row.get("ret_3d") or 0) > thr
        )
    return {
        "accepted_total": len(accepted),
        "momentum_accepted": len(momentum),
        "dip_reversal_accepted": len(dip),
        "rejected_total": len(rejected),
        "overextended_3d_rejected": sum(1 for reason in rejected.values() if reason == "overextended_3d"),
        "momentum_above_max_3d_current": len(overextended_accepted),
        "momentum_would_drop_if_max_3d": tighten_counts,
    }


def pick_alpha_candidate(baseline: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    base_ret = Decimal(str(baseline.get("total_return") or "0"))
    best: dict[str, Any] | None = None
    best_ret = base_ret
    for row in variants:
        if row.get("label") == "baseline":
            continue
        ret = Decimal(str(row.get("total_return") or "0"))
        if ret - base_ret < MIN_ALPHA_DELTA:
            continue
        trades = int(row.get("trades") or 0)
        base_trades = int(baseline.get("trades") or 0)
        if trades == 0:
            continue
        if base_trades and trades < max(5, base_trades // 3):
            continue
        if ret > best_ret:
            best_ret = ret
            best = row
    return best


def run_research_sweep(
    settings: Settings,
    *,
    nav: Decimal = DEFAULT_NAV,
    brief_path: Path | None = None,
) -> dict[str, Any]:
    brief_path = brief_path or (agents_root(settings.root) / "pnl_brief.json")
    brief = read_json(brief_path) or {}
    cache = MarketCache(settings.cache_dir)
    histories, kospi, meta = load_histories(settings, cache)
    snapshot = signal_snapshot_analysis(settings.signals_path, settings.strategy)

    if not histories:
        return {
            "status": "blocked",
            "reason": "no_parquet_cache",
            "message": "Run `krx-toss fetch-cache` then re-run research-sweep for backtest validation.",
            "cache_meta": meta,
            "signal_snapshot": snapshot,
            "hypotheses": brief.get("research_hypotheses") or [],
        }

    base_strategy = settings.strategy
    variants_spec = build_sweep_variants(base_strategy, brief)
    runs: list[dict[str, Any]] = []
    for spec in variants_spec:
        strategy = apply_strategy_patch(base_strategy, spec["patch"])
        result = run_variant_backtest(settings, nav=nav, histories=histories, kospi=kospi, strategy=strategy)
        row = {"label": spec["label"], "patch": spec["patch"], **_metrics(result)}
        runs.append(row)

    baseline = runs[0]
    best = pick_alpha_candidate(baseline, runs)
    status = "candidate" if best else "rejected"
    return {
        "status": status,
        "cache_meta": meta,
        "signal_snapshot": snapshot,
        "baseline": baseline,
        "variants": runs[1:],
        "best_candidate": best,
        "hypotheses": brief.get("research_hypotheses") or [],
        "min_alpha_delta": str(MIN_ALPHA_DELTA),
    }


def write_research_result(settings: Settings, payload: dict[str, Any]) -> Path:
    path = agents_root(settings.root) / "research_result.json"
    write_json(path, payload)
    return path
