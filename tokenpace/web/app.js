(function () {
  "use strict";
  var DATA = null;          // filled from api/usage
  var NOW = Date.now();
  var editing = false;      // a manual-entry form is open: do not re-render under it
  var TOKEN_KEY = "tokenpace-token";
  var SOURCES = { codex: "Codex CLI login", claude_code: "Claude Code login", openrouter_free: "OpenRouter API key",
                  manual: "entered by hand", push: "pushed from another machine", demo: "demo data" };
  // English names to match the page, in the browser's time zone and 12/24-hour habit.
  var H12 = /^h1[12]$/.test(new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle || "");
  function fmt(o) { o.hour12 = H12; return new Intl.DateTimeFormat(H12 ? "en-US" : "en-GB", o); }
  var whenFmt = fmt({ weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  var dayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short" });
  var hmFmt = fmt({ hour: "2-digit", minute: "2-digit" });
  var wdFmt = fmt({ weekday: "short", hour: "2-digit", minute: "2-digit" });
  var dmFmt = fmt({ day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

  var theme = (location.hash.match(/theme=(dark|light)/) || [])[1];
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  (function takeTokenFromHash() {
    var m = /(?:^|[#&])token=([^&]+)/.exec(location.hash);
    if (m) {
      try { localStorage.setItem(TOKEN_KEY, decodeURIComponent(m[1])); } catch (e) { /* malformed link: the page will ask */ }
      history.replaceState(null, "", location.pathname + location.search);
    }
  })();
  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    var t = null;
    try { t = localStorage.getItem(TOKEN_KEY); } catch (e) { /* storage blocked */ }
    if (t) headers.Authorization = "Bearer " + t;
    opts.headers = headers; opts.cache = "no-store";
    return fetch(path, opts);
  }

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function $(s) { return document.querySelector(s); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function pct(v) { return (Math.round(v * 10) / 10) + "%"; }
  function pct0(v) { return Math.round(v) + "%"; }
  function fmtX(v) { return v >= 99 ? "99×+" : (Math.round(v * 10) / 10).toFixed(1) + "×"; }
  function duration(ms) {
    if (ms <= 0) return "now";
    var min = Math.floor(ms / 60000);
    if (min < 1) return "under 1 min";
    var d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
    if (d > 0) return d + "d " + h + "h";
    if (h > 0) return h + "h " + (m < 10 ? "0" : "") + m + "m";
    return m + " min";
  }
  function ago(iso) { if (!iso) return "never"; var ms = NOW - Date.parse(iso); return ms < 60000 ? "under 1 min ago" : duration(ms) + " ago"; }
  function when(iso) { return whenFmt.format(new Date(iso)).replace(",", ""); }
  function whenShort(iso) { var t = Date.parse(iso); return (t - NOW < 6 * 86400e3 ? wdFmt : dmFmt).format(new Date(t)).replace(",", ""); }
  function amount(v) {
    v = Number(v);
    if (v >= 1e9) return trim(v / 1e9) + "B";
    if (v >= 1e6) return trim(v / 1e6) + "M";
    if (v >= 1e4) return trim(v / 1e3) + "k";
    return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
    function trim(x) { return (Math.round(x * 100) / 100).toString(); }
  }
  function noun(label) {
    var l = (label || "").toLowerCase();
    if (l.indexOf("week") === 0) return "week";
    if (l.indexOf("month") === 0) return "month";
    if (l.indexOf("day") === 0) return "day";
    if (l.indexOf("5") === 0) return "5 h";
    return l || "period";
  }
  function shortWin(label) {
    var l = (label || "").toLowerCase();
    if (l === "5 hours") return "5h";
    var m = /^week · (.+)$/.exec(l);
    if (m) return m[1].charAt(0).toUpperCase() + m[1].slice(1) + " wk";
    if (l.indexOf("week") === 0) return "week";
    if (l.indexOf("month") === 0) return "month";
    if (l.indexOf("day") === 0) return "day";
    return label;
  }
  function elapsedOf(w) {
    if (typeof w.elapsed_percent === "number") return w.elapsed_percent;
    if (!w.resets_at || !w.window_seconds) return null;
    return clamp(100 * (1 - (Date.parse(w.resets_at) - NOW) / (w.window_seconds * 1000)), 0, 100);
  }

  var adv = { groups: {} }, ranked = {}, subs = {}, groupList = [], gIndex = {};
  function setData(j) {
    DATA = j; adv = DATA.advice || { groups: {} }; ranked = adv.groups || {};
    subs = {}; (DATA.subscriptions || []).forEach(function (s) { subs[s.id] = s; });
    groupList = DATA.groups || []; gIndex = {};
    groupList.forEach(function (g, i) { gIndex[g.id] = i; });
  }
  function groupOf(id) { return groupList[gIndex[id]] || { id: id, label: id }; }
  function shape(id) { var i = gIndex[id] || 0; return i ? " g" + (i % 4) : ""; }
  function pin(id) { return el("i", "pin" + shape(id)); }
  function logo(g) {
    if (!g.logo_url) return null;
    var img = el("img", "logo"); img.src = g.logo_url; img.alt = ""; return img;
  }
  function label(g, host) { var l = logo(g); if (l) host.appendChild(l); host.appendChild(document.createTextNode(g.label)); return host; }

  /* ---------- hero: the 3-second answer, one tile per group ---------- */
  function renderNow() {
    var box = $("#now"); box.textContent = "";
    var shown = groupList.filter(function (g, index) {
      return (selectedAccount ? g.id === selectedAccount : index === 0) && (ranked[g.id] || []).length;
    });
    box.style.gridTemplateColumns = shown.length > 1 ? "1fr 1fr" : "1fr";
    if (!shown.length) { box.appendChild(el("div", "tile", "Not enough readings to rank yet.")); return; }
    shown.forEach(function (g) {
      var rows = ranked[g.id] || [];
      var first = rows.filter(function (r) { return r.level === "use"; })[0];
      var t = el("a", "tile" + (first ? " use" : "")); t.href = "#g-" + encodeURIComponent(g.id);
      t.appendChild(label(g, el("div", "k")));
      var who = el("div", "who");
      if (first) {
        who.appendChild(el("b", null, first.name));
        who.appendChild(el("span", "chip lv-use", first.verdict));
      } else {
        who.appendChild(el("b", null, rows.every(function (r) { return r.level === "blocked"; }) ? "All used up"
          : rows.every(function (r) { return r.level === "blocked" || r.free; }) ? "Only free tiers"
          : "Nothing has slack"));
      }
      t.appendChild(who);
      if (first) {
        var s2 = el("div", "sub2"); s2.appendChild(el("b", "c-use", fmtX(first.need)));
        s2.appendChild(document.createTextNode(" · " + noun(first.period.label) + (first.period.resets_at ? " resets in " + duration(Date.parse(first.period.resets_at) - NOW) : "")));
        t.appendChild(s2);
      }
      var rest = rows.filter(function (r) { return r !== first && (r.level === "use" || r.level === "lean_use"); }).map(function (r) { return r.name; });
      var then = el("div", "then");
      if (rest.length) { then.appendChild(document.createTextNode("then ")); then.appendChild(el("b", null, rest.join(" · "))); }
      else if (rows.some(function (r) { return r.free; })) then.textContent = "free reserve only after that";
      else then.textContent = "nothing else with slack";
      t.appendChild(then);
      box.appendChild(t);
    });
  }

  /* ---------- generic dot strip: packs labelled dots into rows without overlap ---------- */
  function dotStrip(host, items, opts) {
    host.textContent = "";
    var strip = el("div", "strip"); host.appendChild(strip);
    var W = host.clientWidth || 300;
    var ROW = 15, DOT = 7, GAP = 8;
    (opts.zones || []).forEach(function (z) {
      var d = el("div", "zone"); d.style.left = (z.from * 100) + "%"; d.style.width = ((z.to - z.from) * 100) + "%"; d.style.background = z.color;
      if (z.label) { var s = el("span", null, z.label); s.style.color = z.ink; d.appendChild(s); }
      strip.appendChild(d);
    });
    (opts.bands || []).forEach(function (b, i) {
      if (i % 2 === 0) return;
      var d = el("div", "band"); d.style.left = (b.from * 100) + "%"; d.style.width = ((b.to - b.from) * 100) + "%"; strip.appendChild(d);
    });
    (opts.mids || []).forEach(function (x) { var m = el("div", "mid"); m.style.left = (x * 100) + "%"; strip.appendChild(m); });
    var meas = el("div", "row"); meas.style.visibility = "hidden"; strip.appendChild(meas);
    items.forEach(function (it) { var l = el("span", "lab", it.label); meas.appendChild(l); it.w = l.offsetWidth + 2; });
    strip.removeChild(meas);
    var rows = [];
    items.sort(function (a, b) { return a.x - b.x; }).forEach(function (it) {
      var x = it.x * W;
      var rightEnd = x + DOT / 2 + 5 + it.w;
      var side = rightEnd <= W ? "right" : "left";
      var L = side === "right" ? x - DOT / 2 : x - DOT / 2 - 5 - it.w, R = side === "right" ? rightEnd : x + DOT / 2;
      var r = 0;
      for (; r < rows.length; r++) { if (rows[r].every(function (o) { return o.R + GAP <= L || o.L >= R + GAP; })) break; }
      if (!rows[r]) rows[r] = [];
      rows[r].push({ L: L, R: R });
      it.row = r; it.side = side; it.px = x;
    });
    var nrows = Math.max(rows.length, 1);
    var footH = opts.bands ? 17 : 0;
    var topPad = (opts.zones && opts.zones.length) ? 16 : 0;
    strip.style.height = (topPad + nrows * ROW + 4 + footH) + "px";
    items.forEach(function (it) {
      var row = el("div", "row"); row.style.top = (topPad + (nrows - 1 - it.row) * ROW) + "px";
      var d = el("i", "dot" + shape(it.group)); d.style.left = it.px + "px"; if (it.title) d.title = it.title;
      var l = el("span", "lab " + it.side, it.label); if (it.title) l.title = it.title;
      if (it.side === "right") l.style.left = (it.px + DOT / 2 + 5) + "px"; else l.style.right = (W - it.px + DOT / 2 + 5) + "px";
      row.appendChild(d); row.appendChild(l); strip.appendChild(row);
    });
    if (opts.bands) {
      var days = el("div", "days"); strip.appendChild(days);
      opts.bands.forEach(function (b) {
        var w = (b.to - b.from) * W; if (w < 30 || !b.label) return;
        var d = el("span", "day", b.label); d.style.left = ((b.from + b.to) / 2 * 100) + "%"; d.style.transform = "translateX(-50%)"; days.appendChild(d);
      });
      (opts.edgeLabels || []).forEach(function (e) { var d = el("span", "day", e.label); if (e.at === "start") d.style.left = "0"; else d.style.right = "0"; days.appendChild(d); });
      return days;
    }
    return null;
  }

  /* ---------- reset timeline: next 72 hours, local time ---------- */
  function renderTimeline() {
    var host = $("#timeline"); host.textContent = "";
    var H = 72 * 3600e3;
    var k = el("div", "k", "Resets · next 3 days");
    if (groupList.length > 1) {
      var right = el("span", "right");
      groupList.forEach(function (g) { var s = el("span"); s.appendChild(pin(g.id)); s.appendChild(document.createTextNode(" " + g.label)); right.appendChild(s); });
      k.appendChild(right);
    }
    host.appendChild(k);
    var events = [];
    (DATA.subscriptions || []).forEach(function (s) {
      (s.windows || []).forEach(function (w) {
        if (!w.resets_at) return; var t = Date.parse(w.resets_at); if (!(t > NOW)) return;
        events.push({ t: t, name: s.name, group: s.group, wins: [shortWin(w.label)], title: s.name + " · " + groupOf(s.group).label + " · " + w.label + " · " + when(w.resets_at) });
      });
    });
    events.sort(function (a, b) { return a.t - b.t; });
    var merged = [];
    events.forEach(function (e) {
      var last = merged[merged.length - 1];
      if (last && last.group === e.group && last.name === e.name && Math.abs(last.t - e.t) < 60000) { last.wins = last.wins.concat(e.wins); last.title += " · " + e.wins[0]; }
      else merged.push(e);
    });
    var within = merged.filter(function (e) { return e.t - NOW <= H; }), beyond = merged.filter(function (e) { return e.t - NOW > H; });
    var items = within.map(function (e) { return { x: (e.t - NOW) / H, group: e.group, label: e.name + " " + e.wins.join(" + "), title: e.title }; });
    var bands = [], mids = [], end = NOW + H, cursor = NOW;
    var midnight = new Date(NOW); midnight.setHours(24, 0, 0, 0);
    while (cursor < end) {
      var m = midnight.getTime(), next = Math.min(m, end);
      bands.push({ from: (cursor - NOW) / H, to: (next - NOW) / H, label: dayFmt.format(new Date(cursor)) });
      if (m < end) mids.push((m - NOW) / H);
      cursor = next; midnight.setDate(midnight.getDate() + 1); midnight.setHours(0, 0, 0, 0);
    }
    var area = el("div"); host.appendChild(area);
    dotStrip(area, items, { bands: bands, mids: mids, edgeLabels: [{ at: "start", label: "now " + hmFmt.format(new Date(NOW)) }] });
    if (beyond.length) {
      var later = el("div", "later", "Later:");
      beyond.forEach(function (e) {
        var s = el("span"); s.appendChild(pin(e.group)); s.appendChild(document.createTextNode(" " + e.name + " " + e.wins.join(" + ") + " · " + duration(e.t - NOW))); s.title = e.title; later.appendChild(s);
      });
      host.appendChild(later);
    }
  }

  /* ---------- ranked rows: one bar, used fill + slack/ahead band + time tick ---------- */
  function paceBar(w) {
    var e = elapsedOf(w);
    var used = clamp(w.used_percent, 0, 100), elp = clamp(e == null ? 0 : e, 0, 100);
    var d = el("div", "dual"); d.setAttribute("role", "img");
    d.setAttribute("aria-label", pct0(used) + " of the quota used" + (e == null ? "" : ", " + pct0(elp) + " of the " + noun(w.label) + " elapsed"));
    var q = el("div", "lane");
    var qf = el("i", "fill"); qf.style.width = used + "%"; q.appendChild(qf);
    if (e != null) {
      // drawn over the fill so use past the tick shows as an amber hatch
      var band = el("i", elp >= used ? "slack" : "ahead"); band.style.left = Math.min(used, elp) + "%"; band.style.width = Math.abs(elp - used) + "%"; q.appendChild(band);
    }
    d.appendChild(q);
    if (e != null) { var tick = el("i", "tick"); tick.style.left = elp + "%"; d.appendChild(tick); }
    return d;
  }
  var openWhy = {};
  var selectedAccount = "";

  var serviceMarks = {"codex": "data:image/png;base64,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", "openai": "data:image/png;base64,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", "claude": "data:image/png;base64,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", "antigravity": "data:image/png;base64,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", "gemini": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADgAAAA4CAYAAACohjseAAAL4UlEQVR42u2bbYxc1XnH/895zr135t6dfd/FohLwJU1qtSSAUkxfGG8pDSCcxg3rqAE+kLRbJW1oPzRCGJLZEW2aIAU3qB/yomIFNankVaKWAI5F09mJCKkKxkuDbFI3JUa4ilnbu/N67517znn6YWa9a9lgHM+4uMpdje69MyvN/u7/f57nOc85C1zEQ4pFLcWixi+O/h10UZQDaGF2Vn2g0XgIAPYVCp+ZXVhwBMigv1sNHG52lgmQm5rJlmGm+4eZ7r+pmWwhQGR2li95QLzxBgGAL9ktsNbCWutLdsvGzy5twGrViggpa7dBhCHCsHabiBCqVXtJA65Z8Pjt26/1FF2dWes6zjmt/av/Z9td1xCAPQO2qRq0PQmQIEvu8ZgoU9q1OO8oGKFY4R4AMjVgm9IgIycAHP3Qh8bH2+3DUHqsoXzpqACO89Qk/2TNi951w8IjK90/hOTSUrBYZALET+2f5HNDY6scmpgjanNEqyowOhobT5U/RyBZLM7zJaWgAIRSiV47fHgkV0t/LBxM1uEh44BS5SNRvnNeSE34y7FXePfSVav1+fK8DELFgSi4WCwylcsua9DOIDcydRJ5F+uQWjqPlg7R1pFaJd9RNDHdINpZLpfdoFTsu4J7Zmd5x8KCXdr+sV8dFbW/Raxj5VGHA0pVgFQFSNhHqgLJdM5lHJqE+NpPfOXug7Oze3hhYYd9xyooAE29sZlEhJj01zI/9Bs6L20dUYsjNHWIpg7R0hFaOqS6yiEJRoI6578mIrR58xT1+6H31Rbb5ua83/z2I+b2l4/PD+UKdx1zyiQ61DHn0eYcYp1HzHnEnEPMeSQ6VE1hg2jTlU889Z/uc1+4qTI394K3f/9X3TvOopViSc9Uy+bZHffeNsTBU3XRJlY+p8rvWpMDpMrH+vXavS8ph9b6Q7rRSW/9+13Xf7dUquhyeca8YwArpZKeKZfN9+/51OYQ+rk4jQp1BEi1r9bGXco9OLUBrgedUOAyP6JU+bVWZn7jnx5+96FiqaKrfYCkC4cr6ply1TwzN3fFCOe+b9zQlcut0HU4rzYElLOCbQw6CTxnw3EVC70at9yN1YcnXy+WRFfLZP7PAE/B3Tt3xajHz4gt/PKxds62s1FuU3imauyfAbbRsglpa8MJjp05FCf6935UpguG/LkBpVLUNFM1iw9+9NcKkntC3PBVx1p5G5sRbnRG0FIRzrTnOlDSU7KjetdroGCr8szO4CdIse3AZ+nQhUCeN6BISQFlIYI89zd/sC10/uOZGRo90SzYthvlhhlGMx1Dkwqng62pdFqg8U+DtsS974BlHyyCE66Du5cepL0oSTellckNJA+KgCqVoiYqO6ISHfi7D5SH8+aJVGH0pMClgeaYPSQ6h7bfrVh6+a575ghNDtHWEdpr73OElo4Q6/wpOAAgAtsOHAQTysfT7/u8+QzK5FAmVyyJhgj1TUEREBbBNAMDAC89tuXanNNfClT4Wz9b9V0zG6LEjFIzG0Ezm0Azm0TNTKDuRntWPF3B9XGYQ0f5sKTeqnIQEMAhyKVuUYz68wMP0H8AQLEkujoPC3rr+pXeAkwBICJYAFh6/IbpMb/5acn0vZDQX24Epm0j3c6G0TTDaGVjqNtxNLIp1NPLUHcTiHUXqnNaNO2eM+WdT4VkdADtDBKB26Vt84vP7xw5cQoUcG9m3dMAZQ8YU6A1tQDg1T2bN0Vh+nEY/FnAwaZjKx5aWd7GJuK2jdDMhtG0I2iYUTSyCdTMFGrZJtTNJsQq7AUSv6dkgI7y4Oj8K0QRWFJgzgMuxesAHiXG7v1/Scd7v0HFEnj6IGRhgew5FCTU9l15PevkbnLykbznT56sadTank1cXsUmTy0TomkKaNoCGnYEdTPWtaaZxqq5DHVzORIZ7wWVrmpGXXDPV0TglAYrH7CpO0ZQ3yTCP+y/j158UwUFIIJgZe9VRV+nNytlb/VYruG8oNNQqMeeTTNfJTagtsmhZfNo2RANM4SGHUbdjqBux7BqJlGzU1g1m7BiL0dsJ5FRAR3ldwdT/6p6EayDuhQQcf9OSj1lDb730gP0A0AIIFFSgSZAGvs2PTw61aqE+WxnzrPX2MxJpy4mMxBSYNFCVgmsFhgWdBjoeIJUd1+xJ4h9QdsHWoGgHgCrOYVYU3/hev0NIrAYiIlhxAFKq18nRtkfwrPv+5w8BJAUS6L12421TgkcCwy6gBk5dJwgJYeEBYkFYha0rUPTClrWwZgONNoACMp5wCCb5wI623M8zaLLe68q5nR6Myt7q6/kGg4FWZNQSzzTdB63xKemC1C3edRdhJpEqNlhrLgRrMgYVuwkTrhp1M0k6tk0TDYOZfNgG0KZEEr0wCwK4HkhPO0Mntlo0TcNMsf3XXl9Tid3K3Ef8QNv8mctjROZtg34qu5yVHMRVmUIqzKEFTuGEzKGFTeOhplAO5tAozOFxIyDbQiyIdh0QUm4b0HGpTgmhH8Esm8cuM9/4edKEy8/uXnTtB9/LLb0KRfqTa83GCvwbE0iXnEFnMQQTrhh1OwomnYMLTOORjaOhplC3JnoqmfDnpJ5KBuC5ALTROKOCuRRsbx76QFaPtXIK4l+m2nizES/71s3TI/nap+Ome9t+55/NNFmBZFecQXUpICGGUbLjKFux9DMxlHPJtHOJqBsdApM2TzYhFAu/+bmOWeixy5t8cXnd9L5J/q3U6p964kt10rO/m06pH/7aIPdqomoKaPUMAXU3QjqZhRNM94r2yZAZgjK5dbHoetB2tzZo8JblGrOmb9Yuj946XxKtXN6hQhCMzBrxfaHP/hvL97xg9u21pv8UBCw8nxHgsSBO4BKAZV0XxxDOIblGE4lcCqBcPfsOIZweq4H60iDSINMYj/74n08s3R/8NJasV0tkzkX3AVPl/7q6d/Z5nzv8bryR5fbedt0I9wwo92yrVfZwBTAG8Yf96y6btfgrOPtok+X1hUtOyJIpVLUD972r99pNXI3OoufBpFjR6kV7iolHAMcw3EMxwlsTz2r1hV1HMOp7Ey4HBgOP3Et3Lj0IO0tlkSvTZcuWl90ZqZqSpWi/sL2f/7Ras3bKh0czkWOhRILTkAqAVTcA+tC2R7sOnTave5N1tfgxOBQp42tS2U6eKEtiwtq/JZ7kI/d8e0jrWX/ZpvJER0aFsQO3B2HTq1DCSfda9U9i4phVVdth45jHywG/20z3NyPfkxfGr/Vrx9xxUpRP/n7T65cvv39/0KBvbPDFCSWJYWmDHqtfOymBlq7O/UuBHCsA4KjFdXObjpQ8l7tB1zfWvfVmaopVor6mQ8/9nLaojuVZxV04kTF4jZE0VMRdU3J7hgV0ZlznkHHJH+4v5x7pV9wfW3dH/n6EXfdV+a8H9712CuX3fFellFvpp3AZtCqKxv1ftaCd+9OYHU4pm2rVjp03/Tu6+Ze8H74yC8ZvENXl6hYKXJ166L9lac//lwrX9hSa0RW2YjPVs0ok7eeN8VoybP/9cmrbyzOL3K1vNWij+uE/V4flOrytIBIxPl/7FzSER2TU7F0I2m8IT3EIl6HjNRTQnsORFI9uCzo8yJo/xdAdyxYVIr6lW1ffjlL7aNqxCmrYmc3jDvLCSy1HYa0svbklw7/6Q2HUKpo9HltcHCbEEQI8/N0xfvTkVXv+I8N5Sc5CaBcSL3ZhWMUSHX8N/wsfM+Rny7VMT8vb6f0emdsQiASbIV67fbPrxjHu2TIkVOJPVWLqtghEnLU2nXknu2r2Ao1CLjBbsaTbtIb3lca66ijh4lonNOcKBtC0RBxJ3ciSqN3vfbRT6yeeii4lLaREAkWi1y/pXxSGfVNiRiWY+s4tjIkcCr+xmt3fnIFi/M8KLjB73RanpZuwgt3U2xFlFWWY3admnNkdwMgLB8c6JZKugibRQkQ5Pb+0QHk1XsBgNvqQOu2L18HELqVGi7h3YaLJQaREPR3oJWFVtaRPNm1cOn/wX7RngVZvO+Sp5k8zSz+vo2fXfJbmiEgLOxR+eh7fw0Sipu/uxOzO9yg7fmLo+9HpahRubj/VvC//Fvp/AB2DSsAAAAASUVORK5CYII=", "mimo": "data:image/png;base64,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", "openrouter": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADgAAAA4CAYAAACohjseAAAFNElEQVR42u2aXahUVRTHf+vM3KuZVOpL9RCVca3Ajz4gS4Mo37ISUQOJnnwoezCwpIco6DmQ6CUIFSISKwgCIbToC6H7EFIZJEVCpJXolYrU7sw5/x5a+7I5njNzZjwzzMMsOMzcc8/Za//3Xl//tQfGMpaxjGUs/YvVNZAkG9qkzTRQgA4m8fdlZukwd8X1NwABWSfA1uPAyf8LeCkgSfOAySHgmzWzf3O6G2WLbP0Ak7QQWA2sAVYBNwKLgXl1mn3RVICLwG/AUeAgcMjMWj5H9WK+c6sTfV8l6XVJv2h05JikJ3KbUc3Ow8OSpiS9LakdDZxKavm91K9sCFfqOlu5+bwv6ZpKIOMHJO2Q9Fc0UMsVjYqEhZakryUtiTfHisCZWSZpAbAP2OL/agPNEU55LWAC+BJ4CMiALCkBtwg47OBa7tzNCgEgvoYtEz7X+4GXPCAmlsstBswHPgbujValTNIO4DO/Er+GkiJdZwrcamYnYsUNM8uAtyqACzmnEYH7EzgF/OGhPPH/JdFCDKMyk+fjp+d8MCRKSc8CuyuAC6njMPAB8BXwK3DeAS0GlgHrgI3A0oJ3ByWZ4/oRWI6kxKPOUknnPfQWRcksuv+RpHsqppsrJW2XdNrfbQ8purYlrZxL5JLei9JAEbjUv++Mg5KkZrRI4UokNSQ1o2dvkPRZBx11pw5J2hKUr8iByIMLq741mHRc4VQoGJr+fVLSwSHsZBh7ewgyT7ndZiU23QB2mtk7kibNLK3KIMxMZtZ2P58FNgPHfMxs4P4o6SpJJ6PdKlqJQ74DE5fD+yJ3uEPSbFTe1S1hzA1IWl8CLi6DVripXXYEjMz1zQH740VJU4mXNRSYS+oh/xMz+xZIaiK2cit4bUBpI/Nc+D3wUwLcXcINQ2I+EFU5dbQbUv88Bnzj46YDyIP7zSxLgJtKADb94WknkXUGhLBrR3KLWQe4BDgL7JVkoerIAwwKzwEna55ELMdrHi+41S4zmwGSxNsMlJjn38CFAYbxczUW2bNeXu4zs72h/Ey67ExjwEygWQOw4L+TwH5gm0f7DJ/8PwUmGMx1EXD1AAFe1yegtn+G9mEbeNnMtuZbiQnwewntyICFwC1+L6mZtwGs7IMOBYrWcPbyLrDazF6Za1NEnbUm8AMwFZVk+Yi0zqOd1di0zSTNd/ZdZfHk+k8DX7jvTgOfmtnPHXujkl4oKX5D4X08Zgw1VjKP9VB0ByIwI+nBfJulYxdN0p0dlIT7T8aTqwngdI+sIosWfmNgJ5W61t5uy0p2MZV0StKSnhqrxbom/POZPilTKM5bkh6Nx+ymeEeHwrcdsXjrt+iOwK3xQrjdJ5PoDaRPeJGkM9GOlYE8EMwi+GUFstuIOuRrJZ3N+bgGCjLyiee70Jdw/4ik5XnGXnA1cnq2S7pQA7jeQEY9lHmSvuviG+H+BUm7Jd3eZQev8Gj5eUF0Vs0gHykCmW8b3uUtwPiAs6xiD+38aX/nODDjJdP1wApgLXBzrhC2AbUJ28AmM/tQUtPM2mXthG0VDlmyHiJgOoRWYZG5Njv544uRSaZdclM4ygrHWfHfqTTUU6aeQO4qCDCjLjHI9aVH2JFPbgbecFKc5tj4qErwyRawyTqVVN7PXAq8CmzIUZYQMGxEQSbAGevWx4x+ePAw8BzwQMkR2ihK0yr+wgI/WkPSfcDjTqOWjbjJ7rEeu9JzTNmBTwG3+fHYtcCCIR52duOPR4E9fbXf66BNjPpv1aJm8KgGGmpuKI9lLGMZy6XyHz9at9+nGdufAAAAAElFTkSuQmCC", "opencode": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADgAAAA4CAYAAACohjseAAABU0lEQVR42u2aMU4DMRBF3+xGSQENF+AeXIGKM3AwLhGJK3AKGi5AE4qg7H4aW0qDWJIJcZb/m23W1n8a22PNGKzLVkz5SVIHdI15HyNidAR/iFxEhCTdAbfA0EAkR6AH3iLipXo8aCZJffmu1Z7W+x6/02Ii66ZEb/eLMadS9bCZ8vNUs11ZFirfc6p66KYan7UMeOlanGB//NkF5ByAMecIfgIfSXNdActWAGteegYeyxE+HDhXHfsEPGTk3cwIbiPiPWUjS9sWl2hIiqQItnnIlIu5Dr381rGScB40oAENaEADGtCABjSgAQ1oQAMa0IAGNCD/ojdRyoa1fMgRpccmAWu5cMdxVXIy64aZgCtJN+QUflctAdY57oFX8povKf4yI7gkqSPkBqgboE70BpzdTWYs+WloYJ9VD2Mm4HVJwn0DQen3PPk5JX7S7CfN1ln1BXdaC5/KGVW8AAAAAElFTkSuQmCC"};
  function serviceKey(s) {
    var key = [s.provider, s.id, s.name].join(" ").toLowerCase();
    if (/antigravity/.test(key)) return "antigravity";
    if (/argon|gemini/.test(key)) return "gemini";
    if (/codex|chatgpt/.test(key)) return "codex";
    if (/openai/.test(key)) return "openai";
    if (/claude|anthropic/.test(key)) return "claude";
    if (/mimo|xiaomi/.test(key)) return "mimo";
    if (/openrouter/.test(key)) return "openrouter";
    if (/opencode|zen/.test(key)) return "opencode";
    return null;
  }
  function serviceLogo(s, host) {
    var key = serviceKey(s);
    if (!key) return;
    var img = el("img", "service-logo" + (/^(codex|openai|mimo|openrouter|opencode)$/.test(key) ? " service-mono" : ""));
    img.src = serviceMarks[key]; img.alt = ""; host.appendChild(img);
  }
  function serviceWindows(r) {
    return [r.period].concat(r.short_windows || []).filter(function (w) {
      return w && !(r.exhausted_windows || []).some(function (x) {
        return x.kind === w.kind && x.label === w.label;
      });
    });
  }
  function addMeter(grid, w, credit, showPace) {
    var meter = el("div", "meter"), head = el("div");
    head.appendChild(el("span", "win", w.label));
    if (showPace && typeof w.need === "number") {
      head.appendChild(el("span", "need " + (w.need >= 1.15 ? "c-use" : w.need < 0.85 ? "c-save" : "c-on_pace"), fmtX(w.need)));
    }
    meter.appendChild(head);
    meter.appendChild(paceBar(w));
    var facts = [pct0(w.used_percent) + " used"], elapsed = elapsedOf(w);
    if (elapsed != null) facts.push(pct0(elapsed) + " elapsed");
    if (w.resets_at) facts.push((credit ? "expires in " : "resets in ") + (w.reset_precision === "day" ? "~" : "") + duration(Date.parse(w.resets_at) - NOW));
    else facts.push("No active reset");
    meter.appendChild(el("div", "facts", facts.join(" · ")));
    grid.appendChild(meter);
  }
  function usedUp(rows) {
    var exhausted = rows.filter(function (r) { return (r.exhausted_windows || []).length; });
    if (!exhausted.length) return null;
    var box = el("section", "rec used-up"), list = el("ul");
    box.appendChild(el("h3", null, "Used up"));
    exhausted.sort(function (a, b) {
      function next(r) { return Math.min.apply(null, r.exhausted_windows.map(function (w) { return Date.parse(w.resets_at); })); }
      return next(a) - next(b);
    }).forEach(function (row) {
      var sub = subs[row.id] || row, li = el("li");
      serviceLogo(sub, li);
      li.appendChild(el("strong", null, row.name));
      var resets = row.exhausted_windows.map(function (w) {
        return w.label + (sub.credit_grant ? " expires in " : " resets in ") + (w.reset_precision === "day" ? "~" : "") +
          duration(Date.parse(w.resets_at) - NOW);
      });
      li.appendChild(document.createTextNode(" · " + resets.join(" · ")));
      list.appendChild(li);
    });
    box.appendChild(list);
    return box;
  }
  function accountSwitch(box, groups) {
    if (groups.length < 2) return;
    var nav = el("div", "account-switch"); nav.setAttribute("role", "group"); nav.setAttribute("aria-label", "Account");
    groups.forEach(function (g) {
      var btn = el("button", "btn", g.label);
      btn.type = "button";
      btn.setAttribute("aria-pressed", String(g.id === selectedAccount));
      btn.addEventListener("click", function () {
        selectedAccount = g.id; render();
        var buttons = document.querySelectorAll(".account-switch button");
        for (var i = 0; i < buttons.length; i++) if (buttons[i].getAttribute("aria-pressed") === "true") buttons[i].focus();
      });
      nav.appendChild(btn);
    });
    box.appendChild(nav);
  }

  function renderRec(r) {
    var s = subs[r.id] || {}, p = r.period || {};
    var rec = el("article", "rec");
    var head = el("div", "head");
    serviceLogo(s, head);
    head.appendChild(el("span", "name", r.name));
    if (r.level !== "blocked") head.appendChild(el("span", "chip lv-" + r.level, r.verdict));
    rec.appendChild(head);
    if (s.credit_grant) {
      var amount = (s.windows || []).find(function (w) { return w.kind === p.kind; });
      if (amount && typeof amount.limit_amount === "number" && typeof amount.used_amount === "number") {
        rec.appendChild(el("div", "facts", "$" + (amount.limit_amount - amount.used_amount).toFixed(2) + " remaining"));
      }
    }
    var raw = (s.windows || []).find(function (w) { return w.kind === p.kind && w.label === p.label; });
    if (raw && raw.limit_amount) {
      rec.appendChild(el("p", "facts", amount(raw.used_amount) + " of " + amount(raw.limit_amount) +
        (raw.unit || s.unit ? " " + (raw.unit || s.unit) : "")));
    }
    var windows = serviceWindows(r);
    if (windows.length) {
      var grid = el("div", "meter-grid");
      windows.forEach(function (w) { addMeter(grid, w, s.credit_grant, !r.free && !r.unmetered); });
      rec.appendChild(grid);
    }
    if (r.caveat) rec.appendChild(el("div", "cav", r.caveat + "."));

    var more = el("details", "more"); more.dataset.id = r.id; if (openWhy[r.id]) more.open = true;
    more.appendChild(el("summary", null, "Why"));
    var inner = el("div", "inner");
    inner.appendChild(el("p", null, r.reason + "."));
    if (r.use_via) { var v = el("p"); v.appendChild(el("b", null, "Use via ")); v.appendChild(document.createTextNode(r.use_via)); inner.appendChild(v); }
    inner.appendChild(el("p", "src", sourceLine(s)));
    more.appendChild(inner);
    rec.appendChild(more);
    if (s.manual) rec.appendChild(reportControls(s));
    return rec;
  }
  function sourceLine(s) {
    var src = SOURCES[s.provider] || s.provider || "";
    if (s.provider === "push" && s.source) src = "pushed by " + s.source;
    return (s.plan ? s.plan + " · " : "") + (src ? src + " · " : "") + "read " + ago(s.observed_at)
      + (s.status && s.status !== "ok" ? " · " + s.status + (s.message ? ": " + s.message : "") : "");
  }
  function renderGroups() {
    var box = $("#groups");
    openWhy = {}; box.querySelectorAll("details.more[open]").forEach(function (d) { openWhy[d.dataset.id] = true; });
    box.textContent = "";
    if (!groupList.some(function (g) { return g.id === selectedAccount; })) selectedAccount = groupList.length ? groupList[0].id : "";
    accountSwitch(box, groupList);
    var warnings = (adv.left_out || []).filter(function (w) { return w.group === selectedAccount; });
    if (warnings.length) {
      var alert = el("section", "group"); alert.setAttribute("role", "alert");
      alert.appendChild(el("h2", null, "Readings need attention"));
      warnings.forEach(function (w) { alert.appendChild(el("p", "src", w.name + ": " + w.reason)); });
      box.appendChild(alert);
    }
    var shown = 0;
    groupList.filter(function (g) { return g.id === selectedAccount; }).forEach(function (g) {
      var rows = ranked[g.id] || []; if (!rows.length) return;
      shown++;
      var sec = el("section", "group"); sec.id = "g-" + g.id;
      var h = label(g, el("h2"));
      var latest = (DATA.subscriptions || []).filter(function (s) { return s.group === g.id && s.observed_at; })
        .map(function (s) { return s.observed_at; }).sort().pop();
      h.appendChild(el("span", "meta", "read " + ago(latest)));
      sec.appendChild(h);
      var exhausted = usedUp(rows);
      if (exhausted) sec.appendChild(exhausted);
      [["Subscriptions", false], ["Free routes", true]].forEach(function (part) {
        var items = rows.filter(function (r) {
          return r.free === part[1] && (serviceWindows(r).length || (subs[r.id] || {}).manual);
        });
        if (!items.length) return;
        sec.appendChild(el("h3", "group-part", part[0]));
        items.forEach(function (r) { sec.appendChild(renderRec(r)); });
      });
      box.appendChild(sec);
    });
    var out = warnings;
    if (out.length) {
      var sec2 = el("section", "group"); sec2.appendChild(el("h2", null, "Outside the ranking"));
      out.forEach(function (o) {
        var s = subs[o.id] || {};
        var rec = el("article", "rec");
        var head = el("div", "head"); serviceLogo(s, head); head.appendChild(el("span", "name", o.name));
        if (groupList.length > 1) head.appendChild(el("span", "win", groupOf(o.group).label));
        rec.appendChild(head);
        rec.appendChild(el("p", "src", o.reason));
        if (s.manual) rec.appendChild(reportControls(s));
        sec2.appendChild(rec);
      });
      box.appendChild(sec2);
    }
    box.style.gridTemplateColumns = shown + (out.length ? 1 : 0) > 1 ? "" : "minmax(0, 1fr)";
  }

  /* ---------- manual entry: posts to api/report ---------- */
  function field(form, text, name, type, attrs, full) {
    var l = el("label", full ? "full" : null, text); var i = document.createElement("input"); i.name = name; i.type = type;
    Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); }); l.appendChild(i); form.appendChild(l); return i;
  }
  function reportControls(s) {
    var wrap = el("div", "actions");
    var open = el("button", "linkish", "Update numbers"); open.type = "button"; wrap.appendChild(open);
    open.addEventListener("click", function () {
      open.hidden = true; editing = true;
      var f = el("form", "report"); var cur = (s.windows || [])[0] || {};
      f.appendChild(el("div", "hint full", "Copy the numbers from the provider's console: the % used, or the amount used and the total."));
      var pctIn = field(f, "% used", "used_percent", "number", { min: "0", max: "100", step: "0.1", inputmode: "decimal" });
      var plan = field(f, "Plan", "plan", "text", { maxlength: "40", placeholder: "e.g. Lite" }); if (s.plan) plan.value = s.plan;
      var used = field(f, "Used", "used_amount", "number", { min: "0", step: "any", inputmode: "decimal" }); if (cur.used_amount != null) used.value = cur.used_amount;
      var total = field(f, "Total", "limit_amount", "number", { min: "0", step: "any", inputmode: "decimal" }); if (cur.limit_amount != null) total.value = cur.limit_amount;
      var unit = field(f, "Unit", "unit", "text", { maxlength: "20", placeholder: "credits" }); if (cur.unit || s.unit) unit.value = cur.unit || s.unit;
      var reset = field(f, "Renews on", "resets_at", "datetime-local", { required: "required" });
      if (cur.resets_at) reset.value = new Date(Date.parse(cur.resets_at) - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      var days = field(f, "Period (days)", "period_days", "number", { min: "1", max: "366", step: "1", value: String(s.period_days || 30) });
      var msg = el("div", "hint full");
      var act = el("div", "full actions"); var save = el("button", "btn", "Save"); save.type = "submit"; var cancel = el("button", "linkish", "Cancel"); cancel.type = "button";
      act.appendChild(save); act.appendChild(cancel); f.appendChild(msg); f.appendChild(act);
      cancel.addEventListener("click", function () { f.remove(); open.hidden = false; editing = false; });
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var body = { id: s.id, plan: plan.value.trim() || null, unit: unit.value.trim() || null, period_days: days.value ? Number(days.value) : null };
        if (used.value !== "" || total.value !== "") { body.used_amount = Number(used.value); body.limit_amount = Number(total.value); }
        else if (pctIn.value !== "") { body.used_percent = Number(pctIn.value); }
        else { msg.textContent = "Enter the % used or the amounts."; return; }
        if (reset.value) body.resets_at = new Date(reset.value).toISOString();
        save.disabled = true;
        api("api/report", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
          .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ("HTTP " + r.status)); return j; }); })
          .then(function () { editing = false; return load(); })
          .catch(function (e) { msg.textContent = "Not saved: " + e.message; save.disabled = false; });
      });
      wrap.appendChild(f);
    });
    return wrap;
  }

  /* ---------- pace scale (inside “How the order works”) ---------- */
  function renderScale() {
    var host = $("#scale"); host.textContent = "";
    if (!$("footer details.how").open) return;
    var lo = 0.25, hi = 8, span = Math.log(hi / lo);
    function X(v) { return clamp(Math.log(clamp(v, lo, hi) / lo) / span, 0, 1); }
    var items = [];
    groupList.forEach(function (g) {
      (ranked[g.id] || []).forEach(function (r) {
        if (r.free || r.level === "blocked" || typeof r.need !== "number") return;
        items.push({ x: X(r.need), group: g.id, label: r.name + " " + fmtX(r.need), title: r.name + " · " + g.label + " · needed pace " + fmtX(r.need) });
      });
    });
    var area = el("div"); host.appendChild(area);
    var days = dotStrip(area, items, {
      zones: [
        { from: 0, to: X(0.85), label: "Save · under 0.85×", color: "color-mix(in srgb, var(--amber) 14%, transparent)", ink: "var(--amber)" },
        { from: X(0.85), to: X(1.15), label: "", color: "var(--band)", ink: "var(--ink-muted)" },
        { from: X(1.15), to: 1, label: "Use · over 1.15×", color: "color-mix(in srgb, var(--ok) 14%, transparent)", ink: "var(--ok)" }
      ],
      bands: [{ from: 0, to: 1, label: "" }],
      mids: [X(0.5), X(1), X(2), X(4)],
      edgeLabels: []
    });
    [0.5, 1, 2, 4].forEach(function (v) { var d = el("span", "day", v === 1 ? "1× on pace" : v + "×"); d.style.left = (X(v) * 100) + "%"; d.style.transform = "translateX(-50%)"; days.appendChild(d); });
  }

  function render() {
    document.title = DATA.title || "Token Pace";
    $("#title").textContent = DATA.title || "AI subscriptions";
    $("#version").textContent = DATA.version ? " v" + DATA.version : "";
    $("#android").hidden = !DATA.apk_url;
    $("#meta").textContent = "Updated " + ago(DATA.last_collect) + " · " + hmFmt.format(new Date(NOW));
    $("#method").textContent = adv.method || "";
    $("#about-text").textContent = "Times are in your browser's time zone. The page reloads every minute; the server reads the subscriptions every "
      + Math.max(1, Math.round((DATA.refresh_seconds || 600) / 60)) + " min.";
    renderNow(); renderTimeline(); renderGroups(); renderScale();
  }
  function load() {
    return api("api/usage").then(function (r) {
      if (r.status === 401) { $("#gate").hidden = false; throw new Error("this server needs its token"); }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (j) {
      setData(j); NOW = Date.now();
      $("#gate").hidden = true; $("#error").hidden = true;
      if (!editing) render();
    }).catch(function (e) {
      var box = $("#error"); box.hidden = false;
      box.textContent = "Could not read the data (" + e.message + ")." + (DATA ? " Showing the last reading." : "");
    });
  }

  $("#gate").addEventListener("submit", function (ev) {
    ev.preventDefault();
    try { localStorage.setItem(TOKEN_KEY, $("#gate-token").value.trim()); } catch (e) { /* storage blocked */ }
    load();
  });
  $("#refresh").addEventListener("click", function () {
    var b = this; b.disabled = true;
    api("api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .catch(function () {})
      .then(function () { return new Promise(function (ok) { setTimeout(ok, 5000); }); })
      .then(load)
      .then(function () { b.disabled = false; });
  });
  $("footer details.how").addEventListener("toggle", renderScale);
  var rz; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { if (DATA) { renderTimeline(); renderScale(); } }, 120); });
  load();
  setInterval(load, 60000);
  setInterval(function () { if (DATA && !editing) { NOW = Date.now(); render(); } }, 30000);
})();
