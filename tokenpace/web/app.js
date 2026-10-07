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
    var shown = groupList.filter(function (g) { return (ranked[g.id] || []).length; });
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
  function renderRec(r) {
    var s = subs[r.id] || {}, p = r.period || {};
    var rec = el("article", "rec");
    var head = el("div", "head");
    head.appendChild(el("span", "rank", r.rank + "."));
    head.appendChild(el("span", "name", r.name));
    head.appendChild(el("span", "win", (p.label || "") + (r.free ? " · free" : "")));
    head.appendChild(el("span", "chip lv-" + r.level, r.verdict));
    rec.appendChild(head);

    var body = el("div", "body");
    body.appendChild(paceBar(p));
    var pb = el("div", "pacebox");
    pb.appendChild(el("div", "x c-" + r.level, r.free ? "free" : r.level === "blocked" ? "used up" : fmtX(r.need)));
    var reset = p.resets_at ? Date.parse(p.resets_at) : null;
    pb.appendChild(el("div", "rs", r.level === "blocked" && r.released_at ? "frees in " + duration(Date.parse(r.released_at) - NOW)
      : reset ? "resets in " + duration(reset - NOW) : "reset unknown"));
    if (reset) pb.appendChild(el("div", "rs2", whenShort(p.resets_at)));
    body.appendChild(pb);

    var facts = el("div", "facts");
    var win = (s.windows || []).filter(function (w) { return w.kind === p.kind && w.label === p.label; })[0]
      || (s.windows || []).filter(function (w) { return w.kind === p.kind; })[0] || {};
    var amt = win.limit_amount ? amount(win.used_amount) + " of " + amount(win.limit_amount) + (win.unit || s.unit ? " " + (win.unit || s.unit) : "") : null;
    var f1 = el("span"); f1.appendChild(el("b", null, pct(p.used_percent) + " used")); if (amt) f1.appendChild(document.createTextNode(" (" + amt + ")")); facts.appendChild(f1);
    if (typeof p.elapsed_percent === "number") facts.appendChild(el("span", null, pct0(p.elapsed_percent) + " of the " + noun(p.label) + " gone"));
    if (!r.free && r.level !== "blocked" && typeof p.gap_pp === "number" && Math.abs(p.gap_pp) >= 1) {
      var ahead = p.used_percent > p.elapsed_percent;
      var g = el("span"); g.appendChild(el("b", null, (ahead ? "ahead " : "slack ") + Math.round(Math.abs(p.gap_pp)) + " pp")); facts.appendChild(g);
    }
    body.appendChild(facts);
    rec.appendChild(body);

    if (r.short_windows && r.short_windows.length) {
      var sh = el("div", "shorts");
      r.short_windows.forEach(function (w) {
        var c = el("span", "short"); c.title = w.label + ": " + pct(w.used_percent) + " used" + (w.resets_at ? " · resets " + when(w.resets_at) : "");
        c.appendChild(el("span", null, w.label));
        var mini = el("span", "mini"); var mf = el("i"); mf.style.width = clamp(w.used_percent, 0, 100) + "%"; mini.appendChild(mf); c.appendChild(mini);
        c.appendChild(el("b", null, pct0(w.remaining_percent) + " left"));
        if (w.resets_at) c.appendChild(el("span", "m", duration(Date.parse(w.resets_at) - NOW)));
        sh.appendChild(c);
      });
      rec.appendChild(sh);
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
    var shown = 0;
    groupList.forEach(function (g) {
      var rows = ranked[g.id] || []; if (!rows.length) return;
      shown++;
      var sec = el("section", "group"); sec.id = "g-" + g.id;
      var h = label(g, el("h2"));
      var latest = (DATA.subscriptions || []).filter(function (s) { return s.group === g.id && s.observed_at; })
        .map(function (s) { return s.observed_at; }).sort().pop();
      h.appendChild(el("span", "meta", "read " + ago(latest)));
      sec.appendChild(h);
      rows.forEach(function (r) { sec.appendChild(renderRec(r)); });
      box.appendChild(sec);
    });
    var out = adv.left_out || [];
    if (out.length) {
      var sec2 = el("section", "group"); sec2.appendChild(el("h2", null, "Outside the ranking"));
      out.forEach(function (o) {
        var s = subs[o.id] || {};
        var rec = el("article", "rec");
        var head = el("div", "head"); head.appendChild(el("span", "name", o.name));
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
