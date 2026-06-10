from __future__ import annotations

import time
from typing import Any, Iterator

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Fields we want back. Costs scale with field mask, so request only what we use.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.types",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.rating",
        "places.userRatingCount",
        "places.googleMapsUri",
        "places.businessStatus",
        "places.primaryType",
        "nextPageToken",
    ]
)

# Niche -> (search keyword, prospect_industry enum value).
# Keys match the PRD target niche list (lowercased).
NICHE_MAP: dict[str, tuple[str, str]] = {
    "restaurants":            ("restaurant",                "restaurant"),
    "restaurant":             ("restaurant",                "restaurant"),
    "real estate":            ("real estate agency",        "real_estate"),
    "real_estate":            ("real estate agency",        "real_estate"),
    "dentists":               ("dentist",                   "dentist"),
    "dentist":                ("dentist",                   "dentist"),
    "clinics":                ("medical clinic",            "clinic"),
    "clinic":                 ("medical clinic",            "clinic"),
    "hotels":                 ("hotel",                     "hotel"),
    "hotel":                  ("hotel",                     "hotel"),
    "salons":                 ("beauty salon",              "salon"),
    "salon":                  ("beauty salon",              "salon"),
    "gyms":                   ("gym",                       "gym"),
    "gym":                    ("gym",                       "gym"),
    "law firms":              ("law firm",                  "law_firm"),
    "law_firm":               ("law firm",                  "law_firm"),
    "construction":           ("construction company",      "construction"),
    "educational institutes": ("school",                    "education"),
    "education":              ("school",                    "education"),
    "ecommerce stores":       ("retail store",              "ecommerce"),
    "ecommerce":              ("retail store",              "ecommerce"),
    "local businesses":       ("local business",            "local_business"),
}


def resolve_niche(niche: str) -> tuple[str, str]:
    """Return (search_keyword, industry_enum) for a PRD niche."""
    key = niche.strip().lower()
    if key in NICHE_MAP:
        return NICHE_MAP[key]
    # Unknown niche -> search verbatim, classify as 'other'.
    return (niche, "other")


class GooglePlacesError(RuntimeError):
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, GooglePlacesError)),
    reraise=True,
)
def _post(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    with httpx.Client(timeout=30.0) as http:
        r = http.post(PLACES_SEARCH_URL, headers=headers, json=payload)
        if r.status_code >= 500:
            raise GooglePlacesError(f"upstream {r.status_code}: {r.text[:200]}")
        if r.status_code == 429:
            raise GooglePlacesError("rate limited")
        if r.status_code >= 400:
            # 4xx other than 429 are not retried — caller sees the error.
            raise RuntimeError(f"google places {r.status_code}: {r.text[:500]}")
        return r.json()


def search_places(
    *,
    text_query: str,
    max_results: int,
    region_code: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield raw Place dicts from Google Places Text Search.

    Paginates with nextPageToken up to `max_results` items. The API returns at
    most 20 per page and ~60 total — we cap there.
    """
    settings = get_settings()
    if not settings.google_places_api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set")

    api_key = settings.google_places_api_key
    yielded = 0
    page_token: str | None = None

    while yielded < max_results:
        page_size = min(20, max_results - yielded)
        payload: dict[str, Any] = {
            "textQuery": text_query,
            "maxResultCount": page_size,
        }
        if region_code:
            payload["regionCode"] = region_code
        if page_token:
            payload["pageToken"] = page_token
            # The API requires a short delay before the page token becomes valid.
            time.sleep(2)

        data = _post(payload, api_key)
        places = data.get("places") or []
        for place in places:
            yield place
            yielded += 1
            if yielded >= max_results:
                return

        page_token = data.get("nextPageToken")
        if not page_token:
            return


def map_place_to_prospect(place: dict[str, Any], *, industry: str, location: str) -> dict[str, Any]:
    """Convert a raw Google Place into a prospects-table row."""
    name = (place.get("displayName") or {}).get("text") or "Unknown business"
    address = place.get("formattedAddress")
    phone = place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber")
    website = place.get("websiteUri")
    rating = place.get("rating")
    review_count = place.get("userRatingCount")

    return {
        "business_name": name,
        "industry": industry,
        "location": address or location,
        "source": "google_places",
        "source_ref": place.get("id"),
        "website": website,
        "phone": phone,
        "google_rating": rating,
        "google_reviews": review_count,
        "raw": place,
    }
