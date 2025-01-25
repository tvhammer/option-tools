import math

import backoff
import pandas as pd
import yfinance as yf


def save_history_data(tickers: list[str], filename: str):
    stocks = yf.download(tickers, period="3y", group_by='ticker')
    if (len(tickers) == 1):
        stocks.columns = pd.MultiIndex.from_product(
            [[tickers[0]], stocks.columns])
    stocks.to_pickle(filename)


@backoff.on_exception(backoff.expo,
                      Exception)
def get_info(ticker: str):
    stock = yf.Ticker(ticker)
    info = stock.info
    earnings_dates = stock.earnings_dates
        
    info["earningsDate"] = earnings_dates.index[3] if earnings_dates is not None and len(
        earnings_dates.index) > 3 else "N/A"
    return info


def load_history_data(filename: str):
    return pd.read_pickle(filename)


def get_percentage_changes(ticker: str, days: int, stocks: pd.DataFrame):
    stock = stocks[ticker]
    return 100 * \
        (stock['Close'].shift(-days) - stock['Close']) / stock['Close']


def calc_stats(ticker: str, days: int, stocks: pd.DataFrame):
    percentage_changes = get_percentage_changes(ticker, days, stocks)
    std1 = percentage_changes.std()
    return {"symbol": ticker, "std1": std1, "std2": std1*2, "skew": percentage_changes.skew(), "kurtosis": percentage_changes.kurtosis()}


def calc_stats_all(tickers: list[str], days: int, stocks: pd.DataFrame):

    result = []
    for t in tickers:

        result.append(calc_stats(t, days, stocks))

    return pd.DataFrame(result)


@backoff.on_exception(backoff.expo,
                      Exception)
def get_option_volume_data(ticker: str):
    stock = yf.Ticker(ticker)
    calls = stock.option_chain()[0]
    puts = stock.option_chain()[1]
    call_vol = calls.volume.sum()
    put_vol = puts.volume.sum()
    call_oi = calls.openInterest.sum()
    put_oi = puts.openInterest.sum()
    put_call_ratio = put_vol/call_vol
    put_call_oi_ratio = put_oi/call_oi
    vol_yahoo=put_vol+call_vol
    return {"symbol": ticker, "put_call_ratio": put_call_ratio, "put_call_oi_ratio": put_call_oi_ratio, "vol_yahoo": vol_yahoo}


def get_all_option_volume_data(tickers: list[str]):
    matrix = []
    for t in tickers:
        matrix.append(get_option_volume_data(t))
    return pd.DataFrame(matrix)

def fetch_and_sort_tickers_list(ticker_list: list[str]):
    # Fetch ticker info and store in a list of dictionaries
    ticker_info_list = [{'ticker': ticker, 'info': yf.Ticker(ticker).info} for ticker in ticker_list]

    # Convert list of dictionaries to DataFrame
    df = pd.DataFrame(ticker_info_list)

    # Extract averageVolume and sort by it
    df['averageVolume'] = df['info'].apply(lambda x: x.get('averageVolume', 0))
    df_sorted = df.sort_values('averageVolume', ascending=False)

    return df_sorted

def fetch_and_sort_tickers(tickers: str):
    thicker_list = tickers.split(',')
    return fetch_and_sort_tickers_list(thicker_list)
