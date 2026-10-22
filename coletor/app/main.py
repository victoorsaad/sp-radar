"""FastAPI dashboard for the QuintoAndar SP dataset (sale + rent)."""
import os
import sys
import threading
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quintoandar import db, enrich, filtros  # noqa: E402
from quintoandar.textnorm import norm_name  # noqa: E402

app = FastAPI(title="Imóveis SP · QuintoAndar")
TEMPLATES = Path(__file__).resolve().parent / "templates"

# R$/m² histogram upper bound differs a lot between buying and renting
DIST_MAX = {"sale": 30000, "rent": 200}


def kind_of(k):
    return "rent" if k == "rent" else "sale"


def q(sql, params=None, one=False):
    with psycopg.connect(db.CONNINFO, row_factory=dict_row) as conn:
        cur = conn.execute(sql, params or ())
        return cur.fetchone() if one else cur.fetchall()


@app.get("/", response_class=HTMLResponse)
def index():
    return (TEMPLATES / "dashboard.html").read_text()


@app.get("/api/stats")
def stats(kind: str = "sale"):
    kind = kind_of(kind)
    s = q("""
        SELECT
          count(*) FILTER (WHERE status='active')                        AS active,
          count(*) FILTER (WHERE status='off_market')                    AS off_market,
          count(*)                                                       AS total,
          count(*) FILTER (WHERE status='active' AND first_seen > now()-interval '7 days') AS new_7d,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY price_m2)
              FILTER (WHERE status='active' AND price_m2>0)              AS median_m2,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY price)
              FILTER (WHERE status='active' AND price>0)                 AS median_price,
          avg(price_m2) FILTER (WHERE status='active' AND price_m2>0)    AS avg_m2
        FROM listings WHERE kind=%s
    """, (kind,), one=True)
    drops = q("""
        SELECT count(DISTINCT ph.listing_id) AS n
        FROM price_history ph
        WHERE ph.kind=%s AND ph.observed_at > now() - interval '30 days'
          AND ph.price < (SELECT price FROM price_history p2
                          WHERE p2.listing_id=ph.listing_id AND p2.kind=ph.kind
                            AND p2.observed_at < ph.observed_at
                          ORDER BY p2.observed_at DESC LIMIT 1)
    """, (kind,), one=True)
    last = q("SELECT started_at, finished_at, seen, new_listings, price_changes, "
             "off_market, status FROM scrape_runs WHERE kind=%s "
             "ORDER BY id DESC LIMIT 1", (kind,), one=True)
    s["price_drops_30d"] = drops["n"] if drops else 0
    s["last_run"] = last
    return JSONResponse(clean(s))


@app.get("/api/neighborhoods")
def neighborhoods(kind: str = "sale", limit: int = 25):
    rows = q("""
        SELECT neighborhood,
               count(*)                              AS n,
               round(avg(price_m2))                  AS avg_m2,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_m2)) AS median_m2,
               round(avg(price))                     AS avg_price
        FROM listings
        WHERE kind=%s AND status='active' AND price_m2>0 AND neighborhood IS NOT NULL
        GROUP BY neighborhood HAVING count(*) >= 5
        ORDER BY n DESC LIMIT %s
    """, (kind_of(kind), limit))
    return JSONResponse([clean(r) for r in rows])


@app.get("/api/price-distribution")
def price_distribution(kind: str = "sale"):
    kind = kind_of(kind)
    top = DIST_MAX[kind]
    rows = q("""
        WITH b AS (
          SELECT width_bucket(price_m2, 0, %s, 30) AS bucket
          FROM listings WHERE kind=%s AND status='active' AND price_m2 BETWEEN 1 AND %s
        )
        SELECT bucket, count(*) AS n FROM b GROUP BY bucket ORDER BY bucket
    """, (top, kind, top))
    step = top / 30
    return JSONResponse([{"bucket": r["bucket"], "label": round(r["bucket"] * step),
                          "n": r["n"]} for r in rows])


