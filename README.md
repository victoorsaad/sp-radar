# SP Radar · Radar imobiliário de São Paulo

**Ao vivo:** https://spradar.saadcloud.com.br · [English below](#english)

Os portais mostram o hoje. Este projeto guarda o filme: todo dia coleta os anúncios de venda e aluguel da cidade de São Paulo e registra cada mudança de preço e cada vez que um imóvel sai do ar ou volta. Desde julho de 2026 cobre a cidade inteira (~150 mil imóveis à venda, ~37 mil para alugar).

## Partes

| Pasta | O que é |
|---|---|
| `coletor/` | Coleta diária (Python + PostgreSQL), histórico de preço/status e um painel privado (FastAPI + Leaflet) com filtros, favoritos, alerta de queda de preço no Discord e avaliação de cada anúncio. |
| `publico/` | O site público: `gerar.py` transforma o banco em JSON **só com agregados**, `site/` é a página estática e `serve.py` o servidor com allowlist. |

### Como a coleta funciona
A busca do portal só devolve 1.000 resultados por consulta, então o `scraper.py` divide o mapa da cidade numa *quadtree*: cada célula com mais de 950 anúncios vira quatro, até todas caberem. São ~350 requisições por tipo, com intervalo entre elas. O `pipeline.py` grava tudo com histórico e só marca anúncios como fora do ar se a coleta viu pelo menos 50% dos ativos anteriores (uma coleta bloqueada não zera o mercado).

### Privacidade do site público
Nenhum anúncio individual sai do banco: sem id, endereço, foto, link ou coordenada. Bairros com menos de 20 anúncios e hexágonos com menos de 10 são suprimidos; valores são arredondados. O gerador roda com um papel do Postgres somente leitura e com `GRANT` só nas colunas que usa; o servidor web não tem acesso ao banco.

## Rodando

```bash
cd coletor
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp config/.env.example config/.env   # ajuste a senha
./venv/bin/python -m quintoandar.db              # cria o schema
./venv/bin/python -m quintoandar.pipeline --kind sale --dry-run
./run_pipeline.sh                                # coleta venda + aluguel
./venv/bin/python app/main.py                    # painel em http://127.0.0.1:8095
```

Site público: `pip install "psycopg[binary]" h3`, `python publico/gerar.py` e `python publico/serve.py`. Units do systemd de exemplo em `publico/servidor/`.

## Aviso
Projeto pessoal e educacional, sem relação com o QuintoAndar. Usa endpoints que o próprio site chama, sem login, num ritmo baixo. Leia os termos de uso do portal antes de rodar a coleta e não republique anúncios individuais. Os números são preços **pedidos**, não de fechamento, e não são avaliação de imóvel.

---

## English

**Live:** https://spradar.saadcloud.com.br

Listing sites show you today; this keeps the film. Every day it collects São Paulo's sale and rental listings and records each price change and each time a home leaves the market or comes back. Since July 2026 it covers the whole city (~150k homes for sale, ~37k for rent).

- `coletor/`: daily collector (Python + PostgreSQL), price/status history and a private dashboard (FastAPI + Leaflet) with filters, favourites, Discord price-drop alerts and per-listing valuation.
- `publico/`: the public site. `gerar.py` turns the database into **aggregate-only** JSON (neighbourhoods with fewer than 20 listings and hexagons with fewer than 10 are suppressed, values rounded), `site/` is the static page and `serve.py` an allowlisted static server with no database access.

The portal's search caps results at 1,000 per query, so the scraper splits the city into a quadtree until every cell fits (~350 requests per run). A run that sees fewer than half of the previously active listings never marks the rest as off-market.

**Disclaimer:** personal, educational project, not affiliated with QuintoAndar. It calls endpoints the public website itself uses, without login, at a low rate. Check the portal's terms before running the collector and do not republish individual listings. Figures are asking prices, not sale prices, and not a valuation.

MIT licensed.
