"""Carbon credit registry lookup.

Detects the registry from the serial prefix (VCS/GS/ACR/CAR), fetches the
public registry page, and extracts project metadata. Falls back to
Nominatim country-centroid coordinates when the registry page does not
expose lat/lon. Always returns a structured dict — never raises.
"""
from __future__ import annotations

import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REGISTRY_URLS = {
    "VCS": "https://registry.verra.org/app/projectDetail/VCS/{id}",
    "GS": "https://registry.goldstandard.org/projects/details/{id}",
    "ACR": "https://acr2.apx.com/mymodule/reg/prjView.asp?id1={id}",
    "CAR": "https://thereserve2.apx.com/mymodule/reg/prjView.asp?id1={id}",
}

REGISTRY_NAMES = {
    "VCS": "Verra (VCS)",
    "GS": "Gold Standard",
    "ACR": "American Carbon Registry",
    "CAR": "Climate Action Reserve",
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_HEADERS = {
    "User-Agent": "VeritasOracle/1.0 (+https://veritasoracle.vercel.app)",
}

_COORD_RX = re.compile(
    r"(-?\d{1,3}\.\d+)\s*[°,]?\s*([NS])?\s*[,;]\s*(-?\d{1,3}\.\d+)\s*[°,]?\s*([EW])?"
)
_SERIAL_RX = re.compile(r"^(VCS|GS|ACR|CAR)[-_]?(\d+)$", re.IGNORECASE)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _empty_result(serial: str) -> dict:
    return {
        "found": False,
        "serial": serial,
        "registry": None,
        "project_name": None,
        "country": None,
        "lat": None,
        "lon": None,
        "hectares": None,
        "methodology": None,
        "credits_issued": None,
        "last_verification_date": None,
        "developer_name": None,
        "project_status": None,
        "coordinates_approximate": False,
        "registry_url": None,
        "error": None,
    }


def _detect_registry(serial: str) -> tuple[str | None, str | None]:
    match = _SERIAL_RX.match(serial.strip())
    if not match:
        return None, None
    return match.group(1).upper(), match.group(2)


def _label_value(soup: BeautifulSoup, label_pattern: str) -> str | None:
    rx = re.compile(label_pattern, re.I)
    for el in soup.find_all(["th", "td", "dt", "label", "strong", "span"]):
        text = (el.get_text(strip=True) or "").rstrip(":")
        if rx.fullmatch(text):
            sibling = el.find_next_sibling(["td", "dd", "span", "div"])
            if sibling and sibling.get_text(strip=True):
                return sibling.get_text(" ", strip=True)
    return None


def _extract_coords(text: str) -> tuple[float | None, float | None]:
    match = _COORD_RX.search(text)
    if not match:
        return None, None
    lat = float(match.group(1))
    lon = float(match.group(3))
    if match.group(2) and match.group(2).upper() == "S":
        lat = -abs(lat)
    if match.group(4) and match.group(4).upper() == "W":
        lon = -abs(lon)
    return lat, lon


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None


async def _geocode_country(
    client: httpx.AsyncClient, country: str
) -> tuple[float | None, float | None]:
    started = time.perf_counter()
    try:
        response = await client.get(
            NOMINATIM_URL,
            params={"q": country, "format": "json", "limit": 1},
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        data = response.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            logger.info("nominatim ok %s -> %.3f,%.3f (%dms)", country, lat, lon, _ms(started))
            return lat, lon
    except Exception as exc:  # noqa: BLE001
        logger.warning("nominatim failed for %s: %s (%dms)", country, exc, _ms(started))
    return None, None


async def lookup_serial(serial: str) -> dict:
    started = time.perf_counter()
    result = _empty_result(serial)
    registry, numeric_id = _detect_registry(serial)

    if not registry:
        result["error"] = f"Unsupported serial format: {serial}"
        logger.info("registry %s rejected (%dms)", serial, _ms(started))
        return result

    url = REGISTRY_URLS[registry].format(id=numeric_id)
    result["registry"] = REGISTRY_NAMES[registry]
    result["registry_url"] = url

    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers=DEFAULT_HEADERS
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                result["error"] = f"Registry returned HTTP {response.status_code}"
                logger.info(
                    "registry %s -> HTTP %d (%dms)",
                    serial,
                    response.status_code,
                    _ms(started),
                )
                return result

            soup = BeautifulSoup(response.text, "lxml")

            project_name = _label_value(soup, r"Project\s*(Name|Title)") or (
                soup.title.text.strip() if soup.title else None
            )
            country = _label_value(soup, r"Country|Location|Host\s*Country")
            hectares = _to_float(_label_value(soup, r"(Project\s*)?Area|Hectares"))
            methodology = _label_value(soup, r"Methodology")
            credits = _to_int(
                _label_value(soup, r"Credits\s*Issued|Total\s*VCUs|Issuance(s)?")
            )
            last_verification_date = _label_value(
                soup, r"Last\s*Verification|Verification\s*Date|Last\s*Audit"
            )
            developer_name = _label_value(
                soup, r"Developer|Project\s*Developer|Proponent|Project\s*Owner"
            )
            project_status = _label_value(soup, r"Status|Project\s*Status")

            lat, lon = _extract_coords(soup.get_text(" ", strip=True))
            coordinates_approximate = False

            if (lat is None or lon is None) and country:
                lat, lon = await _geocode_country(client, country)
                coordinates_approximate = lat is not None

            result.update(
                {
                    "found": bool(project_name or country),
                    "project_name": project_name,
                    "country": country,
                    "lat": lat,
                    "lon": lon,
                    "hectares": hectares,
                    "methodology": methodology,
                    "credits_issued": credits,
                    "last_verification_date": last_verification_date,
                    "developer_name": developer_name,
                    "project_status": project_status,
                    "coordinates_approximate": coordinates_approximate,
                }
            )
    except httpx.TimeoutException:
        result["error"] = "Registry request timed out"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Registry lookup failed: {exc}"
    finally:
        logger.info(
            "registry %s -> found=%s (%dms)", serial, result["found"], _ms(started)
        )

    return result
