import matplotlib.pyplot as plt
import pandas as pd

import barchart as bc
import marketchameleon as mc
import yahoo_tools as y


def find_momentum(a200: float, a50: float):
    if (a200 and a50 and a200 > 0 and a50 > 0):
        ret = "up" if (a50 > a200) else "down"
    else:
        ret = "N/A"
    return ret


def draw_histogram(ticker: str, days: int, equities: pd.DataFrame):
    df = y.get_percentage_changes(ticker, days, equities)
    df.plot(kind='hist', bins=100)
    plt.show()


def assemble_stock_list(equities: pd.DataFrame, filename: str):

    y.save_history_data(equities["symbol"].to_list(), filename)
    stocks = y.load_history_data(filename)
    stats = y.calc_stats_all(equities["symbol"].array, 5, stocks)

    result = pd.merge(equities, stats, how='inner')
    info = result.symbol.apply(lambda x: y.get_info(x))
    result = result.assign(
        recommendation=info.apply(lambda x: x.get("recommendationKey")))
    result = result.assign(
        momentum_200_50=info.apply(
            lambda x:
                find_momentum(x.get("twoHundredDayAverage"),
                              x.get("fiftyDayAverage"))
        ))
    result = result.assign(earningsdate=info.apply(
        lambda x: x.get("earningsDate")))
    return result

def add_volume_data(equities: pd.DataFrame):
    vol_data = y.get_all_option_volume_data(equities["symbol"].array)
    result = pd.merge(equities, vol_data, how='inner')
    return result


