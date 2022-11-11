import barchart as bc
import yahoo_tools as y

def main():
    a=bc.get_liquid_indices()
    print(a)
    
    print(y.calc_volatility(["AAPL"],1))

if __name__=="__main__":
    main()