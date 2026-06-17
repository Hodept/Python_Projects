import csv
import os
import sys
import uuid
import webbrowser
import requests

GRAPHQL_URL = "https://api.search-inventory.toyota.com/graphql"
DEFAULT_OUTPUT_PATH = "toyota_grand_highlander_matches.csv"

ZIP_CODE = os.environ.get("TOYOTA_ZIP_CODE", "98036")
DISTANCE_MILES = int(os.environ.get("TOYOTA_DISTANCE_MILES", "20"))
PAGE_SIZE = int(os.environ.get("TOYOTA_PAGE_SIZE", "25"))
PAGE_NUMBER = int(os.environ.get("TOYOTA_PAGE_NUMBER", "1"))
SERIES_CODE = os.environ.get("TOYOTA_SERIES_CODE", "grandhighlander")
MARKETING_SERIES = os.environ.get("TOYOTA_MARKETING_SERIES", "Grand Highlander")
YEAR = int(os.environ.get("TOYOTA_YEAR", "2026"))
EXTERIOR_COLOR_CODE = os.environ.get("TOYOTA_EXTERIOR_COLOR_CODE", "01H5")
INTERIOR_COLOR_CODE = os.environ.get("TOYOTA_INTERIOR_COLOR_CODE", "LA40")
FUEL_TYPE_CODE = os.environ.get("TOYOTA_FUEL_TYPE_CODE", "B")
REQUIRED_OPTION_CODE = os.environ.get("TOYOTA_REQUIRED_OPTION_CODE", "3C")
OPEN_BROWSER_ON_WAF = os.environ.get("TOYOTA_OPEN_BROWSER_ON_WAF", "").lower() in {
    "1",
    "true",
    "yes",
}

QUERY_TEMPLATE = """
query {
  locateVehiclesByZip(
    zipCode: "ZIPCODE",
    brand: "TOYOTA",
    pageNo: PAGENUMBER,
    pageSize: PAGESIZE,
    seriesCodes: "SERIESCODE",
    distance: DISTANCEMILES,
    leadid: "LEADIDUUID",
    interiorMedia: true
  ) {
    pagination {
      pageNo
      pageSize
      totalPages
      totalRecords
    }
    vehicleSummary {
      vin
      stockNum
      marketingSeries
      grade
      year
      dealerCd
      dealerMarketingName
      dealerWebsite
      inventoryStatus
      isPreSold
      distance
      price {
        totalMsrp
        baseMsrp
        advertizedPrice
      }
      options {
        optionCd
        marketingName
        optionType
        packageInd
      }
      intColor {
        colorCd
        marketingName
      }
      extColor {
        colorCd
        marketingName
      }
      engine {
        engineCd
        name
        fuelType
      }
      fuelType {
        code
        name
      }
      drivetrain {
        code
        title
      }
      model {
        modelCd
        marketingName
      }
    }
  }
}
"""

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://www.toyota.com",
    "referer": "https://www.toyota.com/search-inventory/model/grandhighlander/",
    "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "x-api-key": "undefined",
    "x-cache-key": "useVehicleConfig-Toyota-undefined-grandhighlander",
}


class WAFChallengeError(RuntimeError):
    """Raised when Toyota returns a browser challenge instead of API data."""


def build_query():
    return (
        QUERY_TEMPLATE
        .replace("ZIPCODE", ZIP_CODE)
        .replace("PAGENUMBER", str(PAGE_NUMBER))
        .replace("PAGESIZE", str(PAGE_SIZE))
        .replace("SERIESCODE", SERIES_CODE)
        .replace("DISTANCEMILES", str(DISTANCE_MILES))
        .replace("LEADIDUUID", str(uuid.uuid4()))
    )


def inventory_page_url():
    return f"https://www.toyota.com/search-inventory/model/{SERIES_CODE}/"


def response_snippet(response, limit=500):
    return response.text.replace("\n", " ")[:limit]


def diagnostic_headers(response):
    header_names = (
        "content-type",
        "x-amzn-waf-action",
        "x-amzn-errortype",
        "x-cache",
        "x-amz-cf-pop",
        "x-amz-cf-id",
    )
    return {
        name: response.headers[name]
        for name in header_names
        if name in response.headers
    }


