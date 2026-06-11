"""
股票筛选器 — 按技术面条件全市场扫描 A 股

筛选维度:
  - 涨跌幅 / 振幅 / 换手率
  - 成交量变化（量比）
  - 市盈率 / 总市值

数据来源: 东方财富全市场行情接口 (push2.eastmoney.com)
"""

from __future__ import annotations
import time
from typing import Any

from cn_stock.api import _fetch_json


def _fetch_all_a_stocks(page: int = 1, page_size: int = 100) -> list[dict[str, Any]]:
    """获取一页 A 股行情数据"""
    try:
        data = _fetch_json(
            "push2.eastmoney.com",
            "/api/qt/clist/get",
            {
                "pn": str(page),
                "pz": str(page_size),
                "po": "0",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深A股
                "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21",
            },
        )
        if data.get("rc") == 0 and data.get("data", {}).get("diff"):
            return data["data"]["diff"]
    except Exception:
        pass
    return []


def screen_stocks(
    min_gain: float | None = None,
    max_gain: float | None = None,
    min_volume_ratio: float | None = None,
    min_turnover: float | None = None,
    max_pe: float | None = None,
    min_market_cap: float | None = None,
    top_n: int = 50,
) -> dict[str, Any]:
    """
    按条件筛选 A 股

    Args:
        min_gain:         最低涨跌幅 (%)
        max_gain:         最高涨跌幅 (%)
        min_volume_ratio: 最低量比（当日成交量/5日均量）
        min_turnover:     最低换手率 (%)
        max_pe:           最高市盈率（动）
        min_market_cap:   最低总市值（亿元）
        top_n:            返回前 N 条

    Returns:
        {"matched": [...], "total_scanned": int, "conditions": {...}}
    """
    all_stocks = _fetch_all_a_stocks(page_size=100)
    if not all_stocks:
        # 多页获取
        all_stocks = []
        for p in range(1, 5):
            page = _fetch_all_a_stocks(page=p, page_size=100)
            if not page:
                break
            all_stocks.extend(page)
            time.sleep(0.5)

    matched: list[dict[str, Any]] = []
    conditions = {
        "min_gain": min_gain,
        "max_gain": max_gain,
        "min_volume_ratio": min_volume_ratio,
        "min_turnover": min_turnover,
        "max_pe": max_pe,
        "min_market_cap": min_market_cap,
    }

    def _f(val: Any) -> float | None:
        if val is None or val == "-":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    for item in all_stocks:
        code = item.get("f12", "")
        name = item.get("f14", "")
        if not code or not name:
            continue

        gain = _f(item.get("f3"))
        volume_ratio = _f(item.get("f10"))
        turnover = _f(item.get("f8"))
        pe = _f(item.get("f9"))
        market_cap = _f(item.get("f20"))

        # 过滤
        if min_gain is not None and (gain is None or gain < min_gain):
            continue
        if max_gain is not None and (gain is not None and gain > max_gain):
            continue
        if min_volume_ratio is not None and (volume_ratio is None or volume_ratio < min_volume_ratio):
            continue
        if min_turnover is not None and (turnover is None or turnover < min_turnover):
            continue
        if max_pe is not None and (pe is None or pe > max_pe):
            continue
        if min_market_cap is not None and (market_cap is None or market_cap < min_market_cap * 1e8):
            continue

        matched.append({
            "代码": code,
            "名称": name,
            "最新价": item.get("f2"),
            "涨跌幅(%)": gain,
            "量比": volume_ratio,
            "换手率(%)": turnover,
            "振幅(%)": _f(item.get("f7")),
            "市盈率(动)": pe,
            "总市值": market_cap,
            "今开": item.get("f17"),
            "最高": item.get("f15"),
            "最低": item.get("f16"),
            "昨收": item.get("f18"),
        })

    matched.sort(key=lambda x: x["涨跌幅(%)"] if x["涨跌幅(%)"] is not None else -9999, reverse=True)

    return {
        "matched": matched[:top_n],
        "count": len(matched),
        "total_scanned": len(all_stocks),
        "conditions": conditions,
    }