"""
东方财富 + 腾讯财经 API 封装层

数据特点：
- 全部为国内可快速访问的公开 API，无需代理，无需 API Key
- 双数据源容错：优先东方财富，自动降级到腾讯财经
"""

import http.client
import json
import time
from typing import Any, Optional
from urllib.parse import urlencode


# ── 基础配置 ──────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Connection": "close",
}

# 大盘指数 secid 映射
INDEX_SECIDS = {
    "上证指数": "1.000001",
    "深证成指": "0.399001",
    "创业板指": "0.399006",
    "沪深300": "1.000300",
    "科创50": "1.000688",
}


def _parse_price(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return None


def _parse_volume(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_amount(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── 底层 HTTP 请求 ──────────────────────────────────────────────────

def _fetch_json(
    host: str,
    path: str,
    params: dict[str, str] | None = None,
    max_retries: int = 3,
    use_https: bool = True,
) -> dict:
    """
    使用 http.client 发起 GET 请求，每次新建连接

    Args:
        host: 主机名
        path: URL 路径
        params: 查询参数
        max_retries: 最大重试次数
        use_https: 是否使用 HTTPS

    Returns:
        JSON 字典
    """
    if params:
        safe_params = {k: str(v) for k, v in params.items()}
        query = urlencode(safe_params, safe="(),'")
        full_path = f"{path}?{query}"
    else:
        full_path = path

    last_error = None
    for attempt in range(max_retries):
        try:
            if use_https:
                conn = http.client.HTTPSConnection(host, timeout=15)
            else:
                conn = http.client.HTTPConnection(host, timeout=15)
            conn.request("GET", full_path, headers=HEADERS)
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            return json.loads(body.decode("utf-8"))
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"请求失败: {host}{full_path[:60]} — {last_error}")


# ── 1. 实时行情 (腾讯财经为主，东方财富为备) ──────────────────────

def get_realtime_quotations(
    secids: list[str],
) -> list[dict[str, Any]]:
    """
    批量获取实时行情
    - 主源: qt.gtimg.cn (腾讯财经)
    - 备源: push2.eastmoney.com
    """
    if not secids:
        return []

    # 尝试腾讯
    tencent_data = _get_tencent_realtime(secids)
    if tencent_data:
        return tencent_data

    # 尝试东方财富
    try:
        em_params = {
            "secids": ",".join(secids),
            "fields": "f2,f3,f4,f12,f14,f43,f44,f45,f46,f47,f48,f57,f58,f60,f62,f116,f117,f118,f167,f168,f169,f170,f171",
            "fltt": "2",
            "invt": "2",
        }
        data = _fetch_json("push2.eastmoney.com", "/api/qt/ulist.np/get", em_params)
        if data.get("rc") == 0 and data.get("data", {}).get("diff"):
            items = data["data"]["diff"]
            result = []
            for item in items:
                result.append(_format_em_quote(item) if item.get("f57") else _format_em_short(item))
            return result
    except RuntimeError:
        pass

    return []


def _format_em_quote(raw: dict) -> dict:
    return {
        "代码": raw.get("f57", ""),
        "名称": raw.get("f58", ""),
        "最新价": _parse_price(raw.get("f43")),
        "涨跌额": raw.get("f170"),
        "涨跌幅": raw.get("f171"),
        "今开": _parse_price(raw.get("f46")),
        "昨收": _parse_price(raw.get("f60")),
        "最高": _parse_price(raw.get("f44")),
        "最低": _parse_price(raw.get("f45")),
        "成交量(手)": _parse_volume(raw.get("f47")),
        "成交额(元)": _parse_amount(raw.get("f48")),
        "总市值": _parse_amount(raw.get("f116")),
        "流通市值": _parse_amount(raw.get("f117")),
        "市盈率(动)": raw.get("f118"),
        "市净率": raw.get("f167"),
        "股息率": raw.get("f168"),
        "总股本": raw.get("f169"),
    }


def _format_em_short(raw: dict) -> dict:
    return {
        "代码": raw.get("f12", ""),
        "名称": raw.get("f14", ""),
        "最新价": _parse_price(raw.get("f2")),
        "涨跌幅": raw.get("f3"),
        "涨跌额": raw.get("f4"),
        "最高": _parse_price(raw.get("f15")),
        "最低": _parse_price(raw.get("f16")),
        "今开": _parse_price(raw.get("f17")),
        "昨收": _parse_price(raw.get("f18")),
        "总市值": raw.get("f20"),
        "流通市值": raw.get("f21"),
    }


# ── 腾讯财经接口 ────────────────────────────────────────────────────

def _secid_to_tx_code(secid: str) -> str:
    """东方财富 secid → 腾讯代码格式"""
    market, code = secid.split(".")
    exchange = "sh" if market == "1" else "sz"
    return f"{exchange}{code}"


# ── Baostock 惰性导入 ─────────────────────────────────────────────
_bs_available: bool | None = None  # None=未检测, True/False


def _ensure_baostock() -> bool:
    """检测 baostock 是否可用，首次调用时 login"""
    global _bs_available
    if _bs_available is not None:
        return _bs_available
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code == "0":
            _bs_available = True
            return True
        _bs_available = False
        return False
    except ImportError:
        _bs_available = False
        return False


def _secid_to_bs_code(secid: str) -> str:
    """东方财富 secid '1.600519' → Baostock 'sh.600519'"""
    market, code = secid.split(".")
    exchange = "sh" if market == "1" else "sz"
    return f"{exchange}.{code}"


def _get_tencent_realtime(secids: list[str]) -> list[dict[str, Any]]:
    """
    通过腾讯财经 API 获取实时行情
    API: http://qt.gtimg.cn/q=sh600519,sz000001
    """
    import urllib.request

    codes = [_secid_to_tx_code(s) for s in secids]
    code_str = ",".join(codes)

    url = f"http://qt.gtimg.cn/q={code_str}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("gbk")
    except Exception:
        return []

    result = []
    for line in body.strip().split(";"):
        line = line.strip()
        if not line or not line.startswith("v_"):
            continue
        try:
            eq_idx = line.index("=")
            raw_value = line[eq_idx + 1:].strip().strip('"')
            parts = raw_value.split("~")
            if len(parts) < 40:
                continue

            name = parts[1]
            code = parts[2]
            price = _parse_price(parts[3])
            prev_close = _parse_price(parts[4])
            open_price = _parse_price(parts[5])
            volume = _parse_volume(parts[6])
            high = _parse_price(parts[33])
            low = _parse_price(parts[34])
            amount_str = parts[37]
            change_pct = parts[32]

            result.append({
                "代码": code,
                "名称": name,
                "最新价": price,
                "涨跌幅": float(change_pct) if change_pct else None,
                "涨跌额": round(price - prev_close, 2) if price and prev_close else None,
                "今开": open_price,
                "昨收": prev_close,
                "最高": high,
                "最低": low,
                "成交量(手)": volume,
                "成交额(元)": float(amount_str) if amount_str else None,
            })
        except (ValueError, IndexError):
            continue

    return result


# ── 2. K 线数据 ───────────────────────────────────────────────────────

def get_kline(
    secid: str,
    klt: str = "101",
    fqt: str = "1",
    lmt: int = 120,
    beg: str = "",
    end: str = "",
) -> list[dict[str, Any]]:
    """
    获取 K 线数据
    主源: Baostock (最稳定历史数据，量化标配)
    次源: push2.eastmoney.com
    备源: web.ifzq.gtimg.cn (腾讯 K 线)
    """
    # ── 主源: Baostock ──
    bs_result = _get_baostock_kline(secid, klt, fqt, lmt, beg, end)
    if bs_result:
        return bs_result

    # ── 次源: 东方财富 ──
    try:
        params = {
            "secid": secid,
            "klt": str(klt),
            "fqt": str(fqt),
            "lmt": str(lmt),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cffc9f32ec1c0c",
        }
        if beg:
            params["beg"] = beg
        if end:
            params["end"] = end
        data = _fetch_json("push2.eastmoney.com", "/api/qt/stock/kline/get", params)
        if data.get("rc") == 0 and data.get("data", {}).get("klines"):
            return _parse_em_kline(data["data"]["klines"])
    except RuntimeError:
        pass

    # ── 备源: 腾讯 ──
    try:
        return _get_tencent_kline(secid, klt, lmt)
    except Exception:
        pass

    return []


def _parse_em_kline(klines: list[str]) -> list[dict[str, Any]]:
    result = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 11:
            result.append({
                "日期": parts[0],
                "开盘价": _parse_price(parts[1]),
                "收盘价": _parse_price(parts[2]),
                "最高价": _parse_price(parts[3]),
                "最低价": _parse_price(parts[4]),
                "成交量(手)": _parse_volume(parts[5]),
                "成交额(元)": _parse_amount(parts[6]),
                "振幅": f"{parts[7]}%",
                "涨跌幅": f"{parts[8]}%",
                "涨跌额": _parse_price(parts[9]),
                "换手率": f"{parts[10]}%" if parts[10] else "0%",
            })
    return result


def _get_tencent_kline(secid: str, klt: str, lmt: int) -> list[dict[str, Any]]:
    """腾讯日 K 线"""
    import urllib.request
    market, code = secid.split(".")
    exchange = "sh" if market == "1" else "sz"

    url = (
        f"http://web.ifzq.gtimg.cn/appstock/app/kline/mkline?"
        f"param={exchange}{code},m,,{lmt}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    stock_data = data.get("data", {}).get(f"{exchange}{code}", {})
    klines_raw = stock_data.get("m", {}).get("qfq", [])

    result = []
    for k in klines_raw:
        if len(k) >= 6:
            try:
                result.append({
                    "日期": k[0],
                    "开盘价": float(k[1]),
                    "收盘价": float(k[2]),
                    "最高价": float(k[3]),
                    "最低价": float(k[4]),
                    "成交量(手)": int(float(k[5])),
                })
            except (ValueError, IndexError):
                continue
    return result


# ═══════════════════════════════════════════════════════════════
# Baostock K 线实现
# ═══════════════════════════════════════════════════════════════

def _get_baostock_kline(
    secid: str,
    klt: str = "101",
    fqt: str = "1",
    lmt: int = 120,
    beg: str = "",
    end: str = "",
) -> list[dict[str, Any]]:
    """
    通过 Baostock 获取 K 线数据

    klt 映射:
      101 → d (日K), 102 → w (周K), 103 → m (月K)
      60  → 60 (60分钟), 30 → 30, 15 → 15, 5 → 5

    fqt 映射 (Baostock: 1=后复权, 2=前复权, 3=不复权):
      1 (前复权) → 2
      2 (后复权) → 1
      0 (不复权) → 3

    返回格式: 与 _parse_em_kline 一致
    """
    if not _ensure_baostock():
        return []

    import baostock as bs

    bs_code = _secid_to_bs_code(secid)

    # 频率映射
    freq_map = {
        "101": "d", "102": "w", "103": "m",
        "60": "60", "30": "30", "15": "15", "5": "5",
    }
    frequency = freq_map.get(klt, "d")

    # 复权映射
    adjust_map = {"1": "2", "2": "1", "0": "3"}
    adjustflag = adjust_map.get(fqt, "2")

    # 日期范围
    from datetime import datetime, timedelta
    if not end:
        end_dt = datetime.now()
    else:
        end_dt = datetime.strptime(end[:8], "%Y%m%d")
    if not beg:
        if frequency == "d":
            beg_dt = end_dt - timedelta(days=lmt * 2)
        elif frequency == "w":
            beg_dt = end_dt - timedelta(weeks=lmt * 2)
        elif frequency == "m":
            beg_dt = end_dt - timedelta(days=lmt * 45)
        else:
            beg_dt = end_dt - timedelta(days=lmt * 2)
    else:
        beg_dt = datetime.strptime(beg[:8], "%Y%m%d")

    start_date = beg_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    fields = "date,open,high,low,close,volume,amount"
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            fields,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag=adjustflag,
        )
        if rs.error_code != "0":
            return []

        rows: list[dict[str, Any]] = []
        while rs.next():
            row = rs.get_row_data()
            if not row or row[0] == "":
                continue
            # row: [date, open, high, low, close, volume, amount]
            try:
                rows.append({
                    "日期": row[0],
                    "开盘价": _parse_price(row[1]),
                    "收盘价": _parse_price(row[4]),
                    "最高价": _parse_price(row[2]),
                    "最低价": _parse_price(row[3]),
                    "成交量(手)": _parse_volume(row[5]),
                    "成交额(元)": _parse_amount(row[6]),
                })
            except (ValueError, IndexError):
                continue

        # 截取最后 lmt 条
        return rows[-lmt:] if len(rows) > lmt else rows

    except Exception:
        return []


# ── 3. 财务数据 ─────────────────────────────────────────────────────

def get_financials(
    stock_code: str,
    page_size: int = 5,
) -> list[dict[str, Any]]:
    """
    获取核心财务指标 (东方财富 datacenter)

    Args:
        stock_code: 6 位股票代码
        page_size:  最近几期

    Returns:
        财务数据列表
    """
    try:
        data = _fetch_json(
            "datacenter.eastmoney.com",
            "/api/data/v1/get",
            {
                "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{stock_code}")',
                "pageNumber": "1",
                "pageSize": str(page_size),
                "sortTypes": "-1",
                "sortColumns": "REPORT_DATE",
                "source": "HSF10",
                "client": "PC",
            },
        )
    except RuntimeError:
        return []
    if not data.get("success") or not data.get("result", {}).get("data"):
        return []
    rows = data["result"]["data"]

    result = []
    for row in rows:
        result.append({
            "报告期": row.get("REPORT_DATE", ""),
            "营业总收入(元)": row.get("TOTALOPERATEREVE", ""),
            "净利润(元)": row.get("NETPROFITSHSR", ""),
            "基本每股收益": row.get("EPSJB", ""),
            "加权ROE": row.get("JQROE", ""),
            "每股净资产": row.get("BPS", ""),
            "每股经营现金流": row.get("MGJYXJJE", ""),
            "销售毛利率": row.get("XSMLL", ""),
            "扣非净利润(元)": row.get("KCFJCXSYJLR", ""),
            "资产负债率": row.get("ZCFZL", ""),
        })
    return result


# ── 4. 大盘指数 ──────────────────────────────────────────────────────

def get_market_indices() -> list[dict[str, Any]]:
    """获取所有大盘指数实时行情"""
    secids = list(INDEX_SECIDS.values())
    quotes = get_realtime_quotations(secids)
    for q in quotes:
        code = q["代码"]
        for name, secid in INDEX_SECIDS.items():
            idx_code = secid.split(".")[1]
            if code == idx_code:
                q["名称"] = name
                break
    return quotes


# ── 5. 板块排行 ─────────────────────────────────────────────────────

def get_sector_ranking(
    sector_type: str = "industry",
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    获取板块涨幅排行

    策略: 先尝试东方财富 push2 接口。
    若不可用，则通过腾讯行情聚合计算各板块成分股的平均涨跌幅。
    """
    # 先尝试东方财富 API
    sectors_em = _get_sectors_from_eastmoney(sector_type, top_n)
    if sectors_em:
        return sectors_em

    # 备选: 通过成分股聚合计算
    return _get_sectors_from_constituents(sector_type, top_n)


def _get_sectors_from_eastmoney(sector_type: str, top_n: int) -> list[dict[str, Any]]:
    """从东方财富获取板块排行"""
    fs_map = {
        "industry": "m:90+t:2",
        "concept": "m:90+t:3",
        "region": "m:90+t:1",
    }
    fs = fs_map.get(sector_type)
    if not fs:
        return []

    for attempt in range(2):
        try:
            data = _fetch_json(
                "push2.eastmoney.com",
                "/api/qt/clist/get",
                {
                    "pn": "1",
                    "pz": str(top_n),
                    "po": "0",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": fs,
                    "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f8,f20,f21,f62",
                },
            )
            if data.get("rc") == 0 and data.get("data", {}).get("diff"):
                items = data["data"]["diff"]
                result = []
                for item in items:
                    result.append({
                        "板块代码": item.get("f12", ""),
                        "板块名称": item.get("f14", ""),
                        "最新点位": _parse_price(item.get("f2")),
                        "涨跌幅": item.get("f3"),
                        "涨跌额": item.get("f4"),
                        "总市值": item.get("f20"),
                        "主力净流入": item.get("f62"),
                    })
                return result
            if attempt == 0:
                time.sleep(0.5)
        except RuntimeError:
            if attempt == 0:
                time.sleep(1.0)
            continue
    return []


def _get_sectors_from_constituents(sector_type: str, top_n: int) -> list[dict[str, Any]]:
    """
    通过成分股聚合计算板块涨跌幅
    从 data.py 中的 SECTORS 映射获取各板块的代表股票，查询实时行情后聚合
    """
    from cn_stock.data import SECTORS

    if sector_type != "industry":
        return []

    sector_results = []
    for sector_name, codes in SECTORS.items():
        # 构造 secid 列表
        secids = [guess_secid(c) for c in codes]
        quotes = get_realtime_quotations(secids)
        if not quotes:
            continue

        # 计算平均涨跌幅
        total_change = 0
        valid_count = 0
        for q in quotes:
            if q.get("涨跌幅") is not None:
                total_change += q["涨跌幅"]
                valid_count += 1

        if valid_count > 0:
            sector_results.append({
                "板块名称": sector_name,
                "涨跌幅": round(total_change / valid_count, 2),
                "成分股数": valid_count,
                "代表股票": [{"代码": q["代码"], "名称": q["名称"], "涨跌幅": q["涨跌幅"]}
                           for q in quotes[:5] if q.get("涨跌幅") is not None],
            })

    # 按涨跌幅排序
    sector_results.sort(key=lambda x: x["涨跌幅"], reverse=True)
    return sector_results[:top_n]


# ── 6. 北向资金 ─────────────────────────────────────────────────────

def get_north_flow(days: int = 5) -> dict[str, Any]:
    """
    获取北向/南向资金流向 (东方财富)

    Args:
        days: 最近几天

    Returns:
        分市场资金流向数据
    """
    try:
        data = _fetch_json(
            "push2.eastmoney.com",
            "/api/qt/kamt.kline/get",
            {
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "lmt": str(days),
                "klt": "101",
            },
        )
    except RuntimeError:
        return {}
    if not data.get("data"):
        return {}

    raw = data["data"]
    result = {}
    if raw.get("s2n"):
        result["沪股通-北向(净买入)"] = _parse_flow_kline(raw["s2n"])
    if raw.get("hk2sz"):
        result["深股通-北向(净买入)"] = _parse_flow_kline(raw["hk2sz"])
    if raw.get("sh2hk"):
        result["港股通(沪)-南向(净买入)"] = _parse_flow_kline(raw["sh2hk"])
    if raw.get("sz2hk"):
        result["港股通(深)-南向(净买入)"] = _parse_flow_kline(raw["sz2hk"])
    return result


def _parse_flow_kline(rows: list[str]) -> list[dict]:
    result = []
    for line in rows:
        parts = line.split(",")
        if len(parts) >= 4:
            result.append({
                "日期": parts[0],
                "流入额(元)": float(parts[1]) if parts[1] else 0,
                "流出额(元)": float(parts[2]) if parts[2] else 0,
                "净买入额(元)": float(parts[3]) if parts[3] else 0,
            })
    return result


# ── 7. 股票搜索 ─────────────────────────────────────────────────────

def search_stocks(
    keyword: str,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    按关键词搜索股票

    Args:
        keyword: 股票名称或代码关键词
        top_n:   返回条数

    Returns:
        匹配的股票列表
    """
    try:
        data = _fetch_json(
            "searchadapter.eastmoney.com",
            "/api/suggest/get",
            {
                "input": keyword,
                "type": "14",
                "count": str(top_n),
            },
        )
        results = []
        for item in data.get("QuotationCodeTable", {}).get("Data", []):
            code = item.get("Code", "")
            exchange = "SH" if code.startswith("6") else "SZ"
            results.append({
                "代码": code,
                "名称": item.get("Name", ""),
                "交易所": exchange,
            })
        return results
    except (RuntimeError, KeyError, ValueError):
        return _fallback_search(keyword, top_n)


def _fallback_search(keyword: str, top_n: int = 10) -> list[dict]:
    """本地搜索回退"""
    from cn_stock.data import STOCK_MAPPING
    keyword = keyword.upper()
    results = []
    for code, name in STOCK_MAPPING.items():
        if keyword in code or keyword in name:
            exchange = "SH" if code.startswith("6") else "SZ"
            results.append({"代码": code, "名称": name, "交易所": exchange})
            if len(results) >= top_n:
                break
    return results


# ── 工具函数 ─────────────────────────────────────────────────────────

def guess_secid(code: str) -> str:
    """根据股票代码推测 secid (市场前缀)"""
    code = code.strip().upper()
    if code.startswith("SH"):
        code = code[2:]
        return f"1.{code}"
    if code.startswith("SZ") or code.startswith("BJ"):
        code = code[2:]
        return f"0.{code}"
    if code.startswith("6") or code.startswith("9"):
        return f"1.{code}"
    return f"0.{code}"