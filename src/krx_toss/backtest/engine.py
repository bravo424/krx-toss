from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping

from krx_toss.cost.model import CostModel
from krx_toss.strategy.features import Candle, CreditDay, FlowDay, period_return
from krx_toss.strategy.risk import RiskLimits, size_buy
from krx_toss.strategy.signals import evaluate_symbol
from krx_toss.toss.decimal_utils import apply_tick_offset, to_decimal


@dataclass
class SymbolHistory:
    market: str
    candles: list[Candle]
    flow: list[FlowDay]
    credit: list[CreditDay]
    shares_outstanding: Decimal | None = None


@dataclass
class Trade:
    symbol: str
    market: str
    entry_date: str
    exit_date: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    pnl: Decimal
    reason: str


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[str, Decimal]] = field(default_factory=list)
    start_nav: Decimal = Decimal("0")
    end_nav: Decimal = Decimal("0")

    @property
    def total_return(self) -> Decimal:
        if self.start_nav <= 0:
            return Decimal("0")
        return (self.end_nav / self.start_nav) - 1

    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return Decimal(wins) / Decimal(len(self.trades))


def _by_date_index(candles: list[Candle]) -> dict[str, int]:
    return {_bar_date(c): i for i, c in enumerate(candles)}


def _bar_date(candle: Candle) -> str:
    return str(candle.timestamp)[:10]


def _flow_until(flow: list[FlowDay], day: str) -> list[FlowDay]:
    return [f for f in flow if f.date <= day]


def _credit_until(credit: list[CreditDay], day: str) -> list[CreditDay]:
    return [c for c in credit if c.date <= day]


