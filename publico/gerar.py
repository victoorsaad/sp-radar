#!/usr/bin/env python3
"""Gera os dados públicos do Radar Imobiliário de SP (só agregados).

Lê o banco do coletor com um papel somente leitura e escreve JSON em
`site/data/`. Nada que identifique um anúncio sai daqui: sem id, link,
endereço, foto ou coordenada exata. Cada agregado precisa de pelo menos
K_MIN anúncios (K_HEX no mapa, K_SUB nos recortes por quartos), senão é
suprimido. Valores são arredondados.

Saída, para cada negócio (sale/rent) e tipo de imóvel (todos/apto/casa/studio):
  {kind}-{tipo}.json         panorama, tipologia, série, negociação, bairros
  {kind}-{tipo}-mapa{r}.json hexágonos H3 (r = 7 e 8)
e meta.json.

Uso: python3 gerar.py [--saida DIR]
Conexão: RADAR_DSN (padrão: socket local, papel do usuário do sistema).
"""
import argparse
import bisect
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

K_MIN = 20          # bairro
K_SUB = 10          # recorte dentro do bairro (quartos)
K_HEX = 10          # hexágono do mapa
H3_RES = (7, 8)     # 7 ≈ 5 km² (visão da cidade), 8 ≈ 0,74 km² (zoom de bairro)
# A coleta da cidade inteira começou em 04/07/2026; antes disso o histórico é parcial.
INICIO = date(2026, 7, 6)  # segunda-feira, para a série semanal começar cheia
JANELA = 90         # dias para quedas e saídas
TENDENCIA = 6       # semanas comparadas na variação de preço/estoque

FAIXA_M2 = {"sale": (1500, 80000), "rent": (10, 500)}
FAIXA_AREA = (15, 1500)
TIPOS = {
    "todos": None,
    "apto": ("Apartamento",),
    "casa": ("Casa", "CasaCondominio"),
    "studio": ("StudioOuKitchenette",),
}
QUARTOS = ("1", "2", "3", "4")  # "1" = até 1 quarto, "4" = 4 ou mais
ARRED = {
    "sale": {"preco": 1000, "m2": 10, "total": 10},
    "rent": {"preco": 10, "m2": 0.5, "total": 10},
}
# Faixas de preço do "quanto o seu dinheiro compra" (bordas superiores, escala log).
FAIXAS = {
    "sale": [int(round(150_000 * 1.12 ** i, -4)) for i in range(46)],   # 150 mil a ~25 mi
    "rent": [int(round(800 * 1.08 ** i, -1)) for i in range(50)],       # 800 a ~37 mil
}
CORTES = [2, 4, 6, 8, 10, 15, 20]  # bordas (%) do histograma de profundidade da redução


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


def pct(a, b):
    return round(100 * a / b) if b else None


def banda(q):
    q = q or 0
    return "1" if q <= 1 else ("4" if q >= 4 else str(q))


# ---------------------------------------------------------------- leitura

def ler(cur, kind):
    """Ativos, saídas recentes e histórico de redução de preço de um negócio."""
    lo, hi = FAIXA_M2[kind]
    cur.execute(
        """
        SELECT id, neighborhood_norm, neighborhood, region, lat, lng, area::float, bedrooms,
               price::float, price_m2::float, total_cost::float, condominium::float, type,
               is_furnished, accepts_pets
          FROM listings
         WHERE kind = %s AND status = 'active'
           AND price_m2 BETWEEN %s AND %s AND area BETWEEN %s AND %s
        """,
        (kind, lo, hi, *FAIXA_AREA),
    )
    ativos = [
        {"id": i, "nb": nb, "nome": nome, "reg": reg, "lat": lat, "lng": lng, "area": area,
         "q": banda(q), "preco": preco, "m2": m2, "total": total, "condo": condo, "tipo": tipo,
         "mob": mob, "pets": pets}
        for (i, nb, nome, reg, lat, lng, area, q, preco, m2, total, condo, tipo, mob, pets) in cur.fetchall()
    ]

    desde = datetime.now(timezone.utc) - timedelta(days=JANELA)
    cur.execute(
        """
        SELECT id, neighborhood_norm, lat, lng, bedrooms, type,
               EXTRACT(EPOCH FROM removed_at - first_seen)::float / 86400
          FROM listings
         WHERE kind = %s AND status = 'off_market'
           AND removed_at >= %s AND first_seen >= %s
           AND price_m2 BETWEEN %s AND %s AND area BETWEEN %s AND %s
        """,
        (kind, desde, INICIO, lo, hi, *FAIXA_AREA),
    )
    saidas = cur.fetchall()

    # Redução LÍQUIDA de preço. No aluguel o preço oscila (baixa, sobe, baixa de novo), então
    # somar reduções inflaria o desconto. Na janela: preço de referência = último antes do início
    # da janela (ou o primeiro dentro dela) contra o preço atual. Na vida do anúncio (para quem
    # saiu do ar): primeiro preço contra o último.
    cur.execute(
        "SELECT listing_id, observed_at, price::float FROM price_history WHERE kind = %s ORDER BY listing_id, observed_at",
        (kind,),
    )
    hist = defaultdict(list)
    for lid, quando, preco in cur.fetchall():
        hist[lid].append((quando, preco))
    cortes = {}
    for lid, pts in hist.items():
        antes = [p for q, p in pts if q < desde]
        ref = antes[-1] if antes else pts[0][1]
        atual = pts[-1][1]
        cortes[lid] = (
            100 * (ref - atual) / ref if atual < ref else 0.0,   # % líquido na janela
            atual < pts[0][1],                                   # baixou em algum momento da vida
        )
    return ativos, saidas, cortes, desde


