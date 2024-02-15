import time

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

API_URL = "https://marketchameleon.com/volReports/VolatilityRankings"

options = Options()
# options.headless = True   # This does not seem to work with this page
options.add_argument(
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36")
options.add_argument("--window-size=1920,1200")
options.add_argument('--disable-blink-features=AutomationControlled')


def get_option_list():
    driver = webdriver.Chrome(options=options)
    driver.get(API_URL)

    driver.find_element(
        By.XPATH, "//select[@name='iv_rankings_report_tbl_length']//option[@value='100']").click()
    header = driver.find_element(
        By.XPATH, "//th[normalize-space(text()[1])='Current' and normalize-space(text()[2])='Option' and normalize-space(text()[3])='Volume']")
    header.click()
    header.click()

    time.sleep(5)

    tickers = []
    for e in driver.find_elements(By.XPATH, "//table[@id='iv_rankings_report_tbl']//a[@class='mplink popup_link']"):
        tickers.append(e.text)

    ivrs = []
    for e in driver.find_elements(By.XPATH, "//table[@id='iv_rankings_report_tbl']//tr//td[10]"):
        ivrs.append(e.text)
    volumes = []
    for e in driver.find_elements(By.XPATH, "//table[@id='iv_rankings_report_tbl']//tr//td[12]"):
        volumes.append(e.text)
    stocks = pd.DataFrame()
    stocks = stocks.assign(symbol=pd.Series(tickers))
    stocks = stocks.assign(ivr=pd.Series(ivrs))
    stocks = stocks.assign(volume=pd.Series(volumes))
    stocks['ivr'] = stocks['ivr'].transform(
        lambda x: x.replace('%', '')).astype(float)

    #stocks = stocks[stocks['ivr'] > 55]
    return stocks
