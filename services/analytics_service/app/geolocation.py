"""IP-based geolocation for a transaction's "Location" panel.

Fraud360's data model captures no lat/long (see the ingestion-time BRD gap this
partially closes) - the only location-adjacent field on a transaction is its real
``ip_addr``. This resolves that IP to an approximate lat/long via a real, free, no-API-
key-required geolocation lookup (ip-api.com's free tier - non-commercial use, ~45
req/min) rather than fabricating coordinates or a "location" the product doesn't
actually have.

This is deliberately an on-demand, per-transaction lookup, not something computed at
ingestion time and stored: doing it at ingestion would mean geocoding every transaction
whether or not anyone ever looks at it, against a free service's rate limit, for data
that already exists (the IP) and can be resolved on request in under a second. Baking
this into the ingestion pipeline instead - so location is queryable/filterable like any
other dimension - is the "decision, not just a screen" the original gap called out; this
is the read path that decision would eventually feed.
"""
import os

import requests

GEOLOCATE_URL = os.environ.get("GEOLOCATE_URL", "http://ip-api.com/json")
GEOLOCATE_TIMEOUT_S = float(os.environ.get("GEOLOCATE_TIMEOUT_S", "5"))


class GeolocateUnavailableError(RuntimeError):
    """The geolocation service could not be reached."""


def locate_ip(ip: str) -> dict:
    """Resolve one IP to an approximate location.

    Always returns a dict - callers check ``locatable`` rather than catching an
    exception for the (common, expected) case of a private/reserved IP address, which
    this product's own demo data deliberately includes and which no geolocation service
    can resolve to a real place.
    """
    try:
        resp = requests.get(
            f"{GEOLOCATE_URL}/{ip}",
            params={"fields": "status,message,country,regionName,city,lat,lon,isp,query"},
            timeout=GEOLOCATE_TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise GeolocateUnavailableError(f"Could not reach the geolocation service: {exc}") from exc

    body = resp.json()
    if body.get("status") != "success":
        return {"ip": ip, "locatable": False, "reason": body.get("message", "not locatable")}

    return {
        "ip": ip,
        "locatable": True,
        "lat": body["lat"],
        "lon": body["lon"],
        "city": body.get("city", ""),
        "region": body.get("regionName", ""),
        "country": body.get("country", ""),
        "isp": body.get("isp", ""),
    }
