"""Daily pipeline: crawl QuintoAndar, upsert listings, track price + status.

Supports two datasets via --kind:
  sale  -> businessContext SALE, headline price = salePrice
  rent  -> businessContext RENT, headline price = rent (monthly)

Run:  python -m quintoandar.pipeline --kind sale
      python -m quintoandar.pipeline --kind rent
"""
import argparse
import logging
import sys
from datetime import datetime, timezone

from . import db
from . import notifier
from .textnorm import norm_name
from .scraper import Scraper, SP_VIEWPORT, cover_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOG = logging.getLogger("quintoandar.pipeline")

# Safety: refuse to sweep listings to off_market if the crawl saw suspiciously
# few of the currently-active ones (partial/blocked run).
OFF_MARKET_MIN_COVERAGE = 0.5

PET_CODE = "PODE_TER_ANIMAIS_DE_ESTIMACAO"

KINDS = {
    "sale": {"business_context": "SALE", "price_field": "salePrice"},
    "rent": {"business_context": "RENT", "price_field": "rent"},
}


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _bool(v):
    if isinstance(v, bool):
        return v
    if v in (1, "1", "true", "True"):
        return True
    if v in (0, "0", "false", "False"):
        return False
    return None


def normalize(src, kind):
    area = _num(src.get("area"))
    price = _num(src.get(KINDS[kind]["price_field"]))
    price_m2 = round(price / area, 2) if area and price else None
    loc = src.get("location") or {}
    feats = (src.get("amenities") or []) + (src.get("installations") or [])
    return {
        "id": int(src["id"]),
        "kind": kind,
        "link": f"https://www.quintoandar.com.br/imovel/{src['id']}",
        "type": src.get("type"),
        "description": src.get("shortSaleDescription"),
        "address": src.get("address"),
        "neighborhood": src.get("neighbourhood"),
        "neighborhood_norm": norm_name(src.get("neighbourhood")),
        "region": src.get("regionName"),
        "region_norm": norm_name(src.get("regionName")),
        "city": src.get("city"),
        "lat": _num(loc.get("lat")),
        "lng": _num(loc.get("lon")),
        "area": area,
        "bedrooms": _int(src.get("bedrooms")),
        "bathrooms": _int(src.get("bathrooms")),
        "suites": _int(src.get("suites")),
        "parking": _int(src.get("parkingSpaces")),
        "condominium": _num(src.get("iptuPlusCondominium")),
        "price": price,
        "price_m2": price_m2,
        "total_cost": _num(src.get("totalCost")),
        "rent": _num(src.get("rent")),
        "yield": _num(src.get("yield")),
        "is_furnished": _bool(src.get("isFurnished")),
        "is_primary_market": _bool(src.get("isPrimaryMarket")),
        "for_rent": _bool(src.get("forRent")),
        "for_sale": _bool(src.get("forSale")),
        "accepts_pets": PET_CODE in feats,
        "amenities": ", ".join(src.get("amenities") or []),
        "installations": ", ".join(src.get("installations") or []),
        "cover_image": cover_url(src),
    }


UPSERT_COLS = [
    "id", "kind", "link", "type", "description", "address", "neighborhood",
    "neighborhood_norm", "region", "region_norm", "city", "lat", "lng", "area",
    "bedrooms", "bathrooms", "suites", "parking", "condominium", "price", "price_m2",
    "total_cost", "rent", "yield", "is_furnished", "is_primary_market", "for_rent",
    "for_sale", "accepts_pets", "amenities", "installations", "cover_image",
]
# columns updated on an existing row (everything except id + kind)
SET_COLS = UPSERT_COLS[2:]