def serie(cur, kind, tipos):
    """Por semana: anúncios novos, saídas, estoque no fim da semana e R$/m² mediano dos novos."""
    lo, hi = FAIXA_M2[kind]
    filtro = "AND type = ANY(%(tipos)s)" if tipos else ""
    p = {"kind": kind, "inicio": INICIO, "lo": lo, "hi": hi, "tipos": list(tipos or ())}
    cur.execute(
        f"""
        SELECT date_trunc('week', first_seen)::date, count(*),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY price_m2)
          FROM listings
         WHERE kind = %(kind)s AND first_seen >= %(inicio)s
           AND price_m2 BETWEEN %(lo)s AND %(hi)s {filtro}
         GROUP BY 1
        """,
        p,
    )
    novos = {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}
    cur.execute(
        f"""SELECT date_trunc('week', removed_at)::date, count(*) FROM listings
             WHERE kind = %(kind)s AND removed_at >= %(inicio)s {filtro} GROUP BY 1""",
        p,
    )
    saidas = dict(cur.fetchall())
    hoje = date.today()
    atual = hoje - timedelta(days=hoje.weekday())
    semanas = [s for s in sorted(set(novos) | set(saidas)) if INICIO <= s < atual]
    # Estoque no fim de cada semana: entrou até ali e ainda não tinha saído. Quem saiu e
    # voltou conta pela última saída, então o estoque antigo fica levemente subestimado.
    cur.execute(
        f"""
        SELECT w::date, (SELECT count(*) FROM listings
                          WHERE kind = %(kind)s {filtro} AND first_seen < w + interval '7 days'
                            AND (removed_at IS NULL OR removed_at >= w + interval '7 days'))
          FROM unnest(%(semanas)s::date[]) AS w
        """,
        {**p, "semanas": semanas},
    )
    estoque = dict(cur.fetchall())
    out = []
    for s in semanas:
        n, m2 = novos.get(s, (0, None))
        out.append({
            "semana": s.isoformat(),
            "novos": n,
            "saidas": saidas.get(s, 0),
            "estoque": estoque.get(s),
            "m2_novos": arred(m2, ARRED[kind]["m2"]) if n >= K_MIN else None,
        })
    return out


# ---------------------------------------------------------------- agregação

class Grupo:
    """Acumula anúncios de um recorte (cidade, bairro, quartos, hexágono)."""

    def __init__(self):
        self.precos, self.m2s, self.areas, self.totais, self.condos = [], [], [], [], []
        self.com_corte, self.cortes = 0, []
        self.dias, self.saidas_com_corte = [], 0
        self.mob = self.pets = 0
        self.nomes = defaultdict(int)

    def add(self, a, cortes_90d):
        self.precos.append(a["preco"])
        self.m2s.append(a["m2"])
        self.areas.append(a["area"])
        if a["total"]:
            self.totais.append(a["total"])
        if a["condo"]:
            self.condos.append(a["condo"])
        if cortes_90d:
            self.com_corte += 1
            self.cortes.append(cortes_90d)
        self.mob += bool(a["mob"])
        self.pets += bool(a["pets"])
        if a["nome"]:
            self.nomes[a["nome"]] += 1

    def saida(self, dias, teve_corte):
        self.dias.append(dias)
        self.saidas_com_corte += teve_corte

    @property
    def n(self):
        return len(self.precos)

    def nome(self):
        return max(self.nomes, key=self.nomes.get) if self.nomes else None

    def resumo(self, kind, k=K_MIN, completo=True):
        a = ARRED[kind]
        if self.n < k:
            return None
        out = {
            "ativos": self.n,
            "preco": arred(mediana(self.precos), a["preco"]),
            "m2": arred(mediana(self.m2s), a["m2"]),
            "area": arred(mediana(self.areas), 1),
            "pct_queda": pct(self.com_corte, self.n),
            "dias": arred(mediana(self.dias), 1) if len(self.dias) >= k else None,
        }
        if not completo:
            return out
        p25, p75 = quartis(sorted(self.m2s))
        out.update({
            "m2_p25": arred(p25, a["m2"]),
            "m2_p75": arred(p75, a["m2"]),
            "saidas": len(self.dias) if len(self.dias) >= k else None,
            # redução líquida de quem baixou o preço na janela (mediana, %)
            "corte": round(mediana(self.cortes), 1) if len(self.cortes) >= k else None,
            "pct_saiu_corte": pct(self.saidas_com_corte, len(self.dias)) if len(self.dias) >= k else None,
        })
        if kind == "rent":
            out["total"] = arred(mediana(self.totais), a["total"]) if len(self.totais) >= k else None
            out["pct_mobiliado"] = pct(self.mob, self.n)
            out["pct_pets"] = pct(self.pets, self.n)
        else:
            out["condo"] = arred(mediana(self.condos), a["total"]) if len(self.condos) >= k else None
        return out


