import yfinance as yf
import pandas as pd
import math


def calc_volatility(tickers, days):
    stocks = yf.download(tickers, period="1y", group_by='ticker')

    result = []
    for t in tickers:
        if (len(tickers) == 1):
            stock = stocks
        else:
            stock = stocks[t]
        stock['daily_returns'] = (stock['Close'].pct_change())*100

        daily_volatility = stock['daily_returns'].std()
        std1 = math.sqrt(days) * daily_volatility
        std2 = math.sqrt(days) * daily_volatility*2

        line = {"symbol": t, "std1": std1, "std2": std2}
        result.append(line)

    return result