def run(kind="sale", viewport=None, delay=0.25, dry_run=False):
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    db.init_schema()
    scraper = Scraper(business_context=KINDS[kind]["business_context"], delay=delay)
    now = datetime.now(timezone.utc)

    with db.connect() as conn:
        run_id = conn.execute(
            "INSERT INTO scrape_runs (started_at, status, kind) "
            "VALUES (%s, 'running', %s) RETURNING id", (now, kind)).fetchone()[0]
        conn.commit()

    stats = dict(kind=kind, new=0, price_changes=0, reactivated=0, off_market=0, seen=0)
    try:
        listings = scraper.crawl(viewport or SP_VIEWPORT)
        stats["seen"] = len(listings)
        if dry_run:
            LOG.info("dry-run [%s]: %s listings crawled, not writing", kind, len(listings))
            return stats

        with db.connect() as conn:
            active_before = conn.execute(
                "SELECT count(*) FROM listings WHERE status='active' AND kind=%s",
                (kind,)).fetchone()[0]

            for src in listings.values():
                _upsert(conn, normalize(src, kind), now, stats)
            conn.commit()

            coverage_ok = (active_before == 0
                           or stats["seen"] >= active_before * OFF_MARKET_MIN_COVERAGE)
            if coverage_ok:
                rows = conn.execute(
                    "SELECT id FROM listings WHERE status='active' AND kind=%s "
                    "AND last_seen < %s", (kind, now)).fetchall()
                for (lid,) in rows:
                    conn.execute(
                        "UPDATE listings SET status='off_market', removed_at=%s, "
                        "updated_at=%s WHERE id=%s AND kind=%s", (now, now, lid, kind))
                    conn.execute(
                        "INSERT INTO status_history (listing_id, kind, status, changed_at) "
                        "VALUES (%s, %s, 'off_market', %s)", (lid, kind, now))
                    stats["off_market"] += 1
            else:
                LOG.warning("SKIPPING off_market sweep [%s]: saw %s of %s active "
                            "(<%.0f%%); likely partial crawl", kind, stats["seen"],
                            active_before, OFF_MARKET_MIN_COVERAGE * 100)
            conn.commit()

            conn.execute(
                "UPDATE scrape_runs SET finished_at=%s, cells=%s, requests=%s, "
                "seen=%s, new_listings=%s, price_changes=%s, reactivated=%s, "
                "off_market=%s, status='ok' WHERE id=%s",
                (datetime.now(timezone.utc), scraper.cells_visited,
                 scraper.requests_made, stats["seen"], stats["new"],
                 stats["price_changes"], stats["reactivated"], stats["off_market"],
                 run_id))
            conn.commit()

        # alert favorited listings whose price improved
        try:
            stats["notified"] = notifier.check_and_notify(kind)
        except Exception:  # noqa: BLE001  never fail the run on a notify error
            LOG.exception("notification step failed (non-fatal)")

        LOG.info("pipeline ok: %s", stats)
        return stats
    except Exception as exc:  # noqa: BLE001
        LOG.exception("pipeline failed")
        with db.connect() as conn:
            conn.execute(
                "UPDATE scrape_runs SET finished_at=%s, status='error', error=%s, "
                "requests=%s, cells=%s WHERE id=%s",
                (datetime.now(timezone.utc), str(exc)[:1000],
                 scraper.requests_made, scraper.cells_visited, run_id))
            conn.commit()
        raise


def _upsert(conn, row, now, stats):
    kind = row["kind"]
    existing = conn.execute(
        "SELECT price, status FROM listings WHERE id=%s AND kind=%s",
        (row["id"], kind)).fetchone()

    if existing is None:
        cols = UPSERT_COLS + ["status", "first_seen", "last_seen", "updated_at"]
        vals = [row[c] for c in UPSERT_COLS] + ["active", now, now, now]
        placeholders = ", ".join(["%s"] * len(cols))
        conn.execute(
            f'INSERT INTO listings ({", ".join(chr(34)+c+chr(34) for c in cols)}) '
            f"VALUES ({placeholders})", vals)
        if row["price"] is not None:
            conn.execute(
                "INSERT INTO price_history (listing_id, kind, price, price_m2, observed_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (row["id"], kind, row["price"], row["price_m2"], now))
        conn.execute(
            "INSERT INTO status_history (listing_id, kind, status, changed_at) "
            "VALUES (%s, %s, 'active', %s)", (row["id"], kind, now))
        stats["new"] += 1
        return

    old_price, old_status = existing
    assignments = ", ".join(f'"{c}"=%s' for c in SET_COLS)
    vals = [row[c] for c in SET_COLS]

    reactivated = old_status != "active"
    price_changed = (row["price"] is not None and old_price is not None
                     and float(row["price"]) != float(old_price))

    extra = ", last_seen=%s, updated_at=%s, status='active'"
    vals_extra = [now, now]
    if reactivated:
        extra += ", removed_at=NULL"
        stats["reactivated"] += 1
    if price_changed:
        extra += ", price_changed_at=%s"
        vals_extra.append(now)

    conn.execute(
        f"UPDATE listings SET {assignments}{extra} WHERE id=%s AND kind=%s",
        vals + vals_extra + [row["id"], kind])

    if price_changed:
        conn.execute(
            "INSERT INTO price_history (listing_id, kind, price, price_m2, observed_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (row["id"], kind, row["price"], row["price_m2"], now))
        stats["price_changes"] += 1
    if reactivated:
        conn.execute(
            "INSERT INTO status_history (listing_id, kind, status, changed_at) "
            "VALUES (%s, %s, 'active', %s)", (row["id"], kind, now))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=list(KINDS), default="sale")
    ap.add_argument("--dry-run", action="store_true", help="crawl only, no writes")
    ap.add_argument("--delay", type=float, default=0.25, help="seconds between requests")
    args = ap.parse_args()
    run(kind=args.kind, delay=args.delay, dry_run=args.dry_run)