def agregar(kind, tipos, ativos, saidas, cortes, desde):
    """Panorama, tipologia, bairros e hexágonos para um recorte de tipo de imóvel."""
    cidade = Grupo()
    por_q = defaultdict(Grupo)
    bairros = defaultdict(Grupo)
    bairro_q = defaultdict(Grupo)
    rotulos = defaultdict(lambda: defaultdict(int))
    regioes = defaultdict(lambda: defaultdict(int))
    hexes = {r: defaultdict(Grupo) for r in H3_RES}
    faixas = FAIXAS[kind]
    hist = defaultdict(lambda: defaultdict(lambda: [0] * (len(faixas) + 1)))
    hcortes = [0] * (len(CORTES) + 1)
    mob = {True: Grupo(), False: Grupo()}

    for a in ativos:
        if tipos and a["tipo"] not in tipos:
            continue
        c90 = cortes.get(a["id"], (0.0, False))[0]
        if c90:
            hcortes[bisect.bisect_left(CORTES, c90)] += 1
        cidade.add(a, c90)
        por_q[a["q"]].add(a, c90)
        if kind == "rent":
            mob[bool(a["mob"])].add(a, c90)
        if a["nb"]:
            bairros[a["nb"]].add(a, c90)
            bairro_q[(a["nb"], a["q"])].add(a, c90)
            rotulos[a["nb"]][a["nome"]] += 1
            if a["reg"]:
                regioes[a["nb"]][a["reg"]] += 1
            i = bisect.bisect_left(faixas, a["preco"])
            hist[a["nb"]]["t"][i] += 1
            hist[a["nb"]][a["q"]][i] += 1
        for r in H3_RES:
            hexes[r][h3.latlng_to_cell(a["lat"], a["lng"], r)].add(a, c90)

    for (lid, nb, lat, lng, q, tipo, dias) in saidas:
        if tipos and tipo not in tipos:
            continue
        teve = cortes.get(lid, (0.0, False))[1]
        cidade.saida(dias, teve)
        por_q[banda(q)].saida(dias, teve)
        if nb:
            bairros[nb].saida(dias, teve)
            bairro_q[(nb, banda(q))].saida(dias, teve)
        for r in H3_RES:
            hexes[r][h3.latlng_to_cell(lat, lng, r)].saida(dias, teve)

    lista = []
    for nb, g in bairros.items():
        res = g.resumo(kind)
        if not res:
            continue
        res["nome"] = max(rotulos[nb], key=rotulos[nb].get)
        res["regiao"] = max(regioes[nb], key=regioes[nb].get) if regioes[nb] else None
        res["id"] = nb
        res["quartos"] = {
            q: bairro_q[(nb, q)].resumo(kind, K_SUB, completo=False)
            for q in QUARTOS if bairro_q[(nb, q)].n >= K_SUB
        }
        res["hist"] = {k: [[i, n] for i, n in enumerate(v) if n] for k, v in hist[nb].items()}
        lista.append(res)
    lista.sort(key=lambda b: -b["ativos"])

    tipologia = []
    for q in QUARTOS:
        r = por_q[q].resumo(kind, completo=False)
        if r:
            r["q"] = q
            tipologia.append(r)

    out = {
        "resumo": cidade.resumo(kind),
        "tipologia": tipologia,
        "cortes_hist": hcortes,
        "bairros": lista,
    }
    if kind == "rent" and mob[True].n >= K_MIN and mob[False].n >= K_MIN:
        # prêmio do mobiliado, comparando a mesma banda de quartos
        premios = []
        for q in QUARTOS:
            sim = [a["m2"] for a in ativos if a["mob"] and a["q"] == q and (not tipos or a["tipo"] in tipos)]
            nao = [a["m2"] for a in ativos if not a["mob"] and a["q"] == q and (not tipos or a["tipo"] in tipos)]
            if len(sim) >= K_MIN and len(nao) >= K_MIN:
                premios.append({"q": q, "pct": round(100 * (mediana(sim) / mediana(nao) - 1))})
        out["mobiliado"] = premios
    return out, {r: mapa_hex(g, kind) for r, g in hexes.items()}, bairro_q


