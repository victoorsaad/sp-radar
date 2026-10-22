"""Enriquecimento sob demanda: ficha completa, avaliação e similares do QuintoAndar.

Fontes (públicas, sem login; mapeadas por engenharia reversa do site em 17/10/2026):
  - página do imóvel: JSON do Next.js em <script id="__NEXT_DATA__"> -> houseInfo
    (andar, portaria, sol, metrô, condomínio, 1ª publicação, contraproposta, visitas, fotos)
  - POST apigw .../pricing-reports/v1/price-suggestion -> faixa de preço justo + BELOW/WELL/ABOVE_PRICED
  - POST apigw .../pricing-reports/v1/similar-houses   -> similares no ar e que saíram do ar + resumo

Só leitura e em ritmo baixo (DELAY entre chamadas). Nunca enriquece a base inteira:
apenas favoritos, imóveis que batem com pesquisas salvas e pedidos do dashboard.

Uso:
  python -m quintoandar.enrich <kind> <id> [<id> ...]
  python -m quintoandar.enrich --favorites [--stale-days 7]
"""
import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone

import requests

from . import db

LOG = logging.getLogger("quintoandar.enrich")
DELAY = 2.0
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
API = "https://apigw.prod.quintoandar.com.br/customer-facing-bff-api/pricing-reports/v1/"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

TYPES = {"Apartamento": "APARTMENT", "Casa": "HOUSE", "Casa em condomínio": "CONDOMINIUM_HOUSE",
         "Studio e kitnet": "STUDIO"}
# chaves das comodidades da ficha -> campos booleanos que o site manda para a avaliação
FLAGS = {
    "BANHEIRA_DE_HIDROMASSAGEM": "has_bathtub", "BOX": "has_glass_shower_stall",
    "VARANDA": "has_balcony", "PISCINA_PRIVATIVA": "has_pool",
    "ARMARIOS_EMBUTIDOS_NO_QUARTO": "has_bedroom_cabinets",
    "ARMARIOS_NOS_BANHEIROS": "has_bathroom_cabinets", "ARMARIOS_NA_COZINHA": "has_kitchen_cabinets",
    "AR_CONDICIONADO": "has_air_conditioning", "CHUVEIRO_A_GAS": "has_gas_heated_shower",
    "VARANDA_GOURMET": "has_gourmet_balcony", "APARTAMENTO_COBERTURA": "is_penthouse",
    "CLOSET": "has_closet", "JARDIM": "has_garden", "QUINTAL": "has_yard",
    "GARDEN": "has_garden_private_area", "SOL_DA_MANHA": "has_morning_sunlight",
    "SOL_DA_TARDE": "has_afternoon_sunlight", "VENTILADOR_DE_TETO": "has_ceiling_fan",
    "VISTA_LIVRE": "has_clear_view", "CHUVEIRO_ELETRICO": "has_eletric_shower",
    "MICROONDAS": "has_microwave", "TOMADAS_NOVAS": "has_brazilian_plug",
    "TELEVISAO": "has_television", "CHURRASQUEIRA": "has_barbecue_grill", "COOKTOP": "has_cooktop",
    "QUADRA_ESPORTIVA": "has_sports_court", "ACADEMIA": "has_gym", "SALAO_DE_FESTAS": "has_party_saloon",
    "SAUNA": "has_sauna", "LAVANDERIA_NO_PREDIO": "has_common_laundry",
    "ESPACO_GOURMET_NA_AREA_COMUM": "has_gourmet_space", "ELEVADOR": "has_elevator",
}


def _sim(items):
    return {a["key"]: a.get("value") == "SIM" for a in items or [] if a.get("key")}


def ficha(lid, kind, tentativas=3):
    """houseInfo da página pública do imóvel. A página às vezes vem sem os dados na
    primeira chamada (cache do SSR); tenta de novo antes de desistir."""
    url = f"https://www.quintoandar.com.br/imovel/{lid}/{'comprar' if kind == 'sale' else 'alugar'}"
    erro = None
    for i in range(tentativas):
        if i:
            time.sleep(3 * i)
        r = SESSION.get(url, timeout=30)
        if r.status_code == 404:
            raise RuntimeError("página HTTP 404 (saiu do ar)")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if r.status_code != 200 or not m:
            erro = f"página HTTP {r.status_code}{'' if m else ' sem __NEXT_DATA__'}"
            continue
        hi = (json.loads(m.group(1)).get("props", {}).get("pageProps", {})
              .get("initialState", {}).get("house", {}).get("houseInfo", {}))
        if hi.get("id") and hi.get("address"):
            return hi
        erro = "houseInfo vazio"
    raise RuntimeError(erro)


