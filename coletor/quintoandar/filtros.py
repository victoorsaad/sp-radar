"""Filtros da listagem em SQL, compartilhados pelo dashboard (app/main.py) e pelo vigia (watch.py)."""
from .textnorm import norm_name


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _i(v):
    n = _f(v)
    return int(n) if n is not None else None


def build(kind, status="active", region=None, neighborhood=None, bedrooms=None, bathrooms=None,
          parking=None, min_price=None, max_price=None, min_total=None, max_total=None,
          max_m2=None, furnished=None, pets=None, show_hidden=False, below=False):
    """Devolve (clause, params) para `FROM listings WHERE {clause}`.
    `neighborhood` aceita CSV ou lista de nomes (normalizados aqui)."""
    where, params = ["kind = %s", "status = %s"], [kind, status or "active"]
    if region:
        where.append("region_norm = %s"); params.append(norm_name(region))
    if neighborhood:
        items = neighborhood.split(",") if isinstance(neighborhood, str) else neighborhood
        nbs = [norm_name(n) for n in items if n and n.strip()]
        if nbs:
            where.append("neighborhood_norm = ANY(%s)"); params.append(nbs)
    if not show_hidden:
        where.append("id NOT IN (SELECT listing_id FROM hidden WHERE kind = %s)")
        params.append(kind)
    for col, op, val in (("bedrooms", "=", _i(bedrooms)), ("bathrooms", ">=", _i(bathrooms)),
                         ("parking", ">=", _i(parking)), ("price", ">=", _f(min_price)),
                         ("price", "<=", _f(max_price)), ("total_cost", ">=", _f(min_total))):
        if val is not None:
            where.append(f"{col} {op} %s"); params.append(val)
    if _f(max_total) is not None:
        where.append("total_cost <= %s AND total_cost > 0"); params.append(_f(max_total))
    if _f(max_m2) is not None:
        where.append("price_m2 <= %s AND price_m2 > 0"); params.append(_f(max_m2))
    if furnished:
        where.append("is_furnished = true")
    if pets:
        where.append("accepts_pets = true")
    if below:   # só os que o QuintoAndar avaliou como abaixo do mercado (tabela enrichment)
        where.append("id IN (SELECT listing_id FROM enrichment WHERE kind = %s "
                     "AND price_state = 'BELOW_PRICED')")
        params.append(kind)
    return " AND ".join(where), params


def from_saved(kind, p):
    """Converte os params de uma pesquisa salva (estado do dashboard) em (clause, params)."""
    return build(kind, status=p.get("status") or "active", region=p.get("region"),
                 neighborhood=p.get("neighborhoods") or [], bedrooms=p.get("bedrooms"),
                 bathrooms=p.get("bathrooms"), parking=p.get("parking"),
                 min_price=p.get("min_price"), max_price=p.get("max_price"),
                 min_total=p.get("min_total"), max_total=p.get("max_total"),
                 max_m2=p.get("max_m2"), furnished=p.get("furnished"), pets=p.get("pets"),
                 show_hidden=p.get("show_hidden"), below=p.get("below"))
