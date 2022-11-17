import pandas as pd

import barchart as bc
import yahoo_tools as y


def find_momentum(a200: float, a50: float):
    if (a200 and a50 and a200 > 0 and a50 > 0):
        ret = "up" if (a50 > a200) else "down"
    else:
        ret = "N/A"
    return ret


def assemble_stock_list(equities: pd.DataFrame):

    y.save_history_data(equities["symbol"].to_list())
    stocks = y.load_history_data()
    volatilities = y.calc_volatility_all(equities["symbol"].array, 5, stocks)
    volatility_df = equities.symbol.apply(lambda x: volatilities[x]["std1"])

    equities = equities.assign(vol_5_std1=volatility_df)
    equities = equities.assign(vol_5_std2=volatility_df*2)
    info = equities.symbol.apply(lambda x: y.get_info(x))
    equities = equities.assign(
        recommendation=info.apply(lambda x: x.get("recommendationKey")))
    equities = equities.assign(
        moomentum_200_50=info.apply(
            lambda x:
                find_momentum(x.get("twoHundredDayAverage"),
                              x.get("fiftyDayAverage"))
        ))
    print(equities)


def main():
    assemble_stock_list(bc.get_liquid_stocks())    
    assemble_stock_list(bc.get_liquid_etfs())
    assemble_stock_list(bc.get_liquid_indices())

if __name__ == "__main__":
    main()