@app.get("/api/price-drops")
def price_drops(kind: str = "sale", limit: int = 80):
    rows = q("""
        SELECT l.id, l.link, l.neighborhood, l.region, l.address, l.area, l.bedrooms,
               l.price AS current_price, l.cover_image, l.price_changed_at,
               (SELECT price FROM price_history p
                 WHERE p.listing_id=l.id AND p.kind=l.kind
                 ORDER BY observed_at ASC LIMIT 1) AS first_price
        FROM listings l
        WHERE l.kind=%s AND l.status='active' AND l.price_changed_at IS NOT NULL
        ORDER BY l.price_changed_at DESC LIMIT %s
    """, (kind_of(kind), limit))
    out = []
    for r in rows:
        fp, cp = r["first_price"], r["current_price"]
        r["pct_change"] = round((float(cp) - float(fp)) / float(fp) * 100, 1) if fp else None
        out.append(clean(r))
    return JSONResponse(out)


class Filtros:
    """Filtros comuns à listagem e ao mapa (mesmos query params); SQL em quintoandar/filtros.py."""
    def __init__(
        self,
        kind: str = "sale",
        status: str = "active",
        region: str | None = None,
        neighborhood: str | None = None,   # comma-separated normalized names (multi)
        bedrooms: int | None = None,
        bathrooms: int | None = None,
        parking: int | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_total: float | None = None,
        max_total: float | None = None,
        max_m2: float | None = None,
        furnished: bool | None = None,
        pets: bool | None = None,
        show_hidden: bool = False,
        below: bool = False,
    ):
        self.kind = kind_of(kind)
        self.clause, self.params = filtros.build(
            self.kind, status, region, neighborhood, bedrooms, bathrooms, parking, min_price,
            max_price, min_total, max_total, max_m2, furnished, pets, show_hidden, below)


@app.get("/api/map")
def map_points(f: Filtros = Depends(), limit: int = Query(3000, le=8000)):
    top = 60000 if f.kind == "sale" else 400
    rows = q(f"""
        SELECT id, lat, lng, price, price_m2, total_cost, neighborhood, bedrooms, area,
               cover_image
        FROM listings
        WHERE {f.clause} AND lat IS NOT NULL AND lng IS NOT NULL
              AND price_m2 BETWEEN 1 AND %s
        ORDER BY hashtext(id::text) LIMIT %s   -- amostra espalhada (fixa) quando passa do limite
    """, f.params + [top, limit])
    return JSONResponse([clean(r) for r in rows])


@app.get("/api/listings")
def listings(
    f: Filtros = Depends(),
    sort: str = "last_seen",
    order: str = "desc",
    page: int = 1,
    page_size: int = Query(50, le=200),
):
    allowed = {"price", "price_m2", "area", "last_seen", "first_seen", "bedrooms",
               "total_cost"}
    sort = sort if sort in allowed else "last_seen"
    order = "ASC" if order.lower() == "asc" else "DESC"
    clause, params = f.clause, f.params

    total = q(f"SELECT count(*) AS n FROM listings WHERE {clause}", params, one=True)["n"]
    params2 = params + [page_size, (page - 1) * page_size]
    rows = q(f"""
        SELECT l.id, l.kind, l.link, l.type, l.description, l.address, l.neighborhood, l.region,
               l.area, l.lat, l.lng, l.bedrooms, l.bathrooms, l.suites, l.parking, l.condominium,
               l.price, l.price_m2, l.total_cost, l.rent, l.yield, l.is_furnished, l.accepts_pets,
               l.cover_image, l.status, l.first_seen, l.last_seen, l.price_changed_at,
               e.price_state, e.suggested_price, e.first_published_at
        FROM (SELECT * FROM listings WHERE {clause}
              ORDER BY {sort} {order} NULLS LAST LIMIT %s OFFSET %s) l
        LEFT JOIN enrichment e ON e.listing_id = l.id AND e.kind = l.kind
        ORDER BY l.{sort} {order} NULLS LAST
    """, params2)
    return JSONResponse({"total": total, "page": page, "page_size": page_size,
                         "rows": [clean(r) for r in rows]})


