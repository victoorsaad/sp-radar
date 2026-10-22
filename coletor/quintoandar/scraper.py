"""QuintoAndar search scraper.

The public search endpoint caps pagination at offset+pageSize <= 1000 and ignores
price/room filters, but it DOES honour the geographic viewport. So to cover more
than 1000 listings we subdivide the viewport into a quadtree: any cell whose total
exceeds the cap is split into 4 sub-cells and recursed. Leaf cells (total <= cap)
are fully paginated.
"""
import logging
import time

import requests

LOG = logging.getLogger("quintoandar.scraper")

URL = "https://apigw.prod.quintoandar.com.br/house-listing-search/v2/search/list"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.quintoandar.com.br",
    "Referer": "https://www.quintoandar.com.br/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
}

# API constraints discovered empirically
MAX_WINDOW = 1000     # offset + pageSize must stay <= this
PAGE_SIZE = 200       # max accepted page size
SPLIT_THRESHOLD = 950 # if a cell has more than this, subdivide instead of paginating
MIN_SPAN = 0.0015     # ~150m: stop subdividing below this (accept truncation)

FIELDS = [
    "id", "salePrice", "iptuPlusCondominium", "area", "bedrooms", "parkingSpaces",
    "bathrooms", "suites", "type", "neighbourhood", "shortSaleDescription",
    "address", "regionName", "city", "forRent", "forSale", "isPrimaryMarket",
    "isFurnished", "yield", "amenities", "installations", "totalCost", "rent",
    "coverImage", "location",
]

# Full São Paulo bounding box (matches the original AppScript viewport)
SP_VIEWPORT = {
    "north": -23.360287873819562,
    "south": -23.74047723241143,
    "east": -46.51743757055663,
    "west": -46.74918042944335,
}


class Scraper:
    def __init__(self, business_context="SALE", slug="sao-paulo-sp-brasil",
                 delay=0.25, max_retries=4):
        self.business_context = business_context
        self.slug = slug
        self.delay = delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.requests_made = 0
        self.cells_visited = 0

    def _payload(self, viewport, offset, page_size, fields):
        lat = (viewport["north"] + viewport["south"]) / 2
        lng = (viewport["east"] + viewport["west"]) / 2
        return {
            "context": {"mapShowing": True, "listShowing": True,
                        "numPhotos": 1, "isSSR": False},
            "filters": {
                "businessContext": self.business_context,
                "location": {
                    "coordinate": {"lat": lat, "lng": lng},
                    "viewport": viewport,
                    "countryCode": "BR",
                },
            },
            "sorting": {"criteria": "MOST_RECENT", "order": "DESC"},
            "pagination": {"pageSize": page_size, "offset": offset},
            "slug": self.slug,
            "fields": fields,
            "locationDescriptions": [{"description": self.slug}],
        }

    def _post(self, viewport, offset, page_size, fields):
        body = self._payload(viewport, offset, page_size, fields)
        for attempt in range(self.max_retries):
            try:
                r = self.session.post(URL, json=body, timeout=40)
                self.requests_made += 1
                if r.status_code == 200:
                    if self.delay:
                        time.sleep(self.delay)
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = 2 ** attempt
                    LOG.warning("HTTP %s, retry in %ss", r.status_code, wait)
                    time.sleep(wait)
                    continue
                # 400 = bad window etc.; not retryable
                LOG.error("HTTP %s: %s", r.status_code, r.text[:200])
                return None
            except requests.RequestException as exc:
                wait = 2 ** attempt
                LOG.warning("request error %s, retry in %ss", exc, wait)
                time.sleep(wait)
        return None

    def _total(self, viewport):
        data = self._post(viewport, 0, 1, ["id"])
        if not data:
            return None, []
        hits = data.get("hits", {})
        total = hits.get("total", {}).get("value", 0)
        return total, hits.get("hits", [])

    def _paginate_cell(self, viewport, sink):
        """Fetch every reachable listing in a leaf cell into sink (dict by id)."""
        self.cells_visited += 1
        offset = 0
        while offset < MAX_WINDOW:
            page_size = min(PAGE_SIZE, MAX_WINDOW - offset)
            data = self._post(viewport, offset, page_size, FIELDS)
            if not data:
                break
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                break
            for h in hits:
                src = h.get("_source", {})
                src.setdefault("orderedImageList", h.get("orderedImageList"))
                sink[src["id"]] = src
            if len(hits) < page_size:
                break
            offset += page_size

    @staticmethod
    def _quadrants(vp):
        midlat = (vp["north"] + vp["south"]) / 2
        midlng = (vp["east"] + vp["west"]) / 2
        return [
            {"north": vp["north"], "south": midlat, "west": vp["west"], "east": midlng},
            {"north": vp["north"], "south": midlat, "west": midlng, "east": vp["east"]},
            {"north": midlat, "south": vp["south"], "west": vp["west"], "east": midlng},
            {"north": midlat, "south": vp["south"], "west": midlng, "east": vp["east"]},
        ]

    def _crawl(self, viewport, sink, depth=0):
        total, _ = self._total(viewport)
        if total is None:
            LOG.warning("could not fetch total at depth %s, skipping cell", depth)
            return
        if total == 0:
            return
        span = min(viewport["north"] - viewport["south"],
                   viewport["east"] - viewport["west"])
        if total <= SPLIT_THRESHOLD or span <= MIN_SPAN:
            if total > SPLIT_THRESHOLD:
                LOG.warning("cell at min span still has %s listings; "
                            "capturing first %s", total, MAX_WINDOW)
            self._paginate_cell(viewport, sink)
            LOG.info("leaf depth=%s total=%s collected=%s reqs=%s",
                     depth, total, len(sink), self.requests_made)
            return
        for quad in self._quadrants(viewport):
            self._crawl(quad, sink, depth + 1)

    def crawl(self, viewport=None):
        """Return {id: _source} for every listing reachable in the viewport."""
        viewport = viewport or SP_VIEWPORT
        sink = {}
        self._crawl(viewport, sink)
        LOG.info("crawl done: %s listings, %s cells, %s requests",
                 len(sink), self.cells_visited, self.requests_made)
        return sink


IMG_BASE = "https://www.quintoandar.com.br/img/med/"


def cover_url(src):
    ci = src.get("coverImage")
    if ci:
        return IMG_BASE + ci
    oil = src.get("orderedImageList") or []
    if oil and isinstance(oil, list) and oil[0].get("name"):
        return IMG_BASE + oil[0]["name"]
    return None
