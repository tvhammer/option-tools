import yfinance as yf
import pandas as pd
import math


def save_history_data(tickers: list[str], filename: str):
    stocks = yf.download(tickers, period="3y", group_by='ticker')
    if (len(tickers) == 1):
        stocks.columns = pd.MultiIndex.from_product(
            [[tickers[0]], stocks.columns])
    stocks.to_pickle(filename)


def get_info(ticker: str):
    stock = yf.Ticker(ticker)
    info = stock.info
    info["earningsDate"] = stock.earnings_dates.index[3] if len(
        stock.earnings_dates.index) else "N/A"
    return info


def load_history_data(filename: str):
    return pd.read_pickle(filename)


def get_percentage_changes(ticker: str, days: int, stocks: pd.DataFrame):
    stock = stocks[ticker]
    return 100 * \
        (stock['Close'].shift(-days) - stock['Close']) / stock['Close']


def calc_stats(ticker: str, days: int, stocks: pd.DataFrame):
    percentage_changes = get_percentage_changes(ticker, days, stocks)
    return {"std": percentage_changes.std(), "skew": percentage_changes.skew(), "kurtosis": percentage_changes.kurtosis()}


def calc_stats_all(tickers: list[str], days: int, stocks: pd.DataFrame):

    result = {}
    for t in tickers:

        result[t] = calc_stats(t, days, stocks)

    return result
