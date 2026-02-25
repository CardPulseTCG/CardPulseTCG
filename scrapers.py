"""
scrapers.py — CardPulse TCG Price Lookup
==========================================
Uses official APIs instead of Selenium/Chrome scraping.
Works perfectly on Render and any other hosting platform.

APIs used:
  1. pokemontcg.io     — Pokémon card data, images, and TCGPlayer prices
  2. tcgapi.dev        — Pokémon + One Piece prices (from TCGPlayer)
  3. optcgapi.com      — One Piece card data and prices (no key needed)

DECISION-MAKING:
  - Pokémon searches hit pokemontcg.io first for card data + image
  - Price data comes from tcgapi.dev (most comprehensive)
  - One Piece searches hit both tcgapi.dev and optcgapi.com
  - Results are combined and averaged across sources
  - IF a source fails or returns nothing → skip it, don't crash
"""

import requests
import os

# ── API Keys (loaded from .env) ──────────────────────────────────────────────
POKEMON_TCG_API_KEY = os.environ.get("POKEMON_TCG_API_KEY", "")
TCG_API_KEY         = os.environ.get("TCG_API_KEY", "")

# ── Base URLs ────────────────────────────────────────────────────────────────
POKEMON_TCG_BASE = "https://api.pokemontcg.io/v2"
TCG_API_BASE     = "https://api.tcgapi.dev/v1"
OPTCG_BASE       = "https://optcgapi.com/api"

# ── Condition options ─────────────────────────────────────────────────────────
CONDITION_OPTIONS = [
    "PSA 10", "PSA 9", "PSA 8",
    "BGS 9.5", "BGS 9",
    "Near Mint", "Lightly Played",
    "Moderately Played", "Heavily Played", "Damaged",
]


# ════════════════════════════════════════════════════════════════════════════
# POKÉMON — pokemontcg.io
# ════════════════════════════════════════════════════════════════════════════

def search_pokemon_tcg_io(card_name, condition):
    """
    Searches pokemontcg.io for a Pokémon card.
    Returns a list of prices (floats) in USD.
    """
    try:
        headers = {}
        if POKEMON_TCG_API_KEY:
            headers["X-Api-Key"] = POKEMON_TCG_API_KEY
            print(f"pokemontcg.io: using API key (length {len(POKEMON_TCG_API_KEY)})")
        else:
            print("pokemontcg.io: NO API KEY — using rate-limited access")

        params = {
            "q":        f'name:"{card_name}"',
            "pageSize": 10,
        }

        r = requests.get(f"{POKEMON_TCG_BASE}/cards",
                         headers=headers, params=params, timeout=8)

        print(f"pokemontcg.io: status {r.status_code}, cards returned: {len(r.json().get('data', []))}")

        if r.status_code != 200:
            print(f"pokemontcg.io error response: {r.text[:200]}")
            return []

        data   = r.json().get("data", [])
        prices = []

        for card in data:
            tcgplayer  = card.get("tcgplayer", {})
            price_data = tcgplayer.get("prices", {})

            for tier in ["normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"]:
                tier_prices = price_data.get(tier, {})
                market = tier_prices.get("market")
                if market and float(market) > 0:
                    prices.append(round(float(market), 2))
                    break

        print(f"pokemontcg.io: found {len(prices)} prices: {prices[:3]}")
        return prices[:5]

    except Exception as e:
        print(f"pokemontcg.io error: {e}")
        return []


def fetch_pokemon_image(card_name):
    """Fetches card image from pokemontcg.io."""
    try:
        headers = {}
        if POKEMON_TCG_API_KEY:
            headers["X-Api-Key"] = POKEMON_TCG_API_KEY

        r = requests.get(f"{POKEMON_TCG_BASE}/cards",
                         headers=headers,
                         params={"q": f'name:"{card_name}"', "pageSize": 1},
                         timeout=8)

        if r.status_code != 200:
            return None

        data = r.json().get("data", [])
        if data:
            images = data[0].get("images", {})
            return images.get("large") or images.get("small")

    except Exception as e:
        print(f"Pokemon image fetch error: {e}")

    return None


# ════════════════════════════════════════════════════════════════════════════
# ALL GAMES — tcgapi.dev
# ════════════════════════════════════════════════════════════════════════════

def search_tcgapi_dev(card_name, game="pokemon"):
    """
    Searches tcgapi.dev for card prices.
    Works for both Pokémon and One Piece.
    """
    if not TCG_API_KEY:
        print("tcgapi.dev: NO API KEY — skipping")
        return []

    try:
        headers = {"Authorization": f"Bearer {TCG_API_KEY}"}
        params  = {"q": card_name, "game": game}

        r = requests.get(f"{TCG_API_BASE}/search",
                         headers=headers, params=params, timeout=8)

        print(f"tcgapi.dev: status {r.status_code} for '{card_name}' ({game})")

        if r.status_code != 200:
            print(f"tcgapi.dev error response: {r.text[:200]}")
            return []

        data   = r.json().get("data", [])
        prices = []

        for card in data[:5]:
            price = card.get("price")
            if price and float(price) > 0:
                prices.append(round(float(price), 2))

        print(f"tcgapi.dev: found {len(prices)} prices: {prices[:3]}")
        return prices

    except Exception as e:
        print(f"tcgapi.dev error: {e}")
        return []


