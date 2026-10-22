#!/usr/bin/env python3
"""Gera os dados públicos do Radar Imobiliário de SP (só agregados).

Lê o banco do coletor com um papel somente leitura e escreve JSON em
`site/data/`. Nada que identifique um anúncio sai daqui: sem id, link,
endereço, foto ou coordenada exata. Cada agregado precisa de pelo menos
K_MIN anúncios (K_HEX no mapa), senão é suprimido. Valores são arredondados.

Uso: python3 gerar.py [--saida DIR]
Conexão: PG* do ambiente (padrão: socket local, papel do usuário do sistema).
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import h3
import psycopg

K_MIN = 20          # mínimo de anúncios por bairro/recorte
K_HEX = 10          # mínimo por célula do mapa
H3_RES = (7, 8)     # 7 ≈ 5 km² (visão da cidade), 8 ≈ 0,74 km² (zoom de bairro)
# A coleta da cidade inteira começou em 04/07/2026; antes disso o histórico é parcial.
INICIO = date(2026, 7, 6)  # segunda-feira, para a série semanal começar cheia

# Faixas plausíveis: fora delas é erro de cadastro e distorce a mediana.
FAIXA_M2 = {"sale": (1500, 80000), "rent": (10, 500)}
FAIXA_AREA = (15, 1500)

ARRED = {
    "sale": {"preco": 1000, "m2": 10, "total": 1000},
    "rent": {"preco": 10, "m2": 0.5, "total": 10},
}


def arred(v, passo):
    if v is None:
        return None
    r = round(v / passo) * passo
    return int(r) if float(passo).is_integer() else round(r, 1)


def mediana(xs):
    return statistics.median(xs) if xs else None


def quartis(xs):
    if len(xs) < 4:
        return None, None
    q = statistics.quantiles(xs, n=4)
    return q[0], q[2]


def ler_ativos(cur, kind):
    lo, hi = FAIXA_M2[kind]
    cur.execute(
        """
        SELECT neighborhood_norm, neighborhood, region, lat, lng, area, bedrooms,
               price::float, price_m2::float, total_cost::float, first_seen
          FROM listings
         WHERE kind = %s AND status = 'active'
           AND price_m2 BETWEEN %s AND %s AND area BETWEEN %s AND %s
        """,
        (kind, lo, hi, *FAIXA_AREA),
    )
    return cur.fetchall()


def ler_quedas(cur, kind, desde):
    """Anúncios ativos com pelo menos uma redução de preço desde `desde`."""
    cur.execute(
        """
        SELECT DISTINCT listing_id FROM (
            SELECT listing_id, observed_at, price,
                   lag(price) OVER (PARTITION BY listing_id ORDER BY observed_at) AS ant
              FROM price_history WHERE kind = %s
        ) t WHERE ant IS NOT NULL AND price < ant AND observed_at >= %s
        """,
        (kind, desde),
    )
    ids = {r[0] for r in cur.fetchall()}
    cur.execute(
        "SELECT neighborhood_norm, id FROM listings WHERE kind = %s AND status = 'active'",
        (kind,),
    )
    por_bairro = defaultdict(lambda: [0, 0])
    for nb, i in cur.fetchall():
        por_bairro[nb][1] += 1
        if i in ids:
            por_bairro[nb][0] += 1
    return por_bairro


def ler_saidas(cur, kind, desde):
    """Dias no ar dos anúncios que saíram desde `desde` (entrada após INICIO)."""
    cur.execute(
        """
        SELECT neighborhood_norm,
               EXTRACT(EPOCH FROM removed_at - first_seen) / 86400
          FROM listings
         WHERE kind = %s AND status = 'off_market'
           AND removed_at >= %s AND first_seen >= %s
        """,
        (kind, desde, INICIO),
    )
    por_bairro = defaultdict(list)
    for nb, dias in cur.fetchall():
        por_bairro[nb].append(float(dias))
    return por_bairro


def serie_semanal(cur, kind):
    lo, hi = FAIXA_M2[kind]
    cur.execute(
        """
        SELECT date_trunc('week', first_seen)::date AS sem, count(*),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY price_m2)
          FROM listings
         WHERE kind = %s AND first_seen >= %s AND price_m2 BETWEEN %s AND %s
         GROUP BY 1 ORDER BY 1
        """,
        (kind, INICIO, lo, hi),
    )
    novos = {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}
    cur.execute(
        """
        SELECT date_trunc('week', removed_at)::date, count(*)
          FROM listings
         WHERE kind = %s AND removed_at >= %s GROUP BY 1
        """,
        (kind, INICIO),
    )
    saidas = dict(cur.fetchall())
    hoje = date.today()
    semana_atual = hoje - timedelta(days=hoje.weekday())
    out = []
    for sem in sorted(set(novos) | set(saidas)):
        if sem >= semana_atual:  # semana incompleta
            continue
        n, m2 = novos.get(sem, (0, None))
        out.append({
            "semana": sem.isoformat(),
            "novos": n,
            "saidas": saidas.get(sem, 0),
            "m2_novos": arred(m2, ARRED[kind]["m2"]) if n >= K_MIN else None,
        })
    return out


def gerar_kind(cur, kind):
    a = ARRED[kind]
    ativos = ler_ativos(cur, kind)
    hoje = datetime.now(timezone.utc)
    quedas = ler_quedas(cur, kind, hoje - timedelta(days=90))
    saidas = ler_saidas(cur, kind, hoje - timedelta(days=90))

    grupos = defaultdict(list)
    rotulos = defaultdict(lambda: defaultdict(int))
    regioes = defaultdict(lambda: defaultdict(int))
    quartos = defaultdict(list)
    celulas = {r: defaultdict(list) for r in H3_RES}
    for nb, nome, reg, lat, lng, area, q, preco, m2, total, _ in ativos:
        if nb:
            grupos[nb].append((area, preco, m2, total))
            rotulos[nb][nome] += 1
            if reg:
                regioes[nb][reg] += 1
        quartos[min(q or 0, 4)].append(m2)
        for r in H3_RES:
            celulas[r][h3.latlng_to_cell(lat, lng, r)].append((m2, nome))

    bairros = []
    for nb, linhas in grupos.items():
        if len(linhas) < K_MIN:
            continue
        m2s = sorted(l[2] for l in linhas)
        p25, p75 = quartis(m2s)
        qd, tot = quedas.get(nb, (0, 0))
        sai = saidas.get(nb, [])
        bairros.append({
            "nome": max(rotulos[nb], key=rotulos[nb].get),
            "regiao": max(regioes[nb], key=regioes[nb].get) if regioes[nb] else None,
            "ativos": len(linhas),
            "preco": arred(mediana([l[1] for l in linhas]), a["preco"]),
            "m2": arred(mediana(m2s), a["m2"]),
            "m2_p25": arred(p25, a["m2"]),
            "m2_p75": arred(p75, a["m2"]),
            "area": arred(mediana([l[0] for l in linhas]), 1),
            "total": arred(mediana([l[3] for l in linhas if l[3]]), a["total"]) if kind == "rent" else None,
            "pct_queda_90d": round(100 * qd / tot) if tot >= K_MIN else None,
            "saidas_90d": len(sai) if len(sai) >= K_MIN else None,
            "dias_ate_sair": arred(mediana(sai), 1) if len(sai) >= K_MIN else None,
        })
    bairros.sort(key=lambda b: -b["ativos"])

    todos_m2 = [r[8] for r in ativos]
    tot_q = sum(v[1] for v in quedas.values())
    resumo = {
        "ativos": len(ativos),
        "preco": arred(mediana([r[7] for r in ativos]), a["preco"]),
        "m2": arred(mediana(todos_m2), a["m2"]),
        "total": arred(mediana([r[9] for r in ativos if r[9]]), a["total"]) if kind == "rent" else None,
        "pct_queda_90d": round(100 * sum(v[0] for v in quedas.values()) / tot_q) if tot_q else None,
        "saidas_90d": sum(len(v) for v in saidas.values()),
        "dias_ate_sair": arred(mediana([d for v in saidas.values() for d in v]), 1),
        "bairros_publicados": len(bairros),
    }
    por_quartos = [
        {"quartos": q, "ativos": len(v), "m2": arred(mediana(v), a["m2"])}
        for q, v in sorted(quartos.items()) if len(v) >= K_MIN
    ]

    mapas = {r: mapa_hex(cel, a) for r, cel in celulas.items()}

    return {
        "resumo": resumo,
        "por_quartos": por_quartos,
        "serie": serie_semanal(cur, kind),
        "bairros": bairros,
    }, mapas


def mapa_hex(celulas, a):
    """GeoJSON com a mediana de R$/m² por célula e o bairro mais comum nela (para o visitante se localizar)."""
    feats = []
    for cel, v in celulas.items():
        if len(v) < K_HEX:
            continue
        nomes = defaultdict(int)
        for _, nome in v:
            if nome:
                nomes[nome] += 1
        borda = [[lng, lat] for lat, lng in h3.cell_to_boundary(cel)]
        borda.append(borda[0])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[round(x, 5), round(y, 5)] for x, y in borda]]},
            "properties": {
                "n": len(v),
                "m2": arred(mediana([x[0] for x in v]), a["m2"]),
                "b": max(nomes, key=nomes.get) if nomes else None,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def gravar(caminho, obj):
    """Escrita atômica: o site nunca serve um JSON pela metade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, caminho)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=str(Path(__file__).resolve().parent / "site" / "data"))
    args = ap.parse_args()
    saida = Path(args.saida)

    with psycopg.connect(os.environ.get("RADAR_DSN", "dbname=quintoandar")) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            meta = {
                "gerado_em": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "historico_desde": INICIO.isoformat(),
                "k_min": K_MIN,
                "k_hex": K_HEX,
            }
            for kind in ("sale", "rent"):
                dados, mapas = gerar_kind(cur, kind)
                gravar(saida / f"{kind}.json", dados)
                for r, mapa in mapas.items():
                    gravar(saida / f"{kind}-mapa{r}.json", mapa)
                meta[kind] = dados["resumo"]["ativos"]
            gravar(saida / "meta.json", meta)
    print(f"ok: {saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
