import pandas as pd

import barchart as bc
import yahoo_tools as y
import matplotlib.pyplot as plt


def find_momentum(a200: float, a50: float):
    if (a200 and a50 and a200 > 0 and a50 > 0):
        ret = "up" if (a50 > a200) else "down"
    else:
        ret = "N/A"
    return ret


def draw_histogram(ticker: str, days: int, equities: pd.DataFrame):
    df = y.get_percentage_changes(ticker, days, equities)
    df.plot(kind='hist',bins=100)
    plt.show()


def assemble_stock_list(equities: pd.DataFrame, filename: str):

    y.save_history_data(equities["symbol"].to_list(),filename)
    stocks = y.load_history_data(filename)
    stats = y.calc_stats_all(equities["symbol"].array, 5, stocks)
    volatility_df = equities.symbol.apply(lambda x: stats[x]["std"])
    skew_df = equities.symbol.apply(lambda x: stats[x]["skew"])
    kurtosis_df = equities.symbol.apply(lambda x: stats[x]["kurtosis"])

    result = equities.assign(vol_5_std1=volatility_df)
    result = result.assign(vol_5_std2=volatility_df*2)
    result = result.assign(skew=skew_df)
    result = result.assign(kurtosis=kurtosis_df)
    info = result.symbol.apply(lambda x: y.get_info(x))
    result = result.assign(
        recommendation=info.apply(lambda x: x.get("recommendationKey")))
    result = result.assign(
        moomentum_200_50=info.apply(
            lambda x:
                find_momentum(x.get("twoHundredDayAverage"),
                              x.get("fiftyDayAverage"))
        ))
    return result


def main():

    print(assemble_stock_list(bc.get_liquid_stocks(), "stocks.pk1"))
    print(assemble_stock_list(bc.get_liquid_etfs(), "etfs.pk1"))
    print(assemble_stock_list(bc.get_liquid_indices(), "indices.pk1"))


if __name__ == "__main__":
    main()
