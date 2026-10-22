"""One-time seed of historical data from the legacy Google Sheet export (.xlsx).

The sheet 'DB Imoveis' has these columns (0-indexed):
  0 link  1 description  2 address  3 neighborhood  4 area  5 bedrooms
  6 garage  7 condominio  8 price  9 preco_m2  10 collectedAt  11 city
  12 forRent 13 forSale 14 isPrimaryMarket 15 isFurnished 16 yield
  17 amenities 18 installations 19 totalCost 20 rent 21 colectedAt 22 newPrice

Dates were exported as Excel serials. Each row is one price observation; we group
by listing id (parsed from the link) to reconstruct a price history and seed the
current snapshot. Idempotent: skips listings that already exist.

Run:  python -m quintoandar.importer /path/to/file.xlsx
"""
import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone

from openpyxl import load_workbook

from . import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
LOG = logging.getLogger("quintoandar.importer")

EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
FALLBACK_DT = datetime(2025, 1, 1, tzinfo=timezone.utc)
ID_RE = re.compile(r"/imovel/(\d+)")


def to_dt(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if 20000 < f < 60000:  # plausible Excel serial (year ~1954-2064)
            return EXCEL_EPOCH + timedelta(days=f)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def as_int(v):
    n = num(v)
    return int(n) if n is not None else None


def as_bool(v):
    if v in (1, "1", 1.0, "true", "True", True):
        return True
    if v in (0, "0", 0.0, "false", "False", False):
        return False
    return None


def load_rows(path):
    """Yield dict per data row, grouped later by id."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["DB Imoveis"]
    rows = ws.iter_rows(values_only=True)
    next(rows, None)  # header
    for r in rows:
        if not r or not r[0]:
            continue
        m = ID_RE.search(str(r[0]))
        if not m:
            continue
        yield int(m.group(1)), r


def build(path):
    """Return {id: snapshot} and {id: [(price, price_m2, observed_at), ...]}."""
    snapshots, history = {}, {}
    for lid, r in load_rows(path):
        obs = to_dt(r[21]) or to_dt(r[10]) or FALLBACK_DT
        price = num(r[8])
        area = num(r[4])
        price_m2 = num(r[9]) or (round(price / area, 2) if area and price else None)
        history.setdefault(lid, []).append((price, price_m2, obs))
        snap = {
            "id": lid,
            "link": str(r[0]),
            "type": None,
            "description": r[1] or None,
            "address": r[2] or None,
            "neighborhood": r[3] or None,
            "region": None,
            "city": r[11] or None,
            "lat": None, "lng": None,
            "area": area,
            "bedrooms": as_int(r[5]),
            "bathrooms": None,
            "suites": None,
            "parking": as_int(r[6]),
            "condominium": num(r[7]),
            "price": price,
            "price_m2": price_m2,
            "total_cost": num(r[19]),
            "rent": num(r[20]),
            "yield": num(r[16]),
            "is_furnished": as_bool(r[15]),
            "is_primary_market": as_bool(r[14]),
            "for_rent": as_bool(r[12]),
            "for_sale": as_bool(r[13]),
            "amenities": r[17] or None,
            "installations": r[18] or None,
            "cover_image": None,
            "_obs": obs,
        }
        # keep the most recent observation as the current snapshot
        cur = snapshots.get(lid)
        if cur is None or obs >= cur["_obs"]:
            snapshots[lid] = snap
    return snapshots, history


COLS = [
    "id", "link", "type", "description", "address", "neighborhood", "region",
    "city", "lat", "lng", "area", "bedrooms", "bathrooms", "suites", "parking",
    "condominium", "price", "price_m2", "total_cost", "rent", "yield",
    "is_furnished", "is_primary_market", "for_rent", "for_sale", "amenities",
    "installations", "cover_image",
]


def seed(path):
    db.init_schema()
    snapshots, history = build(path)
    LOG.info("parsed %s distinct listings, %s total observations",
             len(snapshots), sum(len(v) for v in history.values()))

    inserted = pts = 0
    with db.connect() as conn:
        existing = {r[0] for r in conn.execute("SELECT id FROM listings").fetchall()}
        for lid, snap in snapshots.items():
            if lid in existing:
                continue
            obs_list = sorted(history[lid], key=lambda x: x[2])
            first_seen = obs_list[0][2]
            last_seen = snap["_obs"]
            cols = COLS + ["status", "first_seen", "last_seen", "updated_at"]
            vals = [snap[c] for c in COLS] + ["active", first_seen, last_seen, last_seen]
            ph = ", ".join(["%s"] * len(cols))
            conn.execute(
                f'INSERT INTO listings ({", ".join(chr(34)+c+chr(34) for c in cols)}) '
                f"VALUES ({ph}) ON CONFLICT (id, kind) DO NOTHING", vals)

            # price history: one point per price change, in observed order
            last_price = None
            for price, price_m2, obs in obs_list:
                if price is None or price == last_price:
                    continue
                conn.execute(
                    "INSERT INTO price_history (listing_id, price, price_m2, observed_at) "
                    "VALUES (%s, %s, %s, %s)", (lid, price, price_m2, obs))
                last_price = price
                pts += 1
            conn.execute(
                "INSERT INTO status_history (listing_id, status, changed_at) "
                "VALUES (%s, 'active', %s)", (lid, first_seen))
            inserted += 1
            if inserted % 5000 == 0:
                conn.commit()
                LOG.info("  %s listings seeded...", inserted)
        conn.commit()
    LOG.info("seed done: %s new listings, %s price points", inserted, pts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", help="path to the legacy .xlsx export")
    args = ap.parse_args()
    seed(args.xlsx)