def mapa_hex(grupos, kind):
    a = ARRED[kind]
    feats = []
    for cel, g in grupos.items():
        if g.n < K_HEX:
            continue
        borda = [[round(lng, 5), round(lat, 5)] for lat, lng in h3.cell_to_boundary(cel)]
        borda.append(borda[0])
        props = {"n": g.n, "m2": arred(mediana(g.m2s), a["m2"]), "b": g.nome(), "pq": pct(g.com_corte, g.n)}
        if len(g.dias) >= K_HEX:
            props["d"] = arred(mediana(g.dias), 1)
        feats.append({"type": "Feature", "id": cel, "geometry": {"type": "Polygon", "coordinates": [borda]}, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def rentabilidade(venda, aluguel):
    """Rentabilidade bruta anual (aluguel × 12 ÷ preço), pareando o mesmo nº de quartos.

    Comparar o R$/m² de venda com o de aluguel do bairro inteiro distorce: a oferta de
    aluguel tem muito mais imóvel pequeno, cujo m² é mais caro. Cada banda de quartos é
    comparada com ela mesma e a média é ponderada pela oferta à venda. Exige que as bandas
    pareadas cubram pelo menos metade dos anúncios à venda do bairro.
    """
    out = {}
    for nb in {nb for nb, _ in venda}:
        soma = peso = total = 0
        for q in QUARTOS:
            v, al = venda.get((nb, q)), aluguel.get((nb, q))
            if not v:
                continue
            total += v.n
            if v.n >= K_SUB and al and al.n >= K_SUB:
                soma += mediana(al.m2s) * 12 / mediana(v.m2s) * v.n
                peso += v.n
        if peso and peso >= total / 2:
            out[nb] = 100 * soma / peso
    return out


def tendencia(serie_, campo):
    """Variação % da média das últimas TENDENCIA semanas contra as TENDENCIA anteriores."""
    vals = [s[campo] for s in serie_ if s[campo] is not None]
    if len(vals) < 2 * TENDENCIA:
        return None
    a = statistics.mean(vals[-2 * TENDENCIA:-TENDENCIA])
    b = statistics.mean(vals[-TENDENCIA:])
    return round(100 * (b - a) / a, 1) if a else None


# ---------------------------------------------------------------- saída

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
    saida = Path(ap.parse_args().saida)

    with psycopg.connect(os.environ.get("RADAR_DSN", "dbname=quintoandar")) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            brutos = {kind: ler(cur, kind) for kind in ("sale", "rent")}
            meta = {
                "gerado_em": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "historico_desde": INICIO.isoformat(),
                "janela_dias": JANELA,
                "tendencia_semanas": TENDENCIA,
                "k_min": K_MIN, "k_sub": K_SUB, "k_hex": K_HEX,
                "faixas": FAIXAS,
                "cortes": CORTES,
                "ativos": {},
            }
            for tipo, tipos in TIPOS.items():
                res, mapas, bq = {}, {}, {}
                for kind in ("sale", "rent"):
                    res[kind], mapas[kind], bq[kind] = agregar(kind, tipos, *brutos[kind])
                    s = serie(cur, kind, tipos)
                    res[kind]["serie"] = s
                    res[kind]["resumo"]["var_m2"] = tendencia(s, "m2_novos")
                    res[kind]["resumo"]["var_estoque"] = tendencia(s, "estoque")

                y = rentabilidade(bq["sale"], bq["rent"])
                for b in res["sale"]["bairros"]:
                    v = y.get(b["id"])
                    b["rent"] = round(v, 1) if v is not None else None
                ys = [b["rent"] for b in res["sale"]["bairros"] if b["rent"] is not None]
                res["sale"]["resumo"]["rent"] = round(mediana(ys), 1) if len(ys) >= K_MIN else None
                for kind in ("sale", "rent"):
                    for b in res[kind]["bairros"]:
                        del b["id"]
                    gravar(saida / f"{kind}-{tipo}.json", res[kind])
                    for r, m in mapas[kind].items():
                        for f in m["features"]:
                            del f["id"]
                        gravar(saida / f"{kind}-{tipo}-mapa{r}.json", m)
                    meta["ativos"][f"{kind}-{tipo}"] = res[kind]["resumo"]["ativos"]
            gravar(saida / "meta.json", meta)
    print(f"ok: {saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
