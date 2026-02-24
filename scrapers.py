"""
scrapers.py — All price scraping and image lookup logic
=========================================================
Kept separate from app.py so it's easy to update scrapers
without touching the web routes.
"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import requests
import os
import time

NUM_SALES = 5

CONDITION_KEYWORDS = {
    "PSA 10":            ["psa 10", "psa10"],
    "PSA 9":             ["psa 9", "psa9"],
    "PSA 8":             ["psa 8", "psa8"],
    "BGS 9.5":           ["bgs 9.5", "bgs9.5"],
    "BGS 9":             ["bgs 9", "bgs9"],
    "Near Mint":         ["near mint", "nm"],
    "Lightly Played":    ["lightly played", "lp"],
    "Moderately Played": ["moderately played", "mp"],
    "Heavily Played":    ["heavily played", "hp"],
    "Damaged":           ["damaged", "dmg"],
}


def make_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )


def get_page_source(url, wait_for_class=None):
    driver = make_driver()
    driver.get(url)
    if wait_for_class:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, wait_for_class))
            )
        except:
            pass
    time.sleep(2)
    source = driver.page_source
    driver.quit()
    return source


def scrape_ebay(card_name, condition):
    search_term = card_name.replace(" ", "+")
    url  = f"https://www.ebay.com/sch/i.html?_nkw={search_term}&LH_Sold=1&LH_Complete=1"
    html = get_page_source(url, wait_for_class="s-item")
    soup = BeautifulSoup(html, "html.parser")
    keywords = CONDITION_KEYWORDS.get(condition, [])
    prices   = []

    for listing in soup.find_all("li", class_="s-item"):
        if len(prices) >= NUM_SALES:
            break
        title_tag = listing.find("div", class_="s-item__title")
        price_tag = listing.find("span", class_="s-item__price")
        if not title_tag or not price_tag:
            continue
        if any(k in title_tag.get_text(separator=" ").lower() for k in keywords):
            pt = price_tag.get_text(strip=True)
            if " to " in pt:
                pt = pt.split(" to ")[0]
            try:
                prices.append(round(float(pt.replace("$","").replace(",","")), 2))
            except ValueError:
                continue
    return prices


def scrape_tcgplayer(card_name, condition):
    search_term = card_name.replace(" ", "+")
    url  = f"https://www.tcgplayer.com/search/all/product?q={search_term}&view=grid"
    html = get_page_source(url, wait_for_class="search-result")
    soup = BeautifulSoup(html, "html.parser")
    keywords = CONDITION_KEYWORDS.get(condition, [])
    prices   = []

    for listing in soup.find_all("div", class_="search-result"):
        if len(prices) >= NUM_SALES:
            break
        if any(k in listing.get_text(separator=" ").lower() for k in keywords):
            pt = listing.find("span", class_="product-listing__price")
            if pt:
                try:
                    prices.append(round(float(pt.get_text(strip=True).replace("$","").replace(",","")), 2))
                except ValueError:
                    continue
    return prices


def scrape_cardladder(card_name, condition):
    email    = os.environ.get("CARDLADDER_EMAIL", "")
    password = os.environ.get("CARDLADDER_PASSWORD", "")

    if not email or not password:
        return []

    keywords = CONDITION_KEYWORDS.get(condition, [])
    driver   = make_driver()
    prices   = []

    try:
        driver.get("https://www.cardladder.com/users/sign_in")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "user_email")))
        driver.find_element(By.ID, "user_email").send_keys(email)
        driver.find_element(By.ID, "user_password").send_keys(password)
        driver.find_element(By.ID, "user_password").send_keys(Keys.RETURN)
        time.sleep(3)

        if "invalid" in driver.page_source.lower():
            return []

        driver.get(f"https://www.cardladder.com/cards?q={card_name.replace(' ', '+')}")
        time.sleep(3)

        soup      = BeautifulSoup(driver.page_source, "html.parser")
        card_link = soup.select_one("a.card-result, a[href*='/cards/']")

        if not card_link:
            return []

        href = card_link.get("href", "")
        if not href.startswith("http"):
            href = "https://www.cardladder.com" + href

        driver.get(href)
        time.sleep(3)

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr"))
            )
        except:
            return []

        soup = BeautifulSoup(driver.page_source, "html.parser")
        for row in soup.select("table tbody tr"):
            if len(prices) >= NUM_SALES:
                break
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            if any(k in cells[1].get_text(strip=True).lower() for k in keywords):
                try:
                    prices.append(round(float(
                        cells[2].get_text(strip=True).replace("$","").replace(",","")
                    ), 2))
                except ValueError:
                    continue
    except Exception as e:
        print(f"Card Ladder error: {e}")
    finally:
        driver.quit()

    return prices


def fetch_card_image(card_name):
    # Source 1: Pokémon TCG API
    try:
        r = requests.get("https://api.pokemontcg.io/v2/cards",
            params={"q": f'name:"{card_name}"', "pageSize": 1}, timeout=5)
        data = r.json()
        if data.get("data"):
            img = data["data"][0].get("images", {}).get("large")
            if img:
                return {"url": img, "source": "Pokémon TCG API"}
    except:
        pass

    # Source 2: Scryfall (MTG)
    try:
        r = requests.get("https://api.scryfall.com/cards/named",
            params={"fuzzy": card_name}, timeout=5)
        data = r.json()
        if data.get("object") != "error":
            img = (data.get("image_uris", {}).get("large") or
                   data.get("image_uris", {}).get("normal") or
                   (data.get("card_faces", [{}])[0].get("image_uris", {}).get("large")))
            if img:
                return {"url": img, "source": "Scryfall (MTG)"}
    except:
        pass

    # Source 3: TCGPlayer scrape
    try:
        html = get_page_source(
            f"https://www.tcgplayer.com/search/all/product?q={card_name.replace(' ','+')}&view=grid",
            wait_for_class="search-result")
        soup = BeautifulSoup(html, "html.parser")
        tag  = soup.select_one(".search-result img, .product-card__image img")
        if tag:
            src = tag.get("src") or tag.get("data-src")
            if src and len(src) > 100 and "placeholder" not in src.lower():
                return {"url": src, "source": "TCGPlayer"}
    except:
        pass

    # Source 4: eBay fallback
    try:
        html = get_page_source(
            f"https://www.ebay.com/sch/i.html?_nkw={card_name.replace(' ','+')}&LH_Sold=1",
            wait_for_class="s-item")
        soup = BeautifulSoup(html, "html.parser")
        for listing in soup.find_all("li", class_="s-item"):
            tag = listing.find("img", class_="s-item__image-img")
            if tag:
                src = tag.get("src") or tag.get("data-src")
                if src and "s-l" in src and "gif" not in src:
                    return {"url": src, "source": "eBay"}
    except:
        pass

    return None
