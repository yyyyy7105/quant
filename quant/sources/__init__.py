"""按市场拆分的行情数据源。

每个模块暴露统一签名:
    fetch_history(ticker: str, period: str = "5y") -> pd.DataFrame
返回以 date 为索引、列为 open/high/low/close/volume(小写、原始 OHLCV、不含指标)
的 DataFrame。指标在加载时由 quant.metrics 即时计算。

- us.py: yfinance(美股/全球)
- cn.py: akshare(A股,前复权)
"""