def fetch_tcgapi_image(card_name, game="pokemon"):
    """Fetches card image from tcgapi.dev."""
    if not TCG_API_KEY:
        return None

    try:
        headers = {"Authorization": f"Bearer {TCG_API_KEY}"}
        params  = {"q": card_name, "game": game}

        r = requests.get(f"{TCG_API_BASE}/search",
                         headers=headers, params=params, timeout=8)

        if r.status_code != 200:
            return None

        data = r.json().get("data", [])
        if data:
            return data[0].get("image") or data[0].get("image_url")

    except Exception as e:
        print(f"tcgapi.dev image error: {e}")

    return None


# ════════════════════════════════════════════════════════════════════════════
# ONE PIECE — optcgapi.com (no API key needed)
# ════════════════════════════════════════════════════════════════════════════

def search_optcgapi(card_name):
    """
    Searches optcgapi.com for One Piece card prices.
    No API key required.
    Returns (prices list, image url or None)
    """
    try:
        r = requests.get(f"{OPTCG_BASE}/sets/cards/",
                         params={"name": card_name},
                         timeout=8)

        if r.status_code != 200:
            return [], None

        data  = r.json()
        cards = data if isinstance(data, list) else data.get("results", [])
        prices = []
        image  = None

        for card in cards[:5]:
            for price_field in ["market_price", "price", "low_price", "tcgplayer_price"]:
                p = card.get(price_field)
                if p and float(p) > 0:
                    prices.append(round(float(p), 2))
                    break

            if not image:
                image = card.get("card_image") or card.get("image_url") or card.get("image")

        return prices, image

    except Exception as e:
        print(f"optcgapi.com error: {e}")
        return [], None


# ════════════════════════════════════════════════════════════════════════════
# MAIN SEARCH FUNCTION
# ════════════════════════════════════════════════════════════════════════════

def search_card_prices(card_name, condition, game="pokemon"):
    """
    Main entry point for price searching.
    Combines results from all available APIs.

    DECISION-MAKING:
    - IF game is 'pokemon' → use pokemontcg.io + tcgapi.dev
    - IF game is 'one_piece' → use tcgapi.dev + optcgapi.com
    - Combines all prices for overall average
    - IF no prices found anywhere → returns empty result
    """
    platforms  = []
    all_prices = []
    card_image = None

    if game == "pokemon":
        # Source 1: pokemontcg.io
        p1 = search_pokemon_tcg_io(card_name, condition)
        platforms.append(summarize(p1, "pokemontcg.io"))
        all_prices.extend(p1)

        if not card_image:
            img = fetch_pokemon_image(card_name)
            if img:
                card_image = {"url": img, "source": "pokemontcg.io"}

        # Source 2: tcgapi.dev
        p2 = search_tcgapi_dev(card_name, game="pokemon")
        platforms.append(summarize(p2, "tcgapi.dev"))
        all_prices.extend(p2)

        if not card_image:
            img = fetch_tcgapi_image(card_name, game="pokemon")
            if img:
                card_image = {"url": img, "source": "tcgapi.dev"}

    elif game == "one_piece":
        # Source 1: tcgapi.dev
        p1 = search_tcgapi_dev(card_name, game="one_piece")
        platforms.append(summarize(p1, "tcgapi.dev"))
        all_prices.extend(p1)

        if not card_image:
            img = fetch_tcgapi_image(card_name, game="one_piece")
            if img:
                card_image = {"url": img, "source": "tcgapi.dev"}

        # Source 2: optcgapi.com
        p2, op_image = search_optcgapi(card_name)
        platforms.append(summarize(p2, "optcgapi.com"))
        all_prices.extend(p2)

        if not card_image and op_image:
            card_image = {"url": op_image, "source": "optcgapi.com"}

    combined_average = None
    if all_prices:
        combined_average = round(sum(all_prices) / len(all_prices), 2)

    return {
        "platforms":        platforms,
        "combined_average": combined_average,
        "total_sales":      len(all_prices),
        "card_image":       card_image,
    }


def summarize(prices, platform):
    """Turns a list of prices into a summary dict."""
    if not prices:
        return {"platform": platform, "prices": [], "average": None, "high": None, "low": None}
    return {
        "platform": platform,
        "prices":   prices,
        "average":  round(sum(prices) / len(prices), 2),
        "high":     max(prices),
        "low":      min(prices),
    }


# ════════════════════════════════════════════════════════════════════════════
# CARD IMAGE LOOKUP
# Used by storefronts to show card images for listings
# ════════════════════════════════════════════════════════════════════════════

def fetch_card_image(card_name):
    """
    Fetches a card image for any card name.
    Tries Pokémon first, then One Piece, then Scryfall for MTG.
    """
    # Try Pokémon
    img = fetch_pokemon_image(card_name)
    if img:
        return {"url": img, "source": "pokemontcg.io"}

    # Try tcgapi.dev
    img = fetch_tcgapi_image(card_name, game="pokemon")
    if img:
        return {"url": img, "source": "tcgapi.dev"}

    img = fetch_tcgapi_image(card_name, game="one_piece")
    if img:
        return {"url": img, "source": "tcgapi.dev (One Piece)"}

    # Fallback: Scryfall for MTG cards
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
    except Exception:
        pass

    return None
