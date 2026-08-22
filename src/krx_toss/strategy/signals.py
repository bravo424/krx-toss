from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from krx_toss.strategy.features import (
    Candle,
    CreditDay,
    FlowDay,
    credit_vs_average,
    net_flow_sum,
    period_return,
    sma,
)


@dataclass
class Signal:
    symbol: str
    market: str
    close: Decimal
    ma20: Decimal
    ret_20d: Decimal
    ret_3d: Decimal
    foreign_net: Decimal
    institution_net: Decimal
    reasons: list[str] = field(default_factory=list)
    ret_1d: Decimal | None = None


@dataclass
class SignalDecision:
    accepted: list[Signal]
    rejected: dict[str, str]


def evaluate_symbol(
    *,
    symbol: str,
    market: str,
    candles: list[Candle],
    flow: list[FlowDay],
    credit: list[CreditDay],
    params: Mapping[str, Any],
) -> tuple[Signal | None, str | None]:
    ma_window = int(params.get("ma_window", 20))
    flow_n = int(params.get("flow_lookback_sessions", 3))
    min_20d = Decimal(str(params.get("min_20d_return", 0)))
    max_3d = Decimal(str(params.get("max_3d_return", "0.13")))
    max_credit = Decimal(str(params.get("max_credit_vs_avg", "1.5")))
    credit_lookback = int(params.get("credit_lookback", 20))

    closes = [c.close for c in candles]
    if len(closes) < ma_window + 1:
        return None, "insufficient_candles"
    ma = sma(closes, ma_window)
    r20 = period_return(closes, ma_window)
    r3 = period_return(closes, 3)
    if ma is None or r20 is None or r3 is None:
        return None, "insufficient_returns"
    if closes[-1] <= ma:
        return None, "below_ma"
    if r20 < min_20d:
        return None, "weak_20d_return"
    if r3 > max_3d:
        return None, "overextended_3d"

    fnet = net_flow_sum(flow, flow_n, "foreign_net")
    inet = net_flow_sum(flow, flow_n, "institution_net")
    if fnet is None or inet is None:
        return None, "missing_flow"
    require_both = bool(params.get("require_both_flows", True))
    if require_both:
        if fnet <= 0:
            return None, "foreign_not_buying"
        if inet <= 0:
            return None, "institution_not_buying"
    elif fnet <= 0 and inet <= 0:
        return None, "no_smart_flow"

    cred = credit_vs_average(credit, credit_lookback)
    if cred is not None and cred > max_credit:
        return None, "crowded_credit"

    return (
        Signal(
            symbol=symbol,
            market=market,
            close=closes[-1],
            ma20=ma,
            ret_20d=r20,
            ret_3d=r3,
            foreign_net=fnet,
            institution_net=inet,
            reasons=["foreign_buy", "institution_buy", "above_ma", "not_extended"],
            ret_1d=period_return(closes, 1),
        ),
        None,
    )


def evaluate_reversal_symbol(
    *,
    symbol: str,
    market: str,
    candles: list[Candle],
    flow: list[FlowDay],
    credit: list[CreditDay],
    params: Mapping[str, Any],
) -> tuple[Signal | None, str | None]:
    """Overnight bounce: name sold off with the market, buy next session."""
    min_drop = Decimal(str(params.get("reversal_min_1d", "-0.025")))
    max_drop = Decimal(str(params.get("reversal_max_1d", "-0.12")))
    max_credit = Decimal(str(params.get("max_credit_vs_avg", "1.5")))
    credit_lookback = int(params.get("credit_lookback", 20))
    closes = [c.close for c in candles]
    r1 = period_return(closes, 1)
    if r1 is None:
        return None, "insufficient_returns"
    if r1 > min_drop:
        return None, "dip_too_small"
    if r1 < max_drop:
        return None, "dip_too_deep"
    cred = credit_vs_average(credit, credit_lookback)
    if cred is not None and cred > max_credit:
        return None, "crowded_credit"
    ma_window = int(params.get("ma_window", 20))
    ma = sma(closes, ma_window) or closes[-1]
    r20 = period_return(closes, ma_window) or Decimal("0")
    r3 = period_return(closes, 3) or r1
    fnet = net_flow_sum(flow, int(params.get("flow_lookback_sessions", 3)), "foreign_net") or Decimal("0")
    inet = net_flow_sum(flow, int(params.get("flow_lookback_sessions", 3)), "institution_net") or Decimal("0")
    return (
        Signal(
            symbol=symbol,
            market=market,
            close=closes[-1],
            ma20=ma,
            ret_20d=r20,
            ret_3d=r3,
            foreign_net=fnet,
            institution_net=inet,
            reasons=["dip_reversal"],
            ret_1d=r1,
        ),
        None,
    )


def reversal_enabled(params: Mapping[str, Any], kospi_1d: Decimal | None) -> bool:
    if bool(params.get("reversal_always", False)):
        return True
    raw = params.get("reversal_kospi_1d", "-0.012")
    if raw in (None, "", False):
        return False
    return kospi_1d is not None and kospi_1d <= Decimal(str(raw))


def index_blocks_entries(kospi_candles: list[Candle], skip_return: Decimal) -> bool:
    r1 = period_return([c.close for c in kospi_candles], 1)
    if r1 is None:
        return False
    return r1 < skip_return


def select_signals(
    candidates: list[tuple[str, str, list[Candle], list[FlowDay], list[CreditDay]]],
    params: Mapping[str, Any],
    *,
    kospi_candles: list[Candle] | None = None,
) -> SignalDecision:
    skip = Decimal(str(params.get("kospi_skip_1d_return", "-0.02")))
    kospi_r1 = period_return([c.close for c in kospi_candles], 1) if kospi_candles else None
    if kospi_candles and kospi_r1 is not None and kospi_r1 < skip:
        return SignalDecision(accepted=[], rejected={sym: "kospi_risk_off" for sym, *_ in candidates})

    reversal_on = reversal_enabled(params, kospi_r1)

    reversal: list[Signal] = []
    momentum: list[Signal] = []
    rejected: dict[str, str] = {}
    for symbol, market, candles, flow, credit in candidates:
        if reversal_on:
            rev, _rreason = evaluate_reversal_symbol(
                symbol=symbol, market=market, candles=candles, flow=flow, credit=credit, params=params
            )
            if rev:
                reversal.append(rev)
                continue
        signal, reason = evaluate_symbol(
            symbol=symbol, market=market, candles=candles, flow=flow, credit=credit, params=params
        )
        if signal:
            momentum.append(signal)
        else:
            rejected[symbol] = reason or "rejected"
    reversal.sort(key=lambda s: s.ret_1d if s.ret_1d is not None else Decimal("0"))
    momentum.sort(key=lambda s: (s.foreign_net + s.institution_net), reverse=True)
    return SignalDecision(accepted=reversal + momentum, rejected=rejected)