def _post(path, body, tentativas=2):
    for i in range(tentativas):
        try:
            r = SESSION.post(API + path, json=body, timeout=30,
                             headers={"Origin": "https://www.quintoandar.com.br",
                                      "Referer": "https://www.quintoandar.com.br/"})
            break
        except requests.Timeout:
            if i == tentativas - 1:
                raise
            time.sleep(5)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


def avaliacao(hi, kind):
    feats = {}
    for grp in ("amenities", "installations", "comfortCommodities", "practicalityCommodities"):
        feats.update(_sim(hi.get(grp)))
    feats.update(_sim((hi.get("condominium") or {}).get("features", {}).get("installations")))
    price = hi.get("rentPrice") if kind == "rent" else hi.get("salePrice")
    body = {
        "businessContext": kind, "type": TYPES.get(hi.get("type"), "APARTMENT"),
        "latitude": hi["address"]["lat"], "longitude": hi["address"]["lng"],
        "bathroom_count": hi.get("bathrooms") or 0, "bedroom_count": hi.get("bedrooms") or 0,
        "parking_slots_count": hi.get("parkingSpaces") or 0, "iptu_per_month": hi.get("iptu") or 0,
        "total_area": hi.get("area") or 0, "price": price, "id": str(hi["id"]),
        "suite_count": hi.get("suites") or 0, "condominium_per_month": hi.get("condoPrice") or 0,
        "city": hi.get("city") or "São Paulo", "is_furnished": bool(hi.get("hasFurniture")),
        "is_near_subway": bool(hi.get("isNearSubway")), "new_or_renovated": bool(hi.get("isNewOrRenovated")),
    }
    for key, field in FLAGS.items():
        body[field] = bool(feats.get(key))
    return _post("price-suggestion", body), price


def similares(hi, kind, aval, price):
    condo = (hi.get("condominium") or {}).get("id") or (hi.get("condominium") or {}).get("condoId")
    body = {"house_id": str(hi["id"]), "bedrooms": hi.get("bedrooms") or 0,
            "percentile_10": (aval or {}).get("lowerBoundLimit") or 0,
            "percentile_90": (aval or {}).get("upperBoundLimit") or 0,
            "business_context": kind, "condo_ids": [condo] if condo else [],
            "listing_price": hi.get("totalCost") if kind == "rent" else price,
            "just_unavailable_similar_houses": False}
    if kind == "rent":
        body["listing_rent_price"] = price
    return _post("similar-houses", body)


def _row(hi, kind):
    feats = {}
    for grp in ("amenities", "installations", "comfortCommodities", "practicalityCommodities"):
        feats.update(_sim(hi.get(grp)))
    condo = hi.get("condominium") or {}
    cfeat = condo.get("features") or {}
    feats.update(_sim(cfeat.get("installations")))
    lst = next((x for x in hi.get("listings") or []
                if (x.get("businessContext") or "").lower() == kind), {})
    rf = hi.get("rangeFloor") or {}
    return {
        "floor_min": rf.get("min"), "floor_max": rf.get("max"),
        "doorman": cfeat.get("doorman") or ("24h" if feats.get("PORTARIA_24H") else None),
        "near_subway": hi.get("isNearSubway"),
        "morning_sun": feats.get("SOL_DA_MANHA"), "afternoon_sun": feats.get("SOL_DA_TARDE"),
        "clear_view": feats.get("VISTA_LIVRE"), "quiet_street": feats.get("RUA_SILENCIOSA"),
        "balcony": feats.get("VARANDA"), "elevator": feats.get("ELEVADOR"),
        "pool": feats.get("PISCINA"), "gym": feats.get("ACADEMIA"),
        "construction_year": hi.get("constructionYear"),
        "condo_name": condo.get("name"), "zip": (hi.get("address") or {}).get("zipCode"),
        # publicação atual (a primeira pode ser de um contrato anterior da mesma unidade)
        "first_published_at": lst.get("lastPublicationDate") or hi.get("lastPublishedDate")
                              or lst.get("firstPublicationDate"),
        "custom_offer": hi.get("allowsCustomOffer"), "direct_offer": hi.get("allowDirectOffer"),
        "visit_status": hi.get("houseVisitStatus"), "is_reserved": hi.get("isReserved"),
        "iptu": hi.get("iptu"), "condo_price": hi.get("condoPrice"),
        "photos": json.dumps([{"url": p.get("url"), "room": p.get("subtitle")}
                              for p in hi.get("photos") or []], ensure_ascii=False),
        "features": json.dumps(sorted(k for k, v in feats.items() if v)),
    }


