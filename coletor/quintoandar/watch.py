"""Vigia: a cada 30 min pega os anúncios mais recentes, grava os novos e avisa no Discord
os que batem com alguma pesquisa salva, já com a avaliação do QuintoAndar.

Custo por execução: 1 chamada de busca por tipo (200 anúncios mais recentes de SP; o
volume diário fica em ~900 novos, então 200 a cada 30 min cobre com folga) + enriquecimento
só dos que batem com pesquisas salvas (limite MAX_ENRICH).

Uso: python -m quintoandar.watch [--sem-discord]
"""
import logging
import sys
from datetime import datetime, timezone

from . import db, enrich, filtros, notifier
from .pipeline import KINDS, _upsert, normalize
from .scraper import FIELDS, SP_VIEWPORT, Scraper

LOG = logging.getLogger("quintoandar.watch")
MAX_ENRICH = 15        # por execução, somando os dois tipos
JANELA_H = 24          # considera anúncios que entraram na base nas últimas 24 h
POR_PESQUISA = 10      # máximo de cards por pesquisa por execução
ESTADO = {"BELOW_PRICED": ("🟢", "abaixo do mercado", 0x1F9D55),
          "WELL_PRICED": ("⚪", "no preço", 0x8B949E),
          "OVER_PRICED": ("🔴", "acima do mercado", 0xD93A3A),
          "ABOVE_PRICED": ("🔴", "acima do mercado", 0xD93A3A)}


def _fmt(n):
    return f"R$ {float(n):,.0f}".replace(",", ".") if n is not None else "—"


def coletar_recentes(kind):
    sc = Scraper(business_context=KINDS[kind]["business_context"], delay=0)
    data = sc._post(SP_VIEWPORT, 0, 200, FIELDS)  # ordenado por MOST_RECENT
    hits = (data or {}).get("hits", {}).get("hits", [])
    stats = dict(new=0, price_changes=0, reactivated=0)
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        for h in hits:
            src = h.get("_source", {})
            src.setdefault("orderedImageList", h.get("orderedImageList"))
            if src.get("id"):
                _upsert(conn, normalize(src, kind), now, stats)
        conn.commit()
    LOG.info("[%s] %s recentes, %s novos, %s mudanças de preço", kind, len(hits),
             stats["new"], stats["price_changes"])
    return stats


def candidatos(conn, kind):
    out = []
    for sid, nome, params in conn.execute(
            "SELECT id, name, params FROM saved_searches WHERE kind=%s ORDER BY id", (kind,)).fetchall():
        clause, ps = filtros.from_saved(kind, params or {})
        rows = conn.execute(f"""
            SELECT id FROM listings WHERE {clause}
              AND first_seen > now() - make_interval(hours => %s)
              AND id NOT IN (SELECT listing_id FROM watch_alerts WHERE kind=%s AND search_id=%s)
            ORDER BY first_seen DESC""", ps + [JANELA_H, kind, sid]).fetchall()
        if rows:
            out.append((sid, nome, [r[0] for r in rows]))
    return out


