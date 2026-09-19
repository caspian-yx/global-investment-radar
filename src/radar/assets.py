from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalAsset:
    key: str
    label_zh: str
    label_en: str
    asset_class: str
    aliases: tuple[str, ...]


_ASSETS: tuple[CanonicalAsset, ...] = (
    CanonicalAsset("SP500", "标普500", "S&P 500", "equity", ("^GSPC", "SP500")),
    CanonicalAsset("NASDAQ100", "纳斯达克100", "Nasdaq 100", "equity", ("^NDX", "NASDAQ100")),
    CanonicalAsset("DOW", "道琼斯", "Dow Jones", "equity", ("^DJI", "DOW")),
    CanonicalAsset("RUSSELL2000", "罗素2000", "Russell 2000", "equity", ("^RUT", "RUSSELL2000")),
    CanonicalAsset("VIX", "VIX", "VIX", "volatility", ("^VIX", "VIX")),
    CanonicalAsset("VXN", "VXN", "VXN", "volatility", ("^VXN", "VXN")),
    CanonicalAsset(
        "US_2Y", "美国2年期国债", "US 2Y Treasury", "rate", ("DGS2", "^UST2Y", "US2Y", "2Y")
    ),
    CanonicalAsset("US_10Y", "美国10年期国债", "US 10Y Treasury", "rate", ("^TNX", "US10Y", "10Y")),
    CanonicalAsset("DXY", "美元指数", "DXY", "fx", ("DX-Y.NYB", "DXY")),
    CanonicalAsset("WTI", "WTI原油", "WTI Crude", "commodity", ("CL=F", "WTI")),
    CanonicalAsset("BRENT", "布伦特原油", "Brent Crude", "commodity", ("BZ=F", "BRENT")),
    CanonicalAsset("GOLD", "黄金", "Gold", "commodity", ("GC=F", "GOLD")),
    CanonicalAsset("SSE", "上证指数", "SSE Composite", "equity", ("000001.SS", "^SSEC", "SSE")),
    CanonicalAsset(
        "SHENZHEN", "深证成指", "Shenzhen Component", "equity", ("399001.SZ", "SHENZHEN")
    ),
    CanonicalAsset("CSI300", "沪深300", "CSI 300", "equity", ("000300.SS", "510300.SS", "CSI300")),
    CanonicalAsset("CSI_A500", "中证A500", "CSI A500", "equity", ("000510.CSI", "CSI_A500")),
    CanonicalAsset("CHINEXT", "创业板指", "ChiNext", "equity", ("399006.SZ", "CHINEXT")),
    CanonicalAsset("STAR50", "科创50", "STAR 50", "equity", ("000688.SS", "STAR50")),
    CanonicalAsset("CSI500", "中证500", "CSI 500", "equity", ("000905.SS", "CSI500")),
    CanonicalAsset("CSI1000", "中证1000", "CSI 1000", "equity", ("000852.SS", "CSI1000")),
    CanonicalAsset("HSI", "恒生指数", "Hang Seng", "equity", ("^HSI", "HSI")),
    CanonicalAsset(
        "HSTECH", "恒生科技", "Hang Seng TECH", "equity", ("3033.HK", "^HSTECH", "HSTECH")
    ),
    CanonicalAsset("NIKKEI", "日经225", "Nikkei 225", "equity", ("^N225", "NIKKEI")),
    CanonicalAsset("KOSPI", "韩国综合指数", "KOSPI", "equity", ("^KS11", "KOSPI")),
    CanonicalAsset("STOXX600", "欧洲斯托克600", "STOXX 600", "equity", ("^STOXX", "STOXX600")),
    CanonicalAsset(
        "EUROSTOXX50", "欧洲斯托克50", "EURO STOXX 50", "equity", ("^STOXX50E", "EUROSTOXX50")
    ),
    CanonicalAsset("DAX", "德国DAX", "DAX", "equity", ("^GDAXI", "DAX")),
    CanonicalAsset("FTSE", "英国富时100", "FTSE 100", "equity", ("^FTSE", "FTSE")),
    CanonicalAsset("EURUSD", "欧元/美元", "EUR/USD", "fx", ("EURUSD=X", "EURUSD")),
    CanonicalAsset("USDJPY", "美元/日元", "USD/JPY", "fx", ("JPY=X", "USDJPY")),
    CanonicalAsset("USDCNH", "美元/离岸人民币", "USD/CNH", "fx", ("CNH=X", "USDCNH")),
    CanonicalAsset("BTC", "比特币", "Bitcoin", "crypto", ("BTC-USD", "BTC")),
    CanonicalAsset("ETH", "以太坊", "Ethereum", "crypto", ("ETH-USD", "ETH")),
)

_BY_ALIAS = {alias.casefold(): item for item in _ASSETS for alias in item.aliases}
_BY_KEY = {item.key.casefold(): item for item in _ASSETS}


def canonical_asset(symbol_or_key: str) -> CanonicalAsset | None:
    """Resolve provider symbols and fixture keys without changing provider identity."""

    normalized = symbol_or_key.strip().casefold()
    return _BY_ALIAS.get(normalized) or _BY_KEY.get(normalized)


def canonical_assets() -> tuple[CanonicalAsset, ...]:
    return _ASSETS