COLS = ["floor_min", "floor_max", "doorman", "near_subway", "morning_sun", "afternoon_sun",
        "clear_view", "quiet_street", "balcony", "elevator", "pool", "gym", "construction_year",
        "condo_name", "zip", "first_published_at", "custom_offer", "direct_offer", "visit_status",
        "is_reserved", "iptu", "condo_price", "photos", "features",
        "price_state", "suggested_price", "p10", "p30", "p70", "p90", "faster_price",
        "certainty", "similares", "error"]


def enrich_one(conn, lid, kind):
    """Busca e grava. Devolve o dict gravado (com price_state etc.)."""
    row = {c: None for c in COLS}
    try:
        hi = ficha(lid, kind)
        row.update(_row(hi, kind))
        time.sleep(DELAY)
        aval, price = avaliacao(hi, kind)
        st = aval.get("currentPriceState") or {}
        row.update(price_state=st.get("state"), suggested_price=aval.get("suggestedPrice"),
                   p10=aval.get("lowerBoundLimit"), p30=aval.get("suggestedLowerBoundPrice"),
                   p70=aval.get("suggestedUpperBoundPrice"), p90=aval.get("upperBoundLimit"),
                   faster_price=((aval.get("dealObjectiveRanges") or {}).get("FASTER") or {}).get("suggestedPrice"),
                   certainty=aval.get("suggestionCertainty"))
        time.sleep(DELAY)
        try:
            row["similares"] = json.dumps(similares(hi, kind, aval, price), ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001  similares são opcionais
            row["error"] = f"similares: {exc}"[:300]
    except Exception as exc:  # noqa: BLE001
        row["error"] = str(exc)[:300]
        LOG.warning("enrich %s/%s falhou: %s", kind, lid, exc)
    cols = ["listing_id", "kind", "fetched_at"] + COLS
    vals = [lid, kind, datetime.now(timezone.utc)] + [row[c] for c in COLS]
    upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in ["fetched_at"] + COLS)
    conn.execute(f"INSERT INTO enrichment ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                 f"ON CONFLICT (listing_id, kind) DO UPDATE SET {upd}", vals)
    conn.commit()
    time.sleep(DELAY)
    return row


def enrich_many(ids, kind):
    with db.connect() as conn:
        return {lid: enrich_one(conn, lid, kind) for lid in ids}


def favorites_due(stale_days=7):
    with db.connect() as conn:
        return conn.execute("""
            SELECT f.listing_id, f.kind FROM favorites f
            JOIN listings l ON l.id=f.listing_id AND l.kind=f.kind AND l.status='active'
            LEFT JOIN enrichment e ON e.listing_id=f.listing_id AND e.kind=f.kind
            WHERE e.fetched_at IS NULL OR e.fetched_at < now() - make_interval(days => %s)
               OR l.price_changed_at > e.fetched_at
        """, (stale_days,)).fetchall()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", nargs="?", choices=["sale", "rent"])
    ap.add_argument("ids", nargs="*", type=int)
    ap.add_argument("--favorites", action="store_true")
    ap.add_argument("--stale-days", type=int, default=7)
    a = ap.parse_args()
    db.init_schema()
    if a.favorites:
        due = favorites_due(a.stale_days)
        LOG.info("favoritos a enriquecer: %s", len(due))
        with db.connect() as conn:
            for lid, kind in due:
                enrich_one(conn, lid, kind)
    else:
        for lid, r in enrich_many(a.ids, a.kind).items():
            print(lid, {k: r[k] for k in ("price_state", "suggested_price", "floor_min", "floor_max",
                                           "first_published_at", "custom_offer", "error")})
