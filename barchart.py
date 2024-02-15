from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import pandas as pd

API_URL = "https://www.barchart.com/options/most-active"

options = Options()
options.headless = True
options.add_argument(
    "user-agent=Mozilla/5.0 (X11; Linux x86_64; rv:106.0) Gecko/20100101 Firefox/106.0")
options.add_argument("--window-size=1920,1200")


def expand_shadow_element(element, driver):
    shadow_root = driver.execute_script(
        'return arguments[0].shadowRoot', element)
    return shadow_root


def get_liquid(selector: str):
    driver = webdriver.Chrome(options=options)
    driver.get(
        f'{API_URL}/{selector}?orderBy=optionsTotalVolume&orderDir=desc')

    root = driver.find_element(By.TAG_NAME, 'bc-data-grid')
    shadow_root = expand_shadow_element(root, driver)

    # xpath does not work within shadowroot
    lines = shadow_root.find_elements(By.CLASS_NAME, 'row')

    result = []
    for line in lines:
        l = {"symbol": line.find_element(By.CLASS_NAME, 'symbol').text,
             "volume": line.find_element(By.CLASS_NAME, 'optionsTotalVolume').text,
             "put_call_ratio": line.find_element(By.CLASS_NAME, 'optionsPutCallVolumeRatio').text,
             "ivr": line.find_element(By.CLASS_NAME, 'optionsImpliedVolatilityRank1y').text}

        result.append(l)
    driver.quit()

    result_df = pd.DataFrame(result)

    result_df['ivr'] = result_df['ivr'].transform(
        lambda x: x.replace('%', '')).astype(float)


    result_df["symbol"] = result_df["symbol"].transform(lambda x: x.replace(
        '$', '^').replace('^ONE', '^SP100').replace('^IUXX', '^NDX'))

    return result_df


def get_liquid_stocks():
    return get_liquid("stocks")


def get_liquid_etfs():
    return get_liquid("etfs")


def get_liquid_indices():
    return get_liquid("indices")
