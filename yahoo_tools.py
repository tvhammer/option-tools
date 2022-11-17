import yfinance as yf
import pandas as pd
import math


def save_history_data(tickers: list[str]):
    stocks = yf.download(tickers, period="3y", group_by='ticker')
    if (len(tickers) == 1):
        stocks.columns = pd.MultiIndex.from_product(
            [[tickers[0]], stocks.columns])
    stocks.to_pickle("history.pk1")


def get_info(ticker: str):
    stock = yf.Ticker(ticker)
    return stock.info


def load_history_data():
    return pd.read_pickle("history.pk1")


def calc_volatility(ticker: str, days: int, stocks: pd.DataFrame):
    stock = stocks[ticker]
    period_change = 100 * \
        (stock['Close'].shift(-days) - stock['Close']) / stock['Close']
    return period_change.std()


def calc_volatility_all(tickers: list[str], days: int, stocks: pd.DataFrame):

    result = {}
    for t in tickers:

        std1 = calc_volatility(t, days, stocks)
        result[t] = {"std1": std1}

    return result