def run_backtest(
    histories: Mapping[str, SymbolHistory],
    *,
    kospi: list[Candle],
    cost: CostModel,
    limits: RiskLimits,
    signal_params: Mapping[str, object],
    start_nav: Decimal,
    slippage_ticks: int = 1,
    take_profit: Decimal = Decimal("0.06"),
    stop_loss: Decimal = Decimal("0.04"),
    time_stop: int = 5,
    max_positions: int | None = None,
) -> BacktestResult:
    max_pos = max_positions or limits.max_positions
    dates = sorted({_bar_date(c) for hist in histories.values() for c in hist.candles})
    if kospi:
        kospi_dates = {_bar_date(c) for c in kospi}
        dates = [d for d in dates if d in kospi_dates or not kospi_dates]
    nav = start_nav
    cash = start_nav
    open_pos: dict[str, dict] = {}
    trades: list[Trade] = []
    curve: list[tuple[str, Decimal]] = []
    kospi_idx = _by_date_index(kospi)

    for i, day in enumerate(dates[:-1]):
        next_day = dates[i + 1]
        # mark to market
        mtm = cash
        for sym, pos in open_pos.items():
            hist = histories[sym]
            idx = _by_date_index(hist.candles)
            if day in idx:
                mtm += hist.candles[idx[day]].close * pos["qty"]
        curve.append((day, mtm))
        nav = mtm

        # exits on next open using today's close signal state
        to_close: list[str] = []
        for sym, pos in open_pos.items():
            hist = histories[sym]
            idx = _by_date_index(hist.candles)
            if next_day not in idx:
                continue
            nxt = hist.candles[idx[next_day]]
            sessions = pos["sessions"] + 1
            pos["sessions"] = sessions
            exit_px = None
            reason = ""
            stop_px = pos["stop"]
            tp_px = pos["tp"]
            if nxt.low <= stop_px:
                exit_px = apply_tick_offset(stop_px, -slippage_ticks, hist.market, side="SELL")
                reason = "stop"
            elif nxt.high >= tp_px:
                exit_px = apply_tick_offset(tp_px, -slippage_ticks, hist.market, side="SELL")
                reason = "take_profit"
            elif sessions >= time_stop:
                exit_px = apply_tick_offset(nxt.open, -slippage_ticks, hist.market, side="SELL")
                reason = "time_stop"
            else:
                flow = _flow_until(hist.flow, day)
                if flow and flow[-1].foreign_net < 0 and flow[-1].institution_net < 0:
                    exit_px = apply_tick_offset(nxt.open, -slippage_ticks, hist.market, side="SELL")
                    reason = "flow_reversal"
            if exit_px is not None:
                buy_notional = pos["entry"] * pos["qty"]
                sell_notional = exit_px * pos["qty"]
                pnl = cost.net_pnl(buy_notional, sell_notional, hist.market)
                cash += sell_notional - cost.sell_cost(sell_notional, hist.market)
                trades.append(
                    Trade(
                        symbol=sym,
                        market=hist.market,
                        entry_date=pos["entry_date"],
                        exit_date=next_day,
                        entry_price=pos["entry"],
                        exit_price=exit_px,
                        quantity=pos["qty"],
                        pnl=pnl,
                        reason=reason,
                    )
                )
                to_close.append(sym)
        for sym in to_close:
            open_pos.pop(sym, None)

        # entries: signal on day close, fill next open (T+1, 09:15 proxy)
        skip = to_decimal(signal_params.get("kospi_skip_1d_return", "-0.02"))
        if kospi and day in kospi_idx:
            kospi_slice = kospi[: kospi_idx[day] + 1]
            r1 = period_return([c.close for c in kospi_slice], 1)
            if r1 is not None and r1 < skip:
                continue
        if len(open_pos) >= max_pos:
            continue

        ranked: list[tuple[Decimal, str]] = []
        for sym, hist in histories.items():
            if sym in open_pos:
                continue
            idx = _by_date_index(hist.candles)
            if day not in idx:
                continue
            end = idx[day] + 1
            signal, _reason = evaluate_symbol(
                symbol=sym,
                market=hist.market,
                candles=hist.candles[:end],
                flow=_flow_until(hist.flow, day),
                credit=_credit_until(hist.credit, day),
                params=signal_params,
            )
            if signal:
                ranked.append((signal.foreign_net + signal.institution_net, sym))
        ranked.sort(reverse=True)

        for _score, sym in ranked:
            if len(open_pos) >= max_pos:
                break
            hist = histories[sym]
            idx = _by_date_index(hist.candles)
            if next_day not in idx:
                continue
            nxt = hist.candles[idx[next_day]]
            entry = apply_tick_offset(nxt.open, slippage_ticks, hist.market, side="BUY")
            intent = size_buy(
                nav=nav,
                price=entry,
                market=hist.market,
                open_positions=len(open_pos),
                shares_outstanding=hist.shares_outstanding,
                limits=limits,
            )
            if intent is None:
                continue
            notional = entry * intent.quantity
            buy_cost = cost.buy_cost(notional)
            if cash < notional + buy_cost:
                continue
            cash -= notional + buy_cost
            stop = apply_tick_offset(entry * (Decimal("1") - stop_loss), 0, hist.market, side="SELL")
            tp = apply_tick_offset(entry * (Decimal("1") + take_profit), 0, hist.market, side="SELL")
            open_pos[sym] = {
                "qty": intent.quantity,
                "entry": entry,
                "entry_date": next_day,
                "stop": stop,
                "tp": tp,
                "sessions": 0,
            }

    # liquidate remainder on last date close
    last = dates[-1] if dates else date.today().isoformat()
    for sym, pos in list(open_pos.items()):
        hist = histories[sym]
        idx = _by_date_index(hist.candles)
        if last not in idx:
            continue
        px = apply_tick_offset(hist.candles[idx[last]].close, -slippage_ticks, hist.market, side="SELL")
        buy_notional = pos["entry"] * pos["qty"]
        sell_notional = px * pos["qty"]
        pnl = cost.net_pnl(buy_notional, sell_notional, hist.market)
        cash += sell_notional - cost.sell_cost(sell_notional, hist.market)
        trades.append(
            Trade(sym, hist.market, pos["entry_date"], last, pos["entry"], px, pos["qty"], pnl, "eod_liquidate")
        )
        open_pos.pop(sym, None)
    curve.append((last, cash))
    return BacktestResult(trades=trades, equity_curve=curve, start_nav=start_nav, end_nav=cash)
