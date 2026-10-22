/* Radar imobiliário de SP — lê os JSON agregados de data/ e desenha a página. */
(function () {
  "use strict";
  var root = document.documentElement;
  var estado = { kind: "sale", sort: "ativos", dir: -1, dados: {}, mapas: {}, meta: null };
  var mapa, camada, charts = {};

  function lang() { return root.getAttribute("data-lang") === "en" ? "en" : "pt"; }
  function L(pt, en) { return lang() === "en" ? en : pt; }
  function cssVar(n) { return getComputedStyle(root).getPropertyValue(n).trim(); }

  function fmt(v, opts) {
    if (v === null || v === undefined) return "—";
    return new Intl.NumberFormat(lang() === "en" ? "en-US" : "pt-BR", opts || {}).format(v);
  }
  function brl(v, compacto) {
    if (v === null || v === undefined) return "—";
    if (compacto && v >= 1e6) return "R$ " + fmt(v / 1e6, { maximumFractionDigits: 2 }) + L(" mi", "M");
    if (compacto && v >= 1e4) return "R$ " + fmt(Math.round(v / 1e3)) + L(" mil", "k");
    return "R$ " + fmt(v, { maximumFractionDigits: v < 100 ? 1 : 0 });
  }
  function compacto(v) { return v >= 1000 ? fmt(v / 1000, { maximumFractionDigits: 1 }) + "k" : fmt(v); }
  function m2(v) { return v === null || v === undefined ? "—" : brl(v) + (estado.kind === "rent" ? L("/m²/mês", "/m²/mo") : "/m²"); }

  function get(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }

  function carregar(kind) {
    if (estado.dados[kind]) return Promise.resolve();
    return Promise.all([get("data/" + kind + ".json"), get("data/" + kind + "-mapa7.json"), get("data/" + kind + "-mapa8.json")]).then(function (r) {
      estado.dados[kind] = r[0];
      estado.mapas[kind] = { 7: r[1], 8: r[2] };
    });
  }

  /* ---------- KPIs ---------- */
  function kpis() {
    var r = estado.dados[estado.kind].resumo, rent = estado.kind === "rent";
    var itens = [
      [L("Anúncios ativos", "Active listings"), fmt(r.ativos), L(r.bairros_publicados + " bairros publicados", r.bairros_publicados + " neighbourhoods shown")],
      [L(rent ? "Aluguel mediano" : "Preço mediano", rent ? "Median rent" : "Median price"), brl(r.preco, true), rent ? L("total com condomínio e IPTU: ", "with condo fee and property tax: ") + brl(r.total) : L("preço pedido", "asking price")],
      [L(estado.kind === "rent" ? "R$/m² mediano por mês" : "R$/m² mediano", estado.kind === "rent" ? "Median R$/m² per month" : "Median R$/m²"), brl(r.m2), L("cidade inteira", "whole city")],
      [L("Baixaram o preço", "Cut their price"), r.pct_queda_90d === null ? "—" : r.pct_queda_90d + "%", L("dos ativos, nos últimos 90 dias", "of active listings, last 90 days")],
      [L("Tempo até sair do ar", "Time to leave market"), r.dias_ate_sair === null ? "—" : fmt(r.dias_ate_sair) + L(" dias", " days"), L("mediana · " + fmt(r.saidas_90d) + " saídas em 90 dias", "median · " + fmt(r.saidas_90d) + " exits in 90 days")]
    ];
    document.getElementById("kpis").innerHTML = itens.map(function (k) {
      return '<div class="kpi"><div class="lbl">' + k[0] + '</div><div class="val">' + k[1] + '</div><div class="sub">' + k[2] + "</div></div>";
    }).join("");
  }

  /* ---------- mapa ---------- */
  function quebras(vals, n) {
    var s = vals.slice().sort(function (a, b) { return a - b; }), out = [];
    for (var i = 1; i < n; i++) out.push(s[Math.floor((i / n) * (s.length - 1))]);
    return out;
  }

  // Visão da cidade: hexágonos de ~5 km²; a partir do zoom 12,5, de ~0,7 km². As cores usam as
  // mesmas faixas nas duas escalas para não mudar de tom ao aproximar.
  var ZOOM_FINO = 12.5, rotulosLayer, tileLayer;

  function resolucao() { return mapa.getZoom() >= ZOOM_FINO ? 8 : 7; }

  function desenharMapa() {
    if (!mapa) mapa = L_map();
    var geos = estado.mapas[estado.kind], geo = geos[resolucao()];
    var cores = cssVar("--seq").split(",").map(function (c) { return c.trim(); });
    var br = quebras(geos[8].features.map(function (f) { return f.properties.m2; }), cores.length);
    function cor(v) { var i = 0; while (i < br.length && v > br[i]) i++; return cores[i]; }
    if (camada) mapa.removeLayer(camada);
    camada = window.L.geoJSON(geo, {
      style: function (f) {
        return { fillColor: cor(f.properties.m2), fillOpacity: 0.55, stroke: false };
      },
      onEachFeature: function (f, l) {
        var p = f.properties;
        l.bindTooltip(
          (p.b ? '<span class="tt-b">' + esc(p.b) + (resolucao() === 7 ? L(" e arredores", " and around") : "") + "</span><br>" : "") +
          "<b>" + m2(p.m2) + "</b><br>" + fmt(p.n) + L(" anúncios ativos", " active listings"),
          { sticky: true, direction: "top" }
        );
        l.on("mouseover", function () { l.setStyle({ stroke: true, weight: 2, color: cssVar("--text"), fillOpacity: 0.75 }); });
        l.on("mouseout", function () { camada.resetStyle(l); });
      }
    }).addTo(mapa);

    var ticks = [""].concat(br.map(compacto));
    document.getElementById("legenda").innerHTML =
      "<span>" + L("R$/m² mediano", "Median R$/m²") + "</span>" +
      '<div><div class="ramp">' + cores.map(function (c) { return '<span style="background:' + c + '"></span>'; }).join("") + "</div>" +
      '<div class="ticks">' + ticks.map(function (t) { return "<span>" + t + "</span>"; }).join("") + "</div></div>" +
      '<span class="dim">' + L("aproxime para ver hexágonos menores, de ~0,7 km²", "zoom in for smaller ~0.7 km² hexagons") + "</span>";
  }

  function L_map() {
    var m = window.L.map("mapa", { scrollWheelZoom: false, zoomSnap: 0.5, attributionControl: false })
      .setView([-23.565, -46.65], 11.5);
    // Nomes de bairros e vias por cima dos hexágonos, sem capturar o mouse.
    m.createPane("rotulos");
    m.getPane("rotulos").style.zIndex = 650;
    m.getPane("rotulos").style.pointerEvents = "none";
    tiles(m);
    var res = 7;
    m.on("zoomend", function () { if (resolucao() !== res) { res = resolucao(); desenharMapa(); } });
    return m;
  }
  function tiles(m) {
    var tom = getComputedStyle(root).colorScheme === "dark" ? "Dark" : "Light";
    var base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_" + tom + "_Gray_";
    if (tileLayer) m.removeLayer(tileLayer);
    if (rotulosLayer) m.removeLayer(rotulosLayer);
    tileLayer = window.L.tileLayer(base + "Base/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16, minZoom: 9 }).addTo(m);
    rotulosLayer = window.L.tileLayer(base + "Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16, minZoom: 9, pane: "rotulos" }).addTo(m);
  }

  /* ---------- gráficos ---------- */
  function baseOpts(yFmt) {
    var dim = cssVar("--text-dim"), grid = cssVar("--border");
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: dim, boxWidth: 12, boxHeight: 2, font: { family: "Inter", size: 12 } } },
        tooltip: { callbacks: { label: function (c) { return " " + c.dataset.label + ": " + yFmt(c.parsed.y); } } }
      },
      scales: {
        x: { ticks: { color: dim, maxRotation: 0, autoSkipPadding: 16, font: { size: 11 } }, grid: { display: false }, border: { color: grid } },
        y: { ticks: { color: dim, font: { size: 11 }, callback: function (v) { return yFmt(v); } }, grid: { color: grid }, border: { display: false } }
      }
    };
  }
  function rotSemana(s) {
    var d = new Date(s + "T12:00:00");
    return d.toLocaleDateString(lang() === "en" ? "en-US" : "pt-BR", { day: "2-digit", month: "short" });
  }
  function graficos() {
    var serie = estado.dados[estado.kind].serie;
    var labels = serie.map(function (s) { return rotSemana(s.semana); });
    Object.keys(charts).forEach(function (k) { charts[k].destroy(); });
    var linha = { borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.25 };
    charts.fluxo = new window.Chart(document.getElementById("ch-fluxo"), {
      type: "line",
      data: { labels: labels, datasets: [
        Object.assign({ label: L("Novos", "New"), data: serie.map(function (s) { return s.novos; }), borderColor: cssVar("--s1"), backgroundColor: cssVar("--s1") }, linha),
        Object.assign({ label: L("Saíram do ar", "Left the market"), data: serie.map(function (s) { return s.saidas; }), borderColor: cssVar("--s2"), backgroundColor: cssVar("--s2") }, linha)
      ] },
      options: baseOpts(function (v) { return fmt(v); })
    });
    var o = baseOpts(function (v) { return brl(v, true); });
    o.plugins.legend.display = false;
    o.scales.y.grace = "10%";
    charts.m2 = new window.Chart(document.getElementById("ch-m2"), {
      type: "line",
      data: { labels: labels, datasets: [
        Object.assign({ label: L("R$/m² mediano", "Median R$/m²"), data: serie.map(function (s) { return s.m2_novos; }), borderColor: cssVar("--s1"), backgroundColor: cssVar("--s1"), spanGaps: true }, linha)
      ] },
      options: o
    });
  }

  /* ---------- tabela ---------- */
  function colunas() {
    var c = [
      ["nome", L("Bairro", "Neighbourhood")],
      ["ativos", L("Ativos", "Active")],
      ["m2", estado.kind === "rent" ? L("R$/m²/mês", "R$/m²/mo") : "R$/m²"],
      ["preco", estado.kind === "rent" ? L("Aluguel", "Rent") : L("Preço", "Price")]
    ];
    if (estado.kind === "rent") c.push(["total", L("Total/mês", "Total/mo")]);
    c.push(["area", L("Área", "Area m²")], ["pct_queda_90d", L("Baixaram %", "Cut %")], ["dias_ate_sair", L("Dias até sair", "Days to exit")]);
    return c;
  }
  function tabela() {
    var d = estado.dados[estado.kind], cols = colunas();
    var q = document.getElementById("busca").value.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    var reg = document.getElementById("regiao").value;
    var linhas = d.bairros.filter(function (b) {
      var n = b.nome.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
      return (!q || n.indexOf(q) >= 0) && (!reg || b.regiao === reg);
    });
    var k = estado.sort, dir = estado.dir;
    linhas.sort(function (a, b) {
      var x = a[k], y = b[k];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return typeof x === "string" ? dir * x.localeCompare(y) : dir * (x - y);
    });
    var head = "<thead><tr>" + cols.map(function (c) {
      var s = c[0] === k ? (dir > 0 ? "ascending" : "descending") : "none";
      return '<th scope="col" data-k="' + c[0] + '" aria-sort="' + s + '">' + c[1] + "</th>";
    }).join("") + "</tr></thead>";
    var body = "<tbody>" + linhas.map(function (b) {
      return "<tr>" + cols.map(function (c) {
        var v = b[c[0]];
        switch (c[0]) {
          case "nome": return '<td class="nome">' + esc(v) + "</td>";
          case "ativos": return "<td>" + fmt(v) + "</td>";
          case "m2": return "<td>" + brl(v) + '<span class="rng">' + brl(b.m2_p25).replace("R$ ", "") + "–" + brl(b.m2_p75).replace("R$ ", "") + "</span></td>";
          case "preco": case "total": return "<td>" + brl(v, true) + "</td>";
          case "area": return "<td>" + fmt(v) + " m²</td>";
          case "pct_queda_90d": return "<td>" + (v === null ? "—" : v + "%") + "</td>";
          case "dias_ate_sair": return "<td>" + fmt(v) + "</td>";
        }
      }).join("") + "</tr>";
    }).join("") + "</tbody>";
    var t = document.getElementById("tabela");
    t.innerHTML = head + body;
    t.querySelectorAll("th").forEach(function (th) {
      th.addEventListener("click", function () {
        var nk = th.getAttribute("data-k");
        estado.dir = nk === estado.sort ? -estado.dir : (nk === "nome" || nk === "regiao" ? 1 : -1);
        estado.sort = nk;
        tabela();
      });
    });
  }
  function regioes() {
    var s = document.getElementById("regiao"), atual = s.value, set = {};
    estado.dados[estado.kind].bairros.forEach(function (b) { if (b.regiao) set[b.regiao] = 1; });
    s.innerHTML = '<option value="">' + L("Todas as regiões", "All areas") + "</option>" +
      Object.keys(set).sort().map(function (r) { return '<option' + (r === atual ? " selected" : "") + ">" + esc(r) + "</option>"; }).join("");
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function meta() {
    var m = estado.meta;
    document.querySelectorAll(".k-min").forEach(function (e) { e.textContent = m.k_min; });
    document.querySelectorAll(".k-hex").forEach(function (e) { e.textContent = m.k_hex; });
    var loc = lang() === "en" ? "en-GB" : "pt-BR";
    document.querySelectorAll(".desde").forEach(function (e) { e.textContent = new Date(m.historico_desde + "T12:00:00").toLocaleDateString(loc, { day: "numeric", month: "long", year: "numeric" }); });
    document.querySelectorAll(".gerado").forEach(function (e) { e.textContent = new Date(m.gerado_em).toLocaleString(loc, { dateStyle: "long", timeStyle: "short" }); });
  }

  function tudo() { kpis(); desenharMapa(); graficos(); regioes(); tabela(); meta(); }

  function trocar(kind) {
    estado.kind = kind;
    document.querySelectorAll(".seg button").forEach(function (b) { b.setAttribute("aria-selected", b.getAttribute("data-kind") === kind); });
    carregar(kind).then(tudo);
  }

  document.querySelectorAll(".seg button").forEach(function (b) {
    b.addEventListener("click", function () { trocar(b.getAttribute("data-kind")); });
  });
  document.getElementById("busca").addEventListener("input", tabela);
  document.getElementById("regiao").addEventListener("change", tabela);
  document.getElementById("lang-btn").addEventListener("click", function () {
    var l = lang() === "en" ? "pt" : "en";
    root.setAttribute("data-lang", l); root.lang = l === "pt" ? "pt-BR" : "en";
    try { localStorage.setItem("lang", l); } catch (e) {}
    tudo();
  });
  document.getElementById("theme-btn").addEventListener("click", function () {
    var escuro = getComputedStyle(root).colorScheme === "dark";
    root.setAttribute("data-theme", escuro ? "light" : "dark");
    try { localStorage.setItem("theme", escuro ? "light" : "dark"); } catch (e) {}
    tiles(mapa); tudo();
  });

  get("data/meta.json").then(function (m) { estado.meta = m; return carregar("sale"); }).then(tudo).catch(function (e) {
    document.getElementById("kpis").innerHTML = '<div class="kpi">' + L("Não foi possível carregar os dados.", "Could not load the data.") + "</div>";
    console.error(e);
  });
})();