def fetch_inventory():
    query = build_query()

    try:
        r = requests.post(
            GRAPHQL_URL,
            headers=HEADERS,
            json={"query": query},
            timeout=30,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Toyota inventory request could not be sent: {e}") from e

    waf_action = r.headers.get("x-amzn-waf-action")
    if waf_action:
        raise WAFChallengeError(
            "Toyota blocked the inventory request with an AWS WAF "
            f"{waf_action!r} response. Open the inventory page in a browser "
            "or use an approved Toyota inventory data source; this endpoint "
            "is currently challenging automated requests. "
            f"Response headers: {diagnostic_headers(r)}"
        )

    try:
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as e:
        raise RuntimeError(
            "Toyota inventory request failed with "
            f"HTTP {r.status_code}. Headers: {diagnostic_headers(r)}. "
            f"Response starts: {response_snippet(r)}"
        ) from e
    except requests.JSONDecodeError as e:
        raise RuntimeError(
            "Toyota inventory response was not JSON. "
            f"HTTP {r.status_code}. Headers: {diagnostic_headers(r)}. "
            f"Response starts: {response_snippet(r)}"
        ) from e

    if data.get("errors"):
        raise RuntimeError(f"Toyota GraphQL returned errors: {data['errors']}")

    return data


def option_codes(vehicle):
    return {opt.get("optionCd") for opt in vehicle.get("options", [])}


def matches_config(vehicle):
    return (
        vehicle.get("year") == YEAR
        and vehicle.get("marketingSeries") == MARKETING_SERIES
        and not vehicle.get("isPreSold")
        and (vehicle.get("extColor") or {}).get("colorCd") == EXTERIOR_COLOR_CODE
        and (vehicle.get("intColor") or {}).get("colorCd") == INTERIOR_COLOR_CODE
        and (vehicle.get("fuelType") or {}).get("code") == FUEL_TYPE_CODE
        and REQUIRED_OPTION_CODE in option_codes(vehicle)
    )


def flatten(vehicle):
    return {
        "vin": vehicle.get("vin"),
        "stock_num": vehicle.get("stockNum"),
        "year": vehicle.get("year"),
        "series": vehicle.get("marketingSeries"),
        "grade": vehicle.get("grade"),
        "model_cd": (vehicle.get("model") or {}).get("modelCd"),
        "model_name": (vehicle.get("model") or {}).get("marketingName"),
        "dealer_cd": vehicle.get("dealerCd"),
        "dealer": vehicle.get("dealerMarketingName"),
        "dealer_website": vehicle.get("dealerWebsite"),
        "distance": vehicle.get("distance"),
        "status": vehicle.get("inventoryStatus"),
        "is_presold": vehicle.get("isPreSold"),
        "msrp": (vehicle.get("price") or {}).get("totalMsrp"),
        "base_msrp": (vehicle.get("price") or {}).get("baseMsrp"),
        "advertised_price": (vehicle.get("price") or {}).get("advertizedPrice"),
        "exterior_cd": (vehicle.get("extColor") or {}).get("colorCd"),
        "exterior": (vehicle.get("extColor") or {}).get("marketingName"),
        "interior_cd": (vehicle.get("intColor") or {}).get("colorCd"),
        "interior": (vehicle.get("intColor") or {}).get("marketingName"),
        "fuel_type_cd": (vehicle.get("fuelType") or {}).get("code"),
        "fuel_type": (vehicle.get("fuelType") or {}).get("name"),
        "drivetrain_cd": (vehicle.get("drivetrain") or {}).get("code"),
        "drivetrain": (vehicle.get("drivetrain") or {}).get("title"),
        "options": "; ".join(
            f"{o.get('optionCd')}={o.get('marketingName')}"
            for o in vehicle.get("options", [])
        ),
    }


def export_csv(rows, path):
    if not rows:
        print("No matches to export.")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main():
    print(
        "Searching Toyota inventory: "
        f"zip={ZIP_CODE}, distance={DISTANCE_MILES}, page_size={PAGE_SIZE}, "
        f"page={PAGE_NUMBER}, series={SERIES_CODE}",
        flush=True,
    )

    try:
        data = fetch_inventory()
    except WAFChallengeError as e:
        print(e, file=sys.stderr)
        if OPEN_BROWSER_ON_WAF:
            url = inventory_page_url()
            print(f"Opening Toyota inventory page for manual review: {url}")
            opened = webbrowser.open(url)
            if not opened:
                print(f"Could not open a browser automatically. Visit: {url}")
        else:
            print(
                "Set TOYOTA_OPEN_BROWSER_ON_WAF=1 to open the Toyota "
                "inventory page automatically when this happens.",
                file=sys.stderr,
            )
        return 1
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    result = (data.get("data") or {}).get("locateVehiclesByZip")
    if not result or "vehicleSummary" not in result:
        print(f"Unexpected Toyota response shape: {data}", file=sys.stderr)
        return 1

    vehicles = result["vehicleSummary"]

    matches = [
        flatten(v)
        for v in vehicles
        if matches_config(v)
    ]

    export_csv(matches, DEFAULT_OUTPUT_PATH)

    print(f"Returned vehicles: {len(vehicles)}")
    print(f"Matching vehicles: {len(matches)}")

    for m in matches:
        print(
            m["vin"],
            m["dealer"],
            m["grade"],
            m["exterior"],
            m["interior"],
            m["msrp"],
            m["status"],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