@app.get("/api/listing/{lid}")
def listing_detail(lid: int, kind: str = "sale"):
    kind = kind_of(kind)
    row = q("SELECT * FROM listings WHERE id=%s AND kind=%s", (lid, kind), one=True)
    hist = q("SELECT price, price_m2, observed_at FROM price_history "
             "WHERE listing_id=%s AND kind=%s ORDER BY observed_at", (lid, kind))
    return JSONResponse({"listing": clean(row) if row else None,
                         "history": [clean(h) for h in hist]})


_ENRICH_LOCK = threading.Lock()


def _enrichment(lid, kind):
    row = q("SELECT * FROM enrichment WHERE listing_id=%s AND kind=%s", (lid, kind), one=True)
    if not row:
        return None
    out = clean(row)
    for k in ("photos", "features", "similares"):
        out[k] = row[k]
    return out


@app.get("/api/enrichment/{kind}/{lid}")
def get_enrichment(kind: str, lid: int):
    return JSONResponse(_enrichment(lid, kind_of(kind)))


@app.post("/api/enrich/{kind}/{lid}")
def do_enrich(kind: str, lid: int):
    """Busca ficha + avaliação agora (6–10 s). Uma de cada vez para não martelar o site."""
    kind = kind_of(kind)
    if not _ENRICH_LOCK.acquire(timeout=60):
        return JSONResponse({"error": "ocupado, tente de novo"}, status_code=429)
    try:
        with psycopg.connect(db.CONNINFO) as conn:
            enrich.enrich_one(conn, lid, kind)
    finally:
        _ENRICH_LOCK.release()
    return JSONResponse(_enrichment(lid, kind))


@app.get("/api/neighborhood-list")
def neighborhood_list(kind: str = "sale"):
    # group by normalized name; label = most common raw spelling (usually accented)
    rows = q("""
        SELECT neighborhood_norm AS value,
               mode() WITHIN GROUP (ORDER BY neighborhood) AS label,
               count(*) AS n
        FROM listings
        WHERE kind=%s AND status='active' AND neighborhood_norm IS NOT NULL
        GROUP BY neighborhood_norm
        ORDER BY label
    """, (kind_of(kind),))
    return JSONResponse([clean(r) for r in rows])


@app.get("/api/region-list")
def region_list(kind: str = "sale"):
    rows = q("""
        SELECT region_norm AS value,
               mode() WITHIN GROUP (ORDER BY region) AS label,
               count(*) AS n
        FROM listings
        WHERE kind=%s AND status='active' AND region_norm IS NOT NULL
        GROUP BY region_norm
        ORDER BY n DESC
    """, (kind_of(kind),))
    return JSONResponse([clean(r) for r in rows])


class FavIn(BaseModel):
    id: int
    kind: str = "sale"
    note: str | None = None
    target_price: float | None = None


@app.get("/api/favorite-ids")
def favorite_ids(kind: str = "sale"):
    rows = q("SELECT listing_id FROM favorites WHERE kind=%s", (kind_of(kind),))
    return JSONResponse([r["listing_id"] for r in rows])


@app.get("/api/favorites")
def favorites(kind: str = "sale"):
    rows = q("""
        SELECT f.listing_id AS id, f.kind, f.note, f.target_price, f.price_at_fav,
               f.created_at, l.price AS current_price, l.total_cost, l.neighborhood,
               l.region, l.address, l.area, l.bedrooms, l.bathrooms, l.parking,
               l.accepts_pets, l.is_furnished, l.cover_image, l.link, l.status,
               l.price_changed_at, e.price_state, e.suggested_price, e.first_published_at
        FROM favorites f
        JOIN listings l ON l.id = f.listing_id AND l.kind = f.kind
        LEFT JOIN enrichment e ON e.listing_id = f.listing_id AND e.kind = f.kind
        WHERE f.kind = %s
        ORDER BY f.created_at DESC
    """, (kind_of(kind),))
    out = []
    for r in rows:
        base, cur = r["price_at_fav"], r["current_price"]
        r["delta_pct"] = (round((float(cur) - float(base)) / float(base) * 100, 1)
                          if base and cur else None)
        out.append(clean(r))
    return JSONResponse(out)