def embed(conn, lid, kind, nome):
    l = conn.execute("""SELECT neighborhood, region, address, area, bedrooms, parking, price,
                               total_cost, link, cover_image FROM listings WHERE id=%s AND kind=%s""",
                     (lid, kind)).fetchone()
    e = conn.execute("""SELECT price_state, suggested_price, p10, p90, faster_price, floor_min,
                               floor_max, morning_sun, afternoon_sun, doorman, near_subway,
                               first_published_at, custom_offer
                        FROM enrichment WHERE listing_id=%s AND kind=%s""", (lid, kind)).fetchone()
    nb, reg, addr, area, q, vagas, price, total, link, cover = l
    suf = "/mês" if kind == "rent" else ""
    ic, txt, cor = ESTADO.get(e[0] if e else None, ("❔", "sem avaliação", 0x2F6BFF))
    linhas = [f"{addr or ''}",
              " · ".join(filter(None, [f"{area:.0f} m²" if area else None, f"{q} qto(s)" if q else None,
                                       f"{vagas} vaga(s)" if vagas else None,
                                       f"total {_fmt(total)}{suf}" if total else None]))]
    campos = [{"name": "Preço", "value": f"{_fmt(price)}{suf}", "inline": True},
              {"name": f"{ic} QuintoAndar", "value": txt, "inline": True}]
    if e and e[1]:
        dif = (float(price) - float(e[1])) / float(e[1]) * 100 if price else 0
        campos.append({"name": "Sugerido", "value": f"{_fmt(e[1])}{suf} ({dif:+.0f}%)", "inline": True})
        if e[2] and e[3]:
            campos.append({"name": "Faixa justa", "value": f"{_fmt(e[2])} – {_fmt(e[3])}", "inline": True})
        if e[4]:
            campos.append({"name": "Para fechar rápido", "value": f"{_fmt(e[4])}{suf}", "inline": True})
    if e:
        extra = []
        if e[5] is not None:
            extra.append(f"andar {e[5]}–{e[6]}" if e[6] and e[6] != e[5] else f"andar {e[5]}")
        if e[7]:
            extra.append("sol da manhã")
        if e[8]:
            extra.append("sol da tarde")
        if e[9] == "NightAndDayShift":
            extra.append("portaria 24 h")
        if e[10]:
            extra.append("perto do metrô")
        if e[12]:
            extra.append("aceita contraproposta")
        if extra:
            linhas.append(" · ".join(extra))
    em = {"title": f"🏠 {nb or 'Imóvel'}{' · ' + reg if reg and reg != nb else ''}", "url": link,
          "color": cor, "description": "\n".join(x for x in linhas if x), "fields": campos,
          "footer": {"text": f"pesquisa salva: {nome}"}}
    if cover:
        em["thumbnail"] = {"url": cover}
    return em


def run(discord=True):
    db.init_schema()
    for kind in KINDS:
        try:
            coletar_recentes(kind)
        except Exception:  # noqa: BLE001  sem rede/API: só avisa o que já está na base
            LOG.exception("[%s] busca dos recentes falhou", kind)
    orcamento = MAX_ENRICH
    total = 0
    with db.connect() as conn:
        for kind in KINDS:
            for sid, nome, ids in candidatos(conn, kind):
                lote, resto = ids[:POR_PESQUISA], len(ids) - POR_PESQUISA
                for lid in lote:
                    ja = conn.execute("SELECT 1 FROM enrichment WHERE listing_id=%s AND kind=%s "
                                      "AND fetched_at > now() - interval '1 day' AND error IS NULL",
                                      (lid, kind)).fetchone()
                    if not ja and orcamento > 0:
                        enrich.enrich_one(conn, lid, kind)
                        orcamento -= 1
                embeds = [embed(conn, lid, kind, nome) for lid in lote]
                msg = (f"🔔 **{len(lote)} anúncio(s) novo(s)** na pesquisa *{nome}* "
                       f"({'aluguel' if kind == 'rent' else 'venda'})"
                       + (f" — e mais {resto} no dashboard" if resto > 0 else ""))
                ok = bool(discord and notifier.WEBHOOK)   # teste sem Discord não marca como avisado
                if ok:
                    for i in range(0, len(embeds), 10):
                        payload = {"embeds": embeds[i:i + 10]}
                        if i == 0:
                            payload["content"] = msg
                        ok = notifier._post(payload) and ok
                else:
                    LOG.info("%s\n%s", msg, "\n".join(f"  {e['title']} · {e['fields'][1]['value']}" for e in embeds))
                if ok:
                    conn.executemany("INSERT INTO watch_alerts (listing_id, kind, search_id) VALUES (%s,%s,%s) "
                                     "ON CONFLICT DO NOTHING", [(lid, kind, sid) for lid in ids])
                    conn.commit()
                total += len(lote)
    LOG.info("vigia ok: %s avisados, %s enriquecidos", total, MAX_ENRICH - orcamento)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    run(discord="--sem-discord" not in sys.argv)
