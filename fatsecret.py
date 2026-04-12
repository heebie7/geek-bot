"""
FatSecret REST API integration for food nutrition lookup.

Flow:
1. Get OAuth2 token (client_credentials)
2. Search food by name → pick best match
3. Get per-100g nutrition → scale by weight_g
"""

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
_API_URL = "https://platform.fatsecret.com/rest/server.api"

_cached_token: str | None = None
_token_expires: float = 0.0


def _get_token() -> str | None:
    """Get OAuth2 access token (cached, refreshed on expiry)."""
    global _cached_token, _token_expires
    if _cached_token and time.time() < _token_expires - 60:
        return _cached_token

    client_id = os.getenv("FATSECRET_CLIENT_ID")
    client_secret = os.getenv("FATSECRET_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    try:
        r = requests.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": "basic"},
            auth=(client_id, client_secret),
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        _cached_token = data["access_token"]
        _token_expires = time.time() + data.get("expires_in", 86400)
        return _cached_token
    except Exception as e:
        logger.warning(f"FatSecret token error: {e}")
        return None


def _api(method: str, params: dict) -> dict | None:
    """Make authenticated FatSecret API call."""
    token = _get_token()
    if not token:
        return None
    try:
        r = requests.get(
            _API_URL,
            params={"method": method, "format": "json", **params},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"FatSecret API error ({method}): {e}")
        return None


def _per100g(serving: dict) -> dict | None:
    """Extract nutrition per 100g from a serving dict. Returns None if can't normalize."""
    try:
        serving_g_str = serving.get("metric_serving_amount", "")
        unit = serving.get("metric_serving_unit", "").lower()
        if unit != "g" or not serving_g_str:
            # Try description-based fallback for "100 g" servings
            desc = serving.get("serving_description", "").lower()
            if "100g" in desc or "100 g" in desc:
                ratio = 1.0
            else:
                return None
        else:
            serving_g = float(serving_g_str)
            if serving_g <= 0:
                return None
            ratio = 100.0 / serving_g

        def _f(key: str) -> float:
            v = serving.get(key)
            return round(float(v) * ratio, 1) if v else 0.0

        return {
            "kcal_per100": _f("calories"),
            "protein_per100": _f("protein"),
            "fat_per100": _f("fat"),
            "carbs_per100": _f("carbohydrate"),
            "fiber_per100": _f("fiber"),
            "calcium_per100": _f("calcium"),
        }
    except (ValueError, TypeError):
        return None


def lookup(food_name: str, weight_g: int) -> dict | None:
    """
    Look up food in FatSecret and return scaled KBJU for weight_g grams.

    Returns dict with keys: kcal, protein, fat, carbs, fiber, calcium, fs_name
    or None if not found / API unavailable.
    """
    if not food_name or weight_g <= 0:
        return None

    # Search
    search_data = _api("foods.search", {
        "search_expression": food_name,
        "max_results": 5,
        "page_number": 0,
    })
    if not search_data:
        return None

    foods_obj = search_data.get("foods", {})
    food_list = foods_obj.get("food", [])
    if isinstance(food_list, dict):
        food_list = [food_list]
    if not food_list:
        return None

    # Pick first generic (non-branded) match, fall back to first
    chosen = None
    for f in food_list:
        if f.get("food_type", "").lower() == "generic":
            chosen = f
            break
    if not chosen:
        chosen = food_list[0]

    food_id = chosen.get("food_id")
    fs_name = chosen.get("food_name", food_name)
    if not food_id:
        return None

    # Get full nutrition
    detail = _api("food.get.v4", {"food_id": food_id})
    if not detail:
        return None

    servings_obj = detail.get("food", {}).get("servings", {})
    servings = servings_obj.get("serving", [])
    if isinstance(servings, dict):
        servings = [servings]
    if not servings:
        return None

    # Find a serving we can normalize to per-100g
    per100 = None
    for s in servings:
        per100 = _per100g(s)
        if per100:
            break

    if not per100:
        return None

    ratio = weight_g / 100.0
    return {
        "kcal": round(per100["kcal_per100"] * ratio),
        "protein": round(per100["protein_per100"] * ratio, 1),
        "fat": round(per100["fat_per100"] * ratio, 1),
        "carbs": round(per100["carbs_per100"] * ratio, 1),
        "fiber": round(per100["fiber_per100"] * ratio, 1),
        "calcium": round(per100["calcium_per100"] * ratio),
        "fs_name": fs_name,
    }