@app.post("/api/favorites")
def add_favorite(fav: FavIn):
    kind = kind_of(fav.kind)
    with psycopg.connect(db.CONNINFO) as conn:
        cur = conn.execute("SELECT price FROM listings WHERE id=%s AND kind=%s",
                           (fav.id, kind)).fetchone()
        if not cur:
            return JSONResponse({"error": "listing not found"}, status_code=404)
        conn.execute("""
            INSERT INTO favorites (listing_id, kind, note, target_price, price_at_fav)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (listing_id, kind) DO UPDATE
              SET note = EXCLUDED.note, target_price = EXCLUDED.target_price
        """, (fav.id, kind, fav.note, fav.target_price, cur[0]))
        conn.commit()
    return JSONResponse({"ok": True})


@app.delete("/api/favorites/{kind}/{lid}")
def remove_favorite(kind: str, lid: int):
    with psycopg.connect(db.CONNINFO) as conn:
        conn.execute("DELETE FROM favorites WHERE listing_id=%s AND kind=%s",
                     (lid, kind_of(kind)))
        conn.commit()
    return JSONResponse({"ok": True})


# ---- hidden / disliked ----
class HideIn(BaseModel):
    id: int
    kind: str = "sale"


@app.get("/api/hidden-ids")
def hidden_ids(kind: str = "sale"):
    rows = q("SELECT listing_id FROM hidden WHERE kind=%s", (kind_of(kind),))
    return JSONResponse([r["listing_id"] for r in rows])


@app.post("/api/hidden")
def add_hidden(h: HideIn):
    with psycopg.connect(db.CONNINFO) as conn:
        conn.execute("INSERT INTO hidden (listing_id, kind) VALUES (%s, %s) "
                     "ON CONFLICT DO NOTHING", (h.id, kind_of(h.kind)))
        conn.commit()
    return JSONResponse({"ok": True})


@app.delete("/api/hidden/{kind}/{lid}")
def remove_hidden(kind: str, lid: int):
    with psycopg.connect(db.CONNINFO) as conn:
        conn.execute("DELETE FROM hidden WHERE listing_id=%s AND kind=%s",
                     (lid, kind_of(kind)))
        conn.commit()
    return JSONResponse({"ok": True})


# ---- saved searches ----
class SearchIn(BaseModel):
    name: str
    kind: str = "sale"
    params: dict


@app.get("/api/searches")
def list_searches(kind: str = "sale"):
    rows = q("SELECT id, name, kind, params, created_at FROM saved_searches "
             "WHERE kind=%s ORDER BY created_at DESC", (kind_of(kind),))
    return JSONResponse([clean(r) for r in rows])


@app.post("/api/searches")
def save_search(s: SearchIn):
    import json
    with psycopg.connect(db.CONNINFO) as conn:
        row = conn.execute(
            "INSERT INTO saved_searches (name, kind, params) VALUES (%s, %s, %s::jsonb) "
            "RETURNING id", (s.name, kind_of(s.kind), json.dumps(s.params))).fetchone()
        conn.commit()
    return JSONResponse({"ok": True, "id": row[0]})


@app.delete("/api/searches/{sid}")
def delete_search(sid: int):
    with psycopg.connect(db.CONNINFO) as conn:
        conn.execute("DELETE FROM saved_searches WHERE id=%s", (sid,))
        conn.commit()
    return JSONResponse({"ok": True})


def clean(d):
    """JSON-safe: dates -> isoformat, Decimal -> float, nested dicts recursed."""
    if not d:
        return d
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, dict):
            out[k] = clean(v)
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DASHBOARD_PORT", "8095"))
    # Sem autenticação: por padrão só escuta localhost. Exponha atrás de um proxy com login.
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"), port=port)
