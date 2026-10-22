/* Radar imobiliário de SP — lê os JSON agregados de data/ e monta o relatório.
   Estado: negócio (sale/rent) × tipo de imóvel; cada combinação é um arquivo, carregado sob demanda. */
(function () {
  "use strict";
  var root = document.documentElement;
  var st = {
    kind: "sale", tipo: "apto", met: "m2", budQ: "t", budSort: "n", bud: null,
    sort: "ativos", dir: -1, meta: null, cache: {}, mapas: {}
  };
  var mapa, camada, rotulos, charts = {};

  /* ---------- utilidades ---------- */
  function en() { return root.getAttribute("data-lang") === "en"; }
  function L(pt, eng) { return en() ? eng : pt; }
  function css(n) { return getComputedStyle(root).getPropertyValue(n).trim(); }
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function nf(v, o) { return v == null ? "—" : new Intl.NumberFormat(en() ? "en-US" : "pt-BR", o || {}).format(v); }
  function brl(v, curto) {
    if (v == null) return "—";
    if (curto && v >= 1e6) return "R$ " + nf(v / 1e6, { maximumFractionDigits: v >= 1e7 ? 1 : 2 }) + L(" mi", "M");
    if (curto && v >= 1e4) return "R$ " + nf(Math.round(v / 1e3)) + L(" mil", "k");
    return "R$ " + nf(v, { maximumFractionDigits: v < 100 ? 1 : 0 });
  }
  function k(v) { return v >= 1e6 ? nf(v / 1e6, { maximumFractionDigits: 1 }) + L(" mi", "M") : v >= 1000 ? nf(v / 1000, { maximumFractionDigits: v >= 1e5 ? 0 : 1 }) + "k" : nf(v); }
  function rent() { return st.kind === "rent"; }
  function m2u() { return rent() ? L("/m²/mês", "/m²/mo") : "/m²"; }
  function pctTxt(v) { return v == null ? "—" : nf(v) + "%"; }
  function delta(v, bomSeSobe) {
    if (v == null) return "";
    var cls = v === 0 ? "" : ((v > 0) === bomSeSobe ? "up" : "down");
    return '<span class="delta ' + cls + '">' + (v > 0 ? "▲ +" : v < 0 ? "▼ " : "") + nf(v, { maximumFractionDigits: 1 }) + "%</span>";
  }
  function qLabel(q) { return q === "1" ? L("Até 1 quarto", "Up to 1 bed") : q === "4" ? L("4+ quartos", "4+ beds") : q + L(" quartos", " beds"); }
  function tipoNome() {
    var t = { todos: [L("imóveis", "homes")], apto: [L("apartamentos", "apartments")], casa: [L("casas", "houses")], studio: [L("studios", "studios")] };
    return t[st.tipo][0];
  }
  function dataLonga(iso) { return new Date(iso).toLocaleDateString(en() ? "en-GB" : "pt-BR", { day: "numeric", month: "long", year: "numeric" }); }

  function get(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(url + " " + r.status); return r.json(); });
  }
  function chave() { return st.kind + "-" + st.tipo; }
  function D() { return st.cache[chave()]; }
  function carregar() {
    var c = chave();
    if (st.cache[c]) return Promise.resolve();
    var g = "?g=" + encodeURIComponent(st.meta.gerado_em);  // muda a cada geração: o cache da borda nunca mistura versões
    return Promise.all([get("data/" + c + ".json" + g), get("data/" + c + "-mapa7.json" + g), get("data/" + c + "-mapa8.json" + g)]).then(function (r) {
      st.cache[c] = r[0];
      st.mapas[c] = { 7: r[1], 8: r[2] };
      var nomes = {};
      r[0].bairros.forEach(function (b) { nomes[b.nome] = b; });
      r[0]._por_nome = nomes;
    });
  }

  /* ---------- 01 panorama ---------- */
  function leitura() {
    var r = D().resumo, t = tipoNome();
    var partes = [];
    if (!rent()) {
      partes.push(L("À venda, " + t + " em São Paulo pedem <b>" + brl(r.m2) + " por m²</b> (mediana), ou <b>" + brl(r.preco, true) + "</b> por imóvel.",
        "For sale, " + t + " in São Paulo ask <b>" + brl(r.m2) + " per m²</b> (median), or <b>" + brl(r.preco, true) + "</b> per home."));
    } else {
      partes.push(L("Para alugar, " + t + " em São Paulo custam <b>" + brl(r.total) + " por mês</b> com condomínio e IPTU (mediana); só o aluguel, " + brl(r.preco) + ".",
        "For rent, " + t + " in São Paulo cost <b>" + brl(r.total) + " a month</b> including condo fee and property tax (median); rent alone, " + brl(r.preco) + "."));
    }
    if (r.var_estoque != null) {
      var sobe = r.var_estoque > 0.5, desce = r.var_estoque < -0.5;
      partes.push(L("A oferta " + (sobe ? "cresceu <b>" + nf(r.var_estoque, { maximumFractionDigits: 1 }) + "%</b>" : desce ? "encolheu <b>" + nf(-r.var_estoque, { maximumFractionDigits: 1 }) + "%</b>" : "ficou estável") + " nas últimas " + st.meta.tendencia_semanas + " semanas" + (sobe ? ", o que dá mais poder a quem " + (rent() ? "aluga" : "compra") : desce ? ", o que favorece quem " + (rent() ? "anuncia" : "vende") : "") + ".",
        "Supply " + (sobe ? "grew <b>" + nf(r.var_estoque, { maximumFractionDigits: 1 }) + "%</b>" : desce ? "shrank <b>" + nf(-r.var_estoque, { maximumFractionDigits: 1 }) + "%</b>" : "held steady") + " over the last " + st.meta.tendencia_semanas + " weeks" + (sobe ? ", which gives " + (rent() ? "renters" : "buyers") + " more leverage" : desce ? ", which favours " + (rent() ? "landlords" : "sellers") : "") + "."));
    }
    partes.push(L("<b>" + r.pct_queda + "%</b> dos anúncios baixaram o preço em " + st.meta.janela_dias + " dias, com redução mediana de <b>" + nf(r.corte, { maximumFractionDigits: 1 }) + "%</b>, e um imóvel fica no ar <b>" + nf(r.dias) + " dias</b> até sair.",
      "<b>" + r.pct_queda + "%</b> of listings cut their price in " + st.meta.janela_dias + " days, by a median <b>" + nf(r.corte, { maximumFractionDigits: 1 }) + "%</b>, and a home stays listed <b>" + nf(r.dias) + " days</b> before leaving."));
    $("leitura").innerHTML = partes.join(" ");
  }

  function kpis() {
    var r = D().resumo, sem = st.meta.tendencia_semanas;
    var itens = [
      [L("Anúncios ativos", "Active listings"), nf(r.ativos), delta(r.var_estoque, true) + " " + L("estoque em " + sem + " sem.", "supply, " + sem + " wks")],
      rent()
        ? [L("Custo mensal", "Monthly cost"), brl(r.total), L("aluguel " + brl(r.preco) + " + condomínio e IPTU", "rent " + brl(r.preco) + " + fees and tax")]
        : [L("Preço mediano", "Median price"), brl(r.preco, true), L("área mediana " + nf(r.area) + " m²", "median area " + nf(r.area) + " m²")],
      [L("Preço por m²", "Price per m²"), brl(r.m2), delta(r.var_m2, false) + " " + L("anúncios novos, " + sem + " sem.", "new listings, " + sem + " wks")],
      [L("Baixaram o preço", "Cut their price"), pctTxt(r.pct_queda), L("redução mediana de " + nf(r.corte, { maximumFractionDigits: 1 }) + "%", "median cut " + nf(r.corte, { maximumFractionDigits: 1 }) + "%")],
      [L("Dias até sair", "Days on market"), nf(r.dias), L(nf(r.saidas) + " saíram em " + st.meta.janela_dias + " dias", nf(r.saidas) + " left in " + st.meta.janela_dias + " days")],
      rent()
        ? [L("Aceitam pets", "Pets allowed"), pctTxt(r.pct_pets), L(pctTxt(r.pct_mobiliado) + " mobiliados", pctTxt(r.pct_mobiliado) + " furnished")]
        : [L("Rentabilidade bruta", "Gross yield"), r.rent == null ? "—" : nf(r.rent, { maximumFractionDigits: 1 }) + L("% a.a.", "% p.a."), L("mediana dos bairros, antes de custos", "median across areas, before costs")]
    ];
    $("kpis").innerHTML = itens.map(function (x) {
      return '<div class="kpi"><div class="lbl">' + x[0] + '</div><div class="val">' + x[1] + '</div><div class="sub">' + x[2] + "</div></div>";
    }).join("");
  }

  function tipologia() {
    var t = D().tipologia, max = Math.max.apply(null, t.map(function (x) { return x.m2; }));
    $("tipologia").innerHTML = "<thead><tr><th>" + L("Tipologia", "Size") + "</th><th>" + L("Ativos", "Active") + "</th><th>" + (rent() ? L("Aluguel", "Rent") : L("Preço", "Price")) +
      "</th><th>" + L("Área", "Area") + "</th><th>R$" + m2u() + "</th><th>" + L("Baixaram", "Cut") + "</th><th>" + L("Dias até sair", "Days on market") + "</th></tr></thead><tbody>" +
      t.map(function (x) {
        return "<tr><td>" + qLabel(x.q) + "</td><td>" + nf(x.ativos) + "</td><td>" + brl(x.preco, true) + "</td><td>" + nf(x.area) + " m²</td><td><span class=\"barcell\"><i style=\"width:" +
          Math.round(70 * x.m2 / max) + 'px"></i>' + brl(x.m2) + "</span></td><td>" + pctTxt(x.pct_queda) + "</td><td>" + nf(x.dias) + "</td></tr>";
      }).join("") + "</tbody>";
  }

  /* ---------- 02 mapa ---------- */
  var ZOOM_FINO = 12.5, tiles;
  function resolucao() { return mapa.getZoom() >= ZOOM_FINO ? 8 : 7; }
  function quebras(vals, n) {
    var s = vals.slice().sort(function (a, b) { return a - b; }), out = [];
    for (var i = 1; i < n; i++) out.push(s[Math.floor((i / n) * (s.length - 1))]);
    return out;
  }
  function fmtMet(v) {
    if (v == null) return "—";
    if (st.met === "m2") return brl(v) + m2u();
    if (st.met === "d") return nf(v) + L(" dias", " days");
    return nf(v) + "%";
  }
  function fmtTick(v) { return st.met === "m2" ? k(v) : st.met === "d" ? nf(v) : nf(v) + "%"; }

  function iniciarMapa() {
    mapa = window.L.map("mapa", { scrollWheelZoom: false, zoomSnap: 0.5, attributionControl: false }).setView([-23.565, -46.65], 11.5);
    mapa.createPane("rotulos");
    mapa.getPane("rotulos").style.zIndex = 650;
    mapa.getPane("rotulos").style.pointerEvents = "none";
    var base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_";
    tiles = window.L.tileLayer(base + "Base/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16, minZoom: 9 }).addTo(mapa);
    window.L.tileLayer(base + "Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16, minZoom: 9, pane: "rotulos" }).addTo(mapa);
    var res = resolucao();
    mapa.on("zoomend", function () { if (resolucao() !== res) { res = resolucao(); desenharMapa(); } });
  }

  function desenharMapa() {
    if (!mapa) iniciarMapa();
    var gs = st.mapas[chave()], geo = gs[resolucao()], met = st.met;
    var cores = css("--seq").split(",").map(function (c) { return c.trim(); });
    var vals = gs[8].features.map(function (f) { return f.properties[met]; }).filter(function (v) { return v != null; });
    var br = quebras(vals, cores.length);
    function cor(v) { var i = 0; while (i < br.length && v > br[i]) i++; return cores[i]; }
    if (camada) mapa.removeLayer(camada);
    camada = window.L.geoJSON(geo, {
      filter: function (f) { return f.properties[met] != null; },
      style: function (f) { return { fillColor: cor(f.properties[met]), fillOpacity: 0.62, stroke: false }; },
      onEachFeature: function (f, l) {
        var p = f.properties;
        l.bindTooltip(function () {
          return '<span class="t">' + esc(p.b || "") + (resolucao() === 7 ? L(" e arredores", " and around") : "") + "</span>" +
            "<b>" + brl(p.m2) + m2u() + "</b> · " + nf(p.n) + L(" anúncios", " listings") + "<br>" +
            (p.d != null ? nf(p.d) + L(" dias até sair", " days on market") + " · " : "") + nf(p.pq) + L("% baixaram", "% cut price");
        }, { sticky: true, direction: "top", className: "tt", offset: [0, -8] });
        l.on("mouseover", function () { l.setStyle({ stroke: true, weight: 2, color: css("--green"), fillOpacity: 0.8 }); });
        l.on("mouseout", function () { camada.resetStyle(l); });
      }
    }).addTo(mapa);

    var titulo = met === "m2" ? L("Preço por m² (mediana)", "Price per m² (median)") : met === "d" ? L("Dias até sair do ar (mediana)", "Days on market (median)") : L("% dos anúncios que baixaram o preço", "% of listings with a price cut");
    $("legenda").innerHTML = "<span>" + titulo + "</span>" +
      '<div><div class="ramp">' + cores.map(function (c) { return '<span style="background:' + c + '"></span>'; }).join("") + "</div>" +
      '<div class="ticks">' + br.map(function (v) { return "<span>" + fmtTick(v) + "</span>"; }).join("") + "</div></div>" +
      '<span class="dim">' + L("faixas com o mesmo número de áreas", "bands hold equal numbers of areas") + "</span>";
  }

  /* ---------- 03 orçamento ---------- */
  function faixas() { return st.meta.faixas[st.kind]; }
  function budPadrao() { var f = faixas(), alvo = rent() ? 4000 : 800000, i = 0; while (i < f.length - 1 && f[i] < alvo) i++; return i; }
  function contaAte(hist, i) { var n = 0; (hist || []).forEach(function (p) { if (p[0] <= i) n += p[1]; }); return n; }
  function totalDe(hist) { var n = 0; (hist || []).forEach(function (p) { n += p[1]; }); return n; }

  function orcamento() {
    var f = faixas(), slider = $("bud");
    slider.max = f.length - 1;
    if (st.bud == null || st.bud > f.length - 1) st.bud = budPadrao();
    slider.value = st.bud;
    var teto = f[st.bud], q = st.budQ;
    $("bud-lbl").textContent = rent() ? L("Aluguel de até", "Rent up to") : L("Preço de até", "Price up to");
    $("bud-val").textContent = brl(teto, !rent());
    $("bud-min").textContent = brl(f[0], true);
    $("bud-max").textContent = brl(f[f.length - 1], true);

    var cidade = 0, cidadeTot = 0, lista = [];
    D().bairros.forEach(function (b) {
      var h = b.hist[q], n = contaAte(h, st.bud), tot = totalDe(h);
      cidade += n; cidadeTot += tot;
      if (n >= 5) lista.push({ b: b, n: n, pct: Math.round(100 * n / tot) });
    });
    lista.sort(st.budSort === "n" ? function (a, b) { return b.n - a.n; } : function (a, b) { return b.pct - a.pct || b.n - a.n; });
    var qtxt = q === "t" ? "" : " " + L("com ", "with ") + qLabel(q).toLowerCase();
    $("bud-big").innerHTML = L("Com até <b>" + brl(teto) + "</b>" + (rent() ? " de aluguel" : "") + " há <b>" + nf(cidade) + " " + tipoNome() + "</b>" + qtxt + " " + (rent() ? "para alugar" : "à venda") + ", <b>" + Math.round(100 * cidade / Math.max(cidadeTot, 1)) + "%</b> da oferta" + (q === "t" ? "" : " dessa tipologia") + ".",
      "Up to <b>" + brl(teto) + "</b>" + (rent() ? " in rent" : "") + " there are <b>" + nf(cidade) + " " + tipoNome() + "</b>" + qtxt + " " + (rent() ? "for rent" : "for sale") + ", <b>" + Math.round(100 * cidade / Math.max(cidadeTot, 1)) + "%</b> of " + (q === "t" ? "supply" : "that size band") + ".");
    var top = lista.slice(0, 12), maxN = top.length ? Math.max.apply(null, top.map(function (x) { return st.budSort === "n" ? x.n : x.pct; })) : 1;
    $("bud-rank").innerHTML = top.map(function (x, i) {
      var b = x.b, sub = q === "t" ? b : (b.quartos[q] || {});
      var w = Math.round(100 * (st.budSort === "n" ? x.n : x.pct) / maxN);
      return '<li><button type="button" data-bairro="' + esc(b.nome) + '"><span class="pos">' + String(i + 1).padStart(2, "0") + '</span><span class="nm">' + esc(b.nome) +
        "<small>" + (sub.area ? L("no bairro: ", "area-wide: ") + nf(sub.area) + " m² · " : "") + (sub.m2 ? brl(sub.m2) + m2u() : "") + "</small></span>" +
        '<span class="vl">' + nf(x.n) + L(" opções", " options") + "<small>" + x.pct + L("% do bairro", "% of the area") + '</small></span><span class="track"><i style="width:' + w + '%"></i></span></button></li>';
    }).join("") || '<li class="dim" style="padding:14px 0">' + L("Nenhum bairro com 5 opções ou mais nessa faixa.", "No neighbourhood has 5 or more options in this range.") + "</li>";
    $("bud-note").textContent = L("Contagem de anúncios ativos com preço pedido até o teto. Bairros com menos de 5 opções não aparecem.", "Count of active listings asking up to the ceiling. Neighbourhoods with fewer than 5 options are hidden.");
  }

  /* ---------- 04 bairros ---------- */
  function colunas() {
    var c = [["nome", L("Bairro", "Neighbourhood")], ["ativos", L("Ativos", "Active")], ["m2", "R$" + m2u()]];
    c.push(rent() ? ["total", L("Custo/mês", "Cost/mo")] : ["preco", L("Preço", "Price")]);
    c.push(["area", L("Área", "Area")], ["pct_queda", L("Baixaram", "Cut")], ["corte", L("Redução", "Cut size")], ["dias", L("Dias", "Days")]);
    if (!rent()) c.push(["rent", L("Rent. bruta", "Gross yield")]);
    return c;
  }
  function norm(s) { return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  function tabela() {
    var cols = colunas(), q = norm($("busca").value.trim()), reg = $("regiao").value, kk = st.sort, dir = st.dir;
    if (!cols.some(function (c) { return c[0] === kk; })) { kk = st.sort = "ativos"; dir = st.dir = -1; }
    var linhas = D().bairros.filter(function (b) { return (!q || norm(b.nome).indexOf(q) >= 0) && (!reg || b.regiao === reg); });
    linhas.sort(function (a, b) {
      var x = a[kk], y = b[kk];
      if (x == null) return 1; if (y == null) return -1;
      return typeof x === "string" ? dir * x.localeCompare(y) : dir * (x - y);
    });
    var head = "<thead><tr>" + cols.map(function (c) {
      return '<th scope="col" data-k="' + c[0] + '" aria-sort="' + (c[0] === kk ? (dir > 0 ? "ascending" : "descending") : "none") + '">' + c[1] + "</th>";
    }).join("") + "</tr></thead>";
    var body = "<tbody>" + linhas.map(function (b) {
      return '<tr data-bairro="' + esc(b.nome) + '" tabindex="0">' + cols.map(function (c) {
        var v = b[c[0]];
        switch (c[0]) {
          case "nome": return "<td>" + esc(v) + (b.regiao && b.regiao !== v ? "<small>" + esc(b.regiao) + "</small>" : "") + "</td>";
          case "ativos": return "<td>" + nf(v) + "</td>";
          case "m2": return "<td>" + brl(v) + '<span class="rng">' + k(b.m2_p25) + "–" + k(b.m2_p75) + "</span></td>";
          case "preco": case "total": return "<td>" + brl(v, c[0] === "preco") + "</td>";
          case "area": return "<td>" + nf(v) + " m²</td>";
          case "pct_queda": return "<td>" + pctTxt(v) + "</td>";
          case "corte": return "<td>" + (v == null ? "—" : nf(v, { maximumFractionDigits: 1 }) + "%") + "</td>";
          case "dias": return "<td>" + nf(v) + "</td>";
          case "rent": return "<td>" + (v == null ? "—" : nf(v, { maximumFractionDigits: 1 }) + "%") + "</td>";
        }
      }).join("") + "</tr>";
    }).join("") + "</tbody>";
    $("tabela").innerHTML = head + body;
  }
  function regioes() {
    var s = $("regiao"), atual = s.value, set = {};
    D().bairros.forEach(function (b) { if (b.regiao) set[b.regiao] = 1; });
    s.innerHTML = '<option value="">' + L("Todas as regiões", "All areas") + "</option>" +
      Object.keys(set).sort().map(function (r) { return "<option" + (r === atual ? " selected" : "") + ">" + esc(r) + "</option>"; }).join("");
  }

  /* ---------- ficha do bairro ---------- */
  var aberto = null, focoAntes = null;
  function vsCidade(v, c) {
    if (v == null || c == null) return "";
    var d = Math.round(100 * (v / c - 1));
    return (d > 0 ? "+" : "") + d + L("% vs. cidade", "% vs. city");
  }
  function ficha(nome) {
    var b = D()._por_nome[nome], r = D().resumo;
    if (!b) { fechar(); return; }
    aberto = nome;
    var h = b.hist.t || [], f = faixas(), maxI = 0, minI = f.length, maxN = 1;
    h.forEach(function (p) { maxI = Math.max(maxI, p[0]); minI = Math.min(minI, p[0]); maxN = Math.max(maxN, p[1]); });
    var cont = {}; h.forEach(function (p) { cont[p[0]] = p[1]; });
    var barras = "";
    for (var i = minI; i <= maxI; i++) {
      var n = cont[i] || 0, de = i === 0 ? 0 : f[i - 1], ate = f[Math.min(i, f.length - 1)];
      barras += '<i style="height:' + Math.max(1, Math.round(100 * n / maxN)) + '%" title="' + brl(de, true) + "–" + brl(ate, true) + ": " + nf(n) + '"></i>';
    }
    var qs = ["1", "2", "3", "4"].filter(function (q) { return b.quartos[q]; });
    var html = '<div class="kicker">' + esc(b.regiao || "") + " · " + (rent() ? L("aluguel", "rent") : L("venda", "sale")) + " · " + tipoNome() + "</div>" +
      '<h2 id="dr-nome">' + esc(b.nome) + "</h2>" +
      '<div class="kpis">' +
      kpiMini(L("Preço por m²", "Price per m²"), brl(b.m2), vsCidade(b.m2, r.m2)) +
      kpiMini(rent() ? L("Custo mensal", "Monthly cost") : L("Preço mediano", "Median price"), rent() ? brl(b.total) : brl(b.preco, true), rent() ? L("aluguel ", "rent ") + brl(b.preco) : L("área ", "area ") + nf(b.area) + " m²") +
      kpiMini(L("Baixaram o preço", "Cut their price"), pctTxt(b.pct_queda), b.corte != null ? L("redução mediana ", "median cut ") + nf(b.corte, { maximumFractionDigits: 1 }) + "%" : "") +
      kpiMini(L("Dias até sair", "Days on market"), nf(b.dias), b.dias != null && r.dias ? (b.dias < r.dias ? L("mais rápido que a cidade", "faster than the city") : b.dias > r.dias ? L("mais lento que a cidade", "slower than the city") : L("igual à cidade", "same as the city")) : "") +
      (rent()
        ? kpiMini(L("Mobiliados", "Furnished"), pctTxt(b.pct_mobiliado), L("aceitam pets: ", "pets allowed: ") + pctTxt(b.pct_pets))
        : kpiMini(L("Rentabilidade bruta", "Gross yield"), b.rent == null ? "—" : nf(b.rent, { maximumFractionDigits: 1 }) + L("% a.a.", "% p.a."), r.rent != null ? L("cidade: ", "city: ") + nf(r.rent, { maximumFractionDigits: 1 }) + "%" : "")) +
      (rent()
        ? kpiMini(L("Anúncios ativos", "Active listings"), nf(b.ativos), b.saidas ? nf(b.saidas) + L(" saíram em 90 dias", " left in 90 days") : "")
        : kpiMini(L("Condomínio", "Condo fee"), brl(b.condo), L(nf(b.ativos) + " anúncios ativos", nf(b.ativos) + " active listings"))) +
      "</div>" +
      (qs.length ? "<h3>" + L("Por número de quartos", "By bedrooms") + '</h3><div class="tbl-wrap"><table><thead><tr><th></th><th>' + L("Ativos", "Active") + "</th><th>" + (rent() ? L("Aluguel", "Rent") : L("Preço", "Price")) + "</th><th>" + L("Área", "Area") + "</th><th>R$" + m2u() + "</th><th>" + L("Dias", "Days") + "</th></tr></thead><tbody>" +
        qs.map(function (q) { var x = b.quartos[q]; return "<tr><td>" + qLabel(q) + "</td><td>" + nf(x.ativos) + "</td><td>" + brl(x.preco, true) + "</td><td>" + nf(x.area) + " m²</td><td>" + brl(x.m2) + "</td><td>" + nf(x.dias) + "</td></tr>"; }).join("") +
        "</tbody></table></div>" : "") +
      "<h3>" + L("Faixa de preço dos anúncios", "Asking price spread") + '</h3><div class="histo">' + barras + '</div><div class="histo-x"><span>' + brl(minI === 0 ? 0 : f[minI - 1], true) + "</span><span>" + brl(f[Math.min(maxI, f.length - 1)], true) + (maxI >= f.length ? "+" : "") + "</span></div>" +
      "<h3>" + L("Faixa de preço por m²", "Price per m² range") + "</h3><p>" + L("Metade dos anúncios pede entre <b>" + brl(b.m2_p25) + "</b> e <b>" + brl(b.m2_p75) + "</b>" + m2u() + ".", "Half of the listings ask between <b>" + brl(b.m2_p25) + "</b> and <b>" + brl(b.m2_p75) + "</b>" + m2u() + ".") + "</p>" +
      (b.pct_saiu_corte != null ? "<p>" + L("Dos que saíram do ar nos últimos 90 dias, <b>" + b.pct_saiu_corte + "%</b> tinham baixado o preço antes.", "Of the listings that left in the last 90 days, <b>" + b.pct_saiu_corte + "%</b> had cut their price first.") + "</p>" : "");
    $("dr-body").innerHTML = html;
    if (!$("drawer").classList.contains("open")) focoAntes = document.activeElement;
    $("drawer").classList.add("open"); $("scrim").classList.add("open");
    $("dr-x").focus();
  }
  function kpiMini(l, v, s) { return '<div class="kpi"><div class="lbl">' + l + '</div><div class="val">' + v + '</div><div class="sub">' + (s || "&nbsp;") + "</div></div>"; }
  function fechar() {
    aberto = null;
    $("drawer").classList.remove("open"); $("scrim").classList.remove("open");
    if (focoAntes) { try { focoAntes.focus(); } catch (e) {} focoAntes = null; }
  }

  /* ---------- gráficos ---------- */
  function baseOpts(yFmt, xFmt) {
    var dim = css("--mute-l"), grid = "rgba(20,20,18,.08)";
    var o = {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: dim, boxWidth: 14, boxHeight: 2, font: { family: "Inter", size: 12 } } },
        tooltip: {
          backgroundColor: "#fbf9f4", titleColor: "#141412", bodyColor: "#5f5a51", borderColor: "rgba(20,20,18,.22)", borderWidth: 1,
          cornerRadius: 2, padding: 10, titleFont: { family: "Inter", weight: "500" }, bodyFont: { family: "Inter" },
          callbacks: { label: function (c) { return " " + c.dataset.label + ": " + yFmt(c.parsed.y); } }
        }
      },
      scales: {
        x: { ticks: { color: dim, maxRotation: 0, autoSkipPadding: 18, font: { family: "Inter", size: 11 } }, grid: { display: false }, border: { color: "rgba(20,20,18,.2)" } },
        y: { ticks: { color: dim, font: { family: "Inter", size: 11 }, callback: function (v) { return yFmt(v); } }, grid: { color: grid }, border: { display: false } }
      }
    };
    if (xFmt) o.scales.x.ticks.callback = xFmt;
    return o;
  }
  function linha(cor) { return { borderColor: cor, backgroundColor: cor, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.25, spanGaps: true }; }
  function semana(s) { return new Date(s + "T12:00:00").toLocaleDateString(en() ? "en-US" : "pt-BR", { day: "2-digit", month: "short" }); }
  function novo(id, cfg) { if (charts[id]) charts[id].destroy(); charts[id] = new window.Chart($(id), cfg); }

  function tendencia() {
    var s = D().serie, labels = s.map(function (x) { return semana(x.semana); });
    var o1 = baseOpts(function (v) { return k(v); }); o1.plugins.legend.display = false; o1.scales.y.grace = "5%";
    novo("ch-estoque", { type: "line", data: { labels: labels, datasets: [Object.assign({ label: L("Ativos", "Active"), data: s.map(function (x) { return x.estoque; }) }, linha(css("--s1")))] }, options: o1 });
    var o2 = baseOpts(function (v) { return brl(v, true); }); o2.plugins.legend.display = false; o2.scales.y.grace = "10%";
    novo("ch-m2", { type: "line", data: { labels: labels, datasets: [Object.assign({ label: "R$" + m2u(), data: s.map(function (x) { return x.m2_novos; }) }, linha(css("--s1")))] }, options: o2 });
    novo("ch-fluxo", {
      type: "line", data: { labels: labels, datasets: [
        Object.assign({ label: L("Entraram", "Listed"), data: s.map(function (x) { return x.novos; }) }, linha(css("--s1"))),
        Object.assign({ label: L("Saíram do ar", "Left the market"), data: s.map(function (x) { return x.saidas; }) }, linha(css("--s2")))
      ] }, options: baseOpts(function (v) { return nf(v); })
    });
  }

  function negociacao() {
    var r = D().resumo, h = D().cortes_hist, cortes = st.meta.cortes;
    var labels = h.map(function (_, i) { return i === 0 ? L("até ", "up to ") + cortes[0] + "%" : i === cortes.length ? cortes[cortes.length - 1] + "%+" : cortes[i - 1] + "–" + cortes[i] + "%"; });
    var tot = h.reduce(function (a, b) { return a + b; }, 0) || 1;
    var o = baseOpts(function (v) { return nf(v) + "%"; });
    o.plugins.legend.display = false; o.interaction = { mode: "nearest", intersect: true };
    novo("ch-cortes", { type: "bar", data: { labels: labels, datasets: [{ label: L("dos que baixaram", "of those who cut"), data: h.map(function (n) { return Math.round(1000 * n / tot) / 10; }), backgroundColor: css("--s1"), borderRadius: { topLeft: 2, topRight: 2 }, maxBarThickness: 46 }] }, options: o });

    var bs = D().bairros.filter(function (b) { return b.dias != null; });
    var maxA = Math.max.apply(null, bs.map(function (b) { return b.ativos; }));
    var o2 = baseOpts(function (v) { return nf(v); }, function (v) { return k(v); });
    o2.plugins.legend.display = false; o2.interaction = { mode: "nearest", intersect: true };
    o2.scales.x.type = "linear"; o2.scales.x.title = { display: true, text: "R$" + m2u(), color: css("--mute-l"), font: { family: "Inter", size: 11 } };
    o2.scales.x.grid = { color: "rgba(20,20,18,.06)" };
    o2.scales.y.title = { display: true, text: L("dias até sair", "days on market"), color: css("--mute-l"), font: { family: "Inter", size: 11 } };
    o2.plugins.tooltip.callbacks = {
      title: function (it) { return it[0].raw.nome; },
      label: function (c) { return " " + brl(c.raw.x) + m2u() + " · " + nf(c.raw.y) + L(" dias · ", " days · ") + nf(c.raw.n) + L(" anúncios", " listings"); }
    };
    o2.onClick = function (e, els) { if (els.length) ficha(els[0].element.$context.raw.nome); };
    novo("ch-liq", { type: "bubble", data: { datasets: [{ data: bs.map(function (b) { return { x: b.m2, y: b.dias, r: 2 + 10 * Math.sqrt(b.ativos / maxA), nome: b.nome, n: b.ativos }; }), backgroundColor: "rgba(28,123,90,.35)", borderColor: css("--s1"), borderWidth: 1, hoverBackgroundColor: css("--s2") }] }, options: o2 });

    $("neg-p").innerHTML = L("Quanto espaço há para negociar e quão rápido os imóveis saem. Em " + st.meta.janela_dias + " dias, <b>" + r.pct_queda + "%</b> dos anúncios ativos baixaram o preço.",
      "How much room there is to negotiate and how fast homes go. In " + st.meta.janela_dias + " days, <b>" + r.pct_queda + "%</b> of active listings cut their price.");
    $("neg-stats").innerHTML =
      "<div><b>" + nf(r.corte, { maximumFractionDigits: 1 }) + "%</b>" + L("redução mediana de quem baixou", "median cut among those who cut") + "</div>" +
      "<div><b>" + pctTxt(r.pct_saiu_corte) + "</b>" + L("dos que saíram do ar tinham baixado antes", "of those that left had cut first") + "</div>" +
      "<div><b>" + nf(r.dias) + "</b>" + L("dias, em mediana, até sair do ar", "median days until leaving the market") + "</div>";
  }

  function extra() {
    var t = document.querySelectorAll(".extra-toc");
    if (!rent()) {
      t.forEach(function (e) { e.textContent = L("Investimento", "Investment"); });
      $("extra-h").textContent = L("Para quem compra para alugar", "If you buy to let");
      $("extra-p").textContent = L("Rentabilidade bruta: o aluguel de um ano dividido pelo preço, comparando imóveis com o mesmo número de quartos no mesmo bairro. Antes de vacância, impostos, condomínio e manutenção.",
        "Gross yield: a year's rent divided by the price, comparing homes with the same number of bedrooms in the same neighbourhood. Before vacancy, tax, condo fees and upkeep.");
      var bs = D().bairros.filter(function (b) { return b.rent != null && b.ativos >= 50; });
      var alto = bs.slice().sort(function (a, b) { return b.rent - a.rent; }).slice(0, 12);
      var baixo = bs.slice().sort(function (a, b) { return a.rent - b.rent; }).slice(0, 12);
      var max = alto.length ? alto[0].rent : 1;
      function bloco(titulo, nota, lista, cls) {
        return '<div class="card"><h3>' + titulo + '</h3><p class="note">' + nota + '</p><div class="bars ' + cls + '">' + lista.map(function (b) {
          return '<div class="row"><span>' + esc(b.nome) + '</span><span class="trk"><i style="width:' + Math.round(100 * b.rent / max) + '%"></i></span><b>' + nf(b.rent, { maximumFractionDigits: 1 }) + "%</b></div>";
        }).join("") + "</div></div>";
      }
      $("extra-body").innerHTML = bs.length === 0 ? "" :
        bloco(L("Maior rentabilidade", "Highest yield"), L("Bairros com pelo menos 50 anúncios à venda. O aluguel rende mais sobre o preço pedido.", "Areas with at least 50 listings for sale. Rent returns the most on the asking price."), alto, "") +
        bloco(L("Menor rentabilidade", "Lowest yield"), L("Onde o preço pesa mais sobre o aluguel: quem compra ali para investir aposta mais em valorização do que em renda.", "Where price weighs most against rent: investors there bet more on appreciation than on income."), baixo, "gold");
    } else {
      t.forEach(function (e) { e.textContent = L("Mobiliado", "Furnished"); });
      $("extra-h").textContent = L("Mobiliado ou vazio", "Furnished or unfurnished");
      $("extra-p").textContent = L("Quanto a mais custa o m² de um imóvel mobiliado, comparando o mesmo número de quartos, e o quanto a oferta aceita pets.",
        "How much more a furnished home costs per m², comparing the same number of bedrooms, and how much of the supply allows pets.");
      var m = D().mobiliado || [], r = D().resumo, maxP = Math.max.apply(null, m.map(function (x) { return x.pct; }).concat([1]));
      var top = D().bairros.filter(function (b) { return b.ativos >= 50; }).sort(function (a, b) { return a.total - b.total; }).slice(0, 12);
      var maxT = Math.max.apply(null, top.map(function (b) { return b.total; }).concat([1]));
      $("extra-body").innerHTML =
        '<div class="card"><h3>' + L("Prêmio do mobiliado", "Furnished premium") + '</h3><p class="note">' + L("R$/m² do mobiliado acima do não mobiliado, por tipologia.", "Furnished R$/m² above unfurnished, by size band.") + '</p><div class="bars gold">' +
        m.map(function (x) { return '<div class="row"><span>' + qLabel(x.q) + '</span><span class="trk"><i style="width:' + Math.round(100 * x.pct / maxP) + '%"></i></span><b>+' + x.pct + "%</b></div>"; }).join("") +
        '</div><div class="stat-line"><div><b>' + pctTxt(r.pct_mobiliado) + "</b>" + L("da oferta é mobiliada", "of supply is furnished") + "</div><div><b>" + pctTxt(r.pct_pets) + "</b>" + L("aceita pets", "allows pets") + "</div></div></div>" +
        '<div class="card"><h3>' + L("Custo mensal mais baixo", "Lowest monthly cost") + '</h3><p class="note">' + L("Aluguel + condomínio + IPTU, mediana. Bairros com pelo menos 50 anúncios.", "Rent + condo fee + property tax, median. Areas with at least 50 listings.") + '</p><div class="bars">' +
        top.map(function (b) { return '<div class="row"><span>' + esc(b.nome) + '</span><span class="trk"><i style="width:' + Math.round(100 * b.total / maxT) + '%"></i></span><b>R$ ' + k(b.total) + "</b></div>"; }).join("") + "</div></div>";
    }
  }

  function meta() {
    var m = st.meta;
    document.querySelectorAll(".k-min").forEach(function (e) { e.textContent = m.k_min; });
    document.querySelectorAll(".k-sub").forEach(function (e) { e.textContent = m.k_sub; });
    document.querySelectorAll(".k-hex").forEach(function (e) { e.textContent = m.k_hex; });
    document.querySelectorAll(".janela").forEach(function (e) { e.textContent = m.janela_dias; });
    document.querySelectorAll(".tend").forEach(function (e) { e.textContent = m.tendencia_semanas; });
    document.querySelectorAll(".desde").forEach(function (e) { e.textContent = dataLonga(m.historico_desde + "T12:00:00"); });
    var g = new Date(m.gerado_em).toLocaleString(en() ? "en-GB" : "pt-BR", { dateStyle: "long", timeStyle: "short" });
    document.querySelectorAll(".gerado").forEach(function (e) { e.textContent = g; });
    $("stamp").textContent = L("Atualizado em ", "Updated ") + g;
  }

  /* ---------- orquestração ---------- */
  function tudo() {
    leitura(); kpis(); tipologia(); desenharMapa(); orcamento(); regioes(); tabela(); negociacao(); extra(); tendencia(); meta();
    if (aberto) ficha(aberto);
  }
  function aplicar() {
    document.querySelectorAll("[data-kind]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.kind === st.kind)); });
    document.querySelectorAll("[data-tipo]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.tipo === st.tipo)); });
    return carregar().then(tudo).catch(falhou);
  }
  function falhou(e) { $("erro").hidden = false; console.error(e); }
  function grupo(sel, attr, fn) {
    document.querySelectorAll(sel).forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll(sel).forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        fn(b.getAttribute(attr));
      });
    });
  }

  grupo("[data-kind]", "data-kind", function (v) { if (v !== st.kind) { st.kind = v; st.bud = null; aplicar(); } });
  grupo("[data-tipo]", "data-tipo", function (v) { if (v !== st.tipo) { st.tipo = v; aplicar(); } });
  grupo("[data-met]", "data-met", function (v) { st.met = v; desenharMapa(); });
  grupo("#bud-q [data-q]", "data-q", function (v) { st.budQ = v; orcamento(); });
  grupo("[data-bs]", "data-bs", function (v) { st.budSort = v; orcamento(); });
  $("bud").addEventListener("input", function () { st.bud = +this.value; orcamento(); });
  $("busca").addEventListener("input", tabela);
  $("regiao").addEventListener("change", tabela);
  $("tabela").addEventListener("click", function (e) {
    var th = e.target.closest("th");
    if (th) {
      var nk = th.getAttribute("data-k");
      st.dir = nk === st.sort ? -st.dir : (nk === "nome" ? 1 : -1);
      st.sort = nk; tabela(); return;
    }
    var tr = e.target.closest("tr[data-bairro]");
    if (tr) ficha(tr.getAttribute("data-bairro"));
  });
  $("tabela").addEventListener("keydown", function (e) {
    var tr = e.target.closest("tr[data-bairro]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); ficha(tr.getAttribute("data-bairro")); }
  });
  $("bud-rank").addEventListener("click", function (e) { var b = e.target.closest("[data-bairro]"); if (b) ficha(b.getAttribute("data-bairro")); });
  $("dr-x").addEventListener("click", fechar);
  $("scrim").addEventListener("click", fechar);

  // menu e idioma, iguais ao portfólio
  var mb = document.querySelector(".menu-btn");
  function fecharMenu() { root.classList.remove("menu-open"); mb.setAttribute("aria-expanded", "false"); }
  mb.addEventListener("click", function () { var o = !root.classList.contains("menu-open"); root.classList.toggle("menu-open", o); mb.setAttribute("aria-expanded", String(o)); });
  addEventListener("keydown", function (e) { if (e.key === "Escape") { fecharMenu(); fechar(); } });
  function setLang(l) {
    root.setAttribute("data-lang", l); root.lang = l === "pt" ? "pt-BR" : "en";
    try { localStorage.setItem("lang", l); } catch (e) {}
    document.querySelectorAll("[data-set-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.setLang === l)); });
  }
  document.querySelectorAll("[data-set-lang]").forEach(function (b) {
    b.addEventListener("click", function () { setLang(b.dataset.setLang); if (st.meta) tudo(); });
  });
  setLang(en() ? "en" : "pt");

  // seção atual destacada no índice
  if ("IntersectionObserver" in window) {
    var links = {};
    document.querySelectorAll(".toc a").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var obs = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting || !links[e.target.id]) return;
        Object.keys(links).forEach(function (id) { links[id].classList.toggle("on", id === e.target.id); });
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    document.querySelectorAll("main .sec").forEach(function (sec) { obs.observe(sec); });
  }

  // estrelas e forks do repositório; se a API do GitHub falhar, os botões ficam sem número
  fetch("https://api.github.com/repos/victoorsaad/sp-radar", { headers: { Accept: "application/vnd.github+json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (g) {
      if (!g) return;
      document.querySelectorAll(".gh-n").forEach(function (e) { e.textContent = nf(g.stargazers_count); });
      document.querySelectorAll(".gh-f").forEach(function (e) { e.textContent = nf(g.forks_count); });
      document.querySelectorAll(".gh-stars, .gh-forks").forEach(function (e) { e.hidden = false; });
      document.querySelectorAll(".gh-upd").forEach(function (e) {
        e.textContent = L("atualizado em ", "updated ") + new Date(g.pushed_at).toLocaleDateString(en() ? "en-GB" : "pt-BR", { day: "numeric", month: "short" });
        e.hidden = false;
      });
    }).catch(function () {});

  get("data/meta.json").then(function (m) { st.meta = m; return aplicar(); }).catch(falhou);
})();
