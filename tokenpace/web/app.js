(function () {
  "use strict";
  var data = null;
  var editing = false;
  var TOKEN_KEY = "tokenpace-token";
  var whenFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  var timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
  var SOURCES = { codex: "Codex CLI login", claude_code: "Claude Code login", openrouter_free: "OpenRouter API key",
                  manual: "entered by hand", push: "pushed from another machine", demo: "demo data" };

  (function takeTokenFromHash() {
    var m = /(?:^|[#&])token=([^&]+)/.exec(location.hash);
    if (m) {
      localStorage.setItem(TOKEN_KEY, decodeURIComponent(m[1]));
      history.replaceState(null, "", location.pathname + location.search);
    }
  })();
  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    var t = localStorage.getItem(TOKEN_KEY);
    if (t) headers.Authorization = "Bearer " + t;
    opts.headers = headers;
    opts.cache = "no-store";
    return fetch(path, opts);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function groupById(id) { return (data.groups || []).filter(function (g) { return g.id === id; })[0] || { id: id, label: id }; }
  function heading(tag, g) {
    var h = el(tag);
    if (g.logo_url) {
      var img = el("img", "logo");
      img.src = g.logo_url;
      img.alt = "";
      h.appendChild(img);
    }
    h.appendChild(document.createTextNode(g.label));
    return h;
  }
  function pct(v) { return (Math.round(v * 10) / 10).toString() + "%"; }
  function duration(ms) {
    if (ms <= 0) return "now";
    var min = Math.floor(ms / 60000);
    if (min < 1) return "under 1 min";
    var d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
    if (d > 0) return d + "d " + h + "h";
    if (h > 0) return h + "h " + (m < 10 ? "0" : "") + m + "m";
    return m + " min";
  }
  function ago(iso) {
    if (!iso) return "never";
    var ms = Date.now() - Date.parse(iso);
    if (ms < 60000) return "under 1 min ago";
    return duration(ms) + " ago";
  }
  function color(rem) { return rem >= 40 ? "var(--ok)" : rem >= 15 ? "var(--amber)" : "var(--danger)"; }
  function when(iso) { return whenFmt.format(new Date(iso)).replace(",", ""); }
  function fmtNum(v) { return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); }
  function fmtX(v) { return (Math.round(v * 10) / 10).toFixed(1) + "×"; }

  function windowState(w) {
    var reset = w.resets_at ? Date.parse(w.resets_at) : null;
    var passed = reset !== null && reset <= Date.now();
    return { reset: reset, passed: passed, remaining: passed ? 100 : w.remaining_percent, estimated: passed };
  }

  function statusLine(s) {
    var p = el("div", "status");
    if (s.status === "ok") {
      p.textContent = "Read " + ago(s.observed_at) + (s.message ? " · " + s.message : "");
    } else if (s.status === "stale") {
      p.className = "status warn";
      p.textContent = "Old data: read " + ago(s.observed_at) + (s.message ? ". " + s.message : "");
    } else {
      p.className = s.manual || s.provider === "push" ? "status warn" : "status bad";
      p.textContent = s.message || "Unavailable.";
    }
    return p;
  }

  function renderWindow(w) {
    var st = windowState(w);
    var box = el("div", "win");
    var row = el("div", "row");
    row.appendChild(el("span", "label", w.label));
    var big = el("span", "big", (st.estimated ? "≈" : "") + pct(st.remaining));
    big.appendChild(el("small", null, "left"));
    row.appendChild(big);
    box.appendChild(row);
    var bar = el("div", "bar");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(Math.round(st.remaining)));
    bar.setAttribute("aria-label", w.label + ": " + pct(st.remaining) + " left");
    var fill = el("div", "fill" + (st.estimated ? " est" : ""));
    fill.style.width = Math.max(0, Math.min(100, st.remaining)) + "%";
    fill.style.background = color(st.remaining);
    bar.appendChild(fill);
    box.appendChild(bar);
    if (w.limit_amount) {
      box.appendChild(el("div", "amount", fmtNum(w.used_amount) + " of " + fmtNum(w.limit_amount) + " " + (w.unit || "") + " used"));
    }
    var r = el("div", "reset");
    if (st.reset === null) {
      r.textContent = "Reset time unknown";
    } else if (st.passed) {
      r.appendChild(el("b", null, "Already renewed"));
      r.appendChild(el("span", "when", " · " + when(w.resets_at) + " · waiting for a new reading"));
    } else {
      r.appendChild(el("b", null, "Resets in " + duration(st.reset - Date.now())));
      r.appendChild(el("span", "when", " · " + when(w.resets_at)));
    }
    box.appendChild(r);
    return box;
  }

  function renderCard(s) {
    var card = el("article", "card" + (s.windows.length ? "" : " dim"));
    var top = el("div", "top");
    top.appendChild(el("span", "name", s.name));
    if (s.plan) top.appendChild(el("span", "pill", s.plan));
    card.appendChild(top);
    card.appendChild(statusLine(s));
    if (!s.windows.length) {
      var u = el("div", "unavail");
      u.appendChild(el("b", null, "Unavailable"));
      u.appendChild(document.createTextNode(s.manual ? "Enter its numbers so it joins the ranking." : "No reading until valid data arrives."));
      card.appendChild(u);
    }
    s.windows.forEach(function (w) { card.appendChild(renderWindow(w)); });
    var src = SOURCES[s.provider] || s.provider;
    if (s.provider === "push" && s.source) src = "pushed by " + s.source;
    card.appendChild(el("div", "src", "Source: " + src + (s.use_via ? " · use via " + s.use_via : "")));
    if (s.manual) card.appendChild(reportControls(s));
    return card;
  }

  function field(form, label, name, type, attrs, full) {
    var l = el("label", full ? "full" : null, label);
    var i = document.createElement("input");
    i.name = name; i.type = type;
    Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
    l.appendChild(i);
    form.appendChild(l);
    return i;
  }

  function reportControls(s) {
    var wrap = el("div");
    var open = el("button", "linkish", "Update numbers");
    open.type = "button";
    wrap.appendChild(open);
    open.addEventListener("click", function () {
      editing = true;
      open.hidden = true;
      var f = el("form", "report");
      var cur = s.windows[0] || {};
      f.appendChild(el("div", "hint full", "Copy the numbers from the provider's console: the % used, or the amount used and the total."));
      var pctIn = field(f, "% used", "used_percent", "number", { min: "0", max: "100", step: "0.1", inputmode: "decimal" });
      var plan = field(f, "Plan", "plan", "text", { maxlength: "40", placeholder: "e.g. Lite" });
      var used = field(f, "Used", "used_amount", "number", { min: "0", step: "any", inputmode: "decimal" });
      var total = field(f, "Total", "limit_amount", "number", { min: "0", step: "any", inputmode: "decimal" });
      var unit = field(f, "Unit", "unit", "text", { maxlength: "20", placeholder: "credits" });
      var reset = field(f, "Renews on", "resets_at", "datetime-local", { required: "required" });
      var days = field(f, "Period (days)", "period_days", "number", { min: "1", max: "366", step: "1", value: String(s.period_days || 30) });
      if (s.plan) plan.value = s.plan;
      if (s.unit) unit.value = s.unit;
      if (cur.resets_at) {
        var d = new Date(Date.parse(cur.resets_at) - new Date().getTimezoneOffset() * 60000);
        reset.value = d.toISOString().slice(0, 16);
      }
      var msg = el("div", "hint full");
      var actions = el("div", "full");
      var save = el("button", null, "Save");
      save.type = "submit";
      var cancel = el("button", "linkish", "Cancel");
      cancel.type = "button";
      cancel.style.marginLeft = "12px";
      actions.appendChild(save); actions.appendChild(cancel);
      f.appendChild(msg); f.appendChild(actions);
      cancel.addEventListener("click", function () { editing = false; render(); });
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var body = { id: s.id, plan: plan.value.trim() || null, unit: unit.value.trim() || null,
                     period_days: days.value ? Number(days.value) : null };
        if (used.value !== "" || total.value !== "") {
          body.used_amount = Number(used.value); body.limit_amount = Number(total.value);
        } else if (pctIn.value !== "") {
          body.used_percent = Number(pctIn.value);
        } else { msg.textContent = "Enter the % used or the amounts."; return; }
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

  function renderAdvice() {
    var box = document.getElementById("advice");
    box.textContent = "";
    var adv = data.advice || { groups: {} };
    document.getElementById("advice-method").textContent = adv.method ? "How the order works: " + adv.method : "";
    (data.groups || []).forEach(function (g) {
      var rows = (adv.groups || {})[g.id] || [];
      if (!rows.length) return;
      var lane = el("div", "lane");
      lane.appendChild(heading("h3", g));
      rows.forEach(function (r) {
        var p = r.period;
        var rec = el("div", "rec");
        var top = el("div", "row");
        var who = el("span", "who");
        who.appendChild(el("span", "n", r.rank + "."));
        who.appendChild(document.createTextNode(r.name));
        who.appendChild(el("small", null, (p.label || "") + (r.free ? " · free" : "")));
        top.appendChild(who);
        top.appendChild(el("span", "verdict lv-" + r.level, r.verdict));
        rec.appendChild(top);
        var bar = el("div", "pace");
        bar.setAttribute("role", "img");
        bar.setAttribute("aria-label", "Used " + pct(p.used_percent) + ", period elapsed " + pct(p.elapsed_percent));
        var usedP = Math.max(0, Math.min(100, p.used_percent)), elP = Math.max(0, Math.min(100, p.elapsed_percent));
        var band = el("div", elP >= usedP ? "slackband" : "overband");
        band.style.left = Math.min(usedP, elP) + "%"; band.style.width = Math.abs(elP - usedP) + "%";
        var u = el("div", "used"); u.style.width = usedP + "%";
        var m = el("div", "mark"); m.style.left = "calc(" + elP + "% - 1px)";
        bar.appendChild(band); bar.appendChild(u); bar.appendChild(m);
        rec.appendChild(bar);
        var gap = r.free ? "reserve for light tasks" : "needed pace " + fmtX(r.need);
        var reset = p.resets_at ? " · resets in " + duration(Date.parse(p.resets_at) - Date.now()) : "";
        var why = r.level === "blocked" && r.released_at
          ? r.reason + " Frees up in " + duration(Date.parse(r.released_at) - Date.now()) + "."
          : r.reason + ".";
        rec.appendChild(el("div", "why lead", gap + reset));
        rec.appendChild(el("div", "why", why));
        if (r.use_via) rec.appendChild(el("div", "via", r.use_via));
        if (r.caveat) rec.appendChild(el("div", "cav", r.caveat + "."));
        lane.appendChild(rec);
      });
      box.appendChild(lane);
    });
    var out = adv.left_out || [];
    if (out.length) {
      var lane2 = el("div", "lane");
      lane2.appendChild(el("h3", null, "Outside the ranking"));
      out.forEach(function (o) {
        var rec = el("div", "rec");
        rec.appendChild(el("div", "who", o.name + " · " + groupById(o.group).label));
        rec.appendChild(el("div", "why", o.reason));
        lane2.appendChild(rec);
      });
      box.appendChild(lane2);
    }
    if (!box.childNodes.length) box.appendChild(el("div", "unavail", "Not enough readings to rank."));
  }

  function renderSummary() {
    var box = document.getElementById("summary");
    box.textContent = "";
    var next = null, low = null;
    data.subscriptions.forEach(function (s) {
      var g = groupById(s.group).label;
      s.windows.forEach(function (w) {
        var st = windowState(w);
        var who = s.name + " (" + g + ") · " + w.label;
        if (st.reset !== null && !st.passed && (!next || st.reset < next.t)) next = { t: st.reset, who: who };
        if (!st.passed && (!low || st.remaining < low.rem)) low = { rem: st.remaining, who: who };
      });
    });
    function tile(k, v, hero) { var t = el("div", "tile" + (hero ? " hero" : "")); t.appendChild(el("div", "k", k)); t.appendChild(el("div", "v", v)); box.appendChild(t); }
    var g0 = (data.groups || [])[0];
    var rows = g0 ? ((data.advice || {}).groups || {})[g0.id] || [] : [];
    var first = rows.filter(function (r) { return r.level === "use"; })[0];
    tile("Use first" + (g0 && data.groups.length > 1 ? " (" + g0.label + ")" : ""), first
      ? first.name + " · " + (first.period.label || "").toLowerCase() + ", needed pace " + fmtX(first.need)
      : !rows.length ? "—"
      : rows.every(function (r) { return r.level === "blocked"; }) ? "Everything is used up for now"
      : rows.every(function (r) { return r.level === "blocked" || r.free; }) ? "Only free tiers are available"
      : "Nothing has slack right now", true);
    tile("Next reset", next ? next.who + " in " + duration(next.t - Date.now()) : "—");
    tile("Least quota left", low ? low.who + ": " + pct(low.rem) : "—");
  }

  function render() {
    if (!data) return;
    document.title = data.title;
    document.getElementById("title").textContent = data.title;
    document.getElementById("version").textContent = " v" + data.version;
    document.getElementById("android").hidden = !data.apk_url;
    if (!editing) {
      var groups = document.getElementById("groups");
      groups.textContent = "";
      (data.groups || []).forEach(function (g) {
        var items = data.subscriptions.filter(function (s) { return s.group === g.id; });
        if (!items.length) return;
        groups.appendChild(heading("h2", g));
        var grid = el("div", "grid");
        items.forEach(function (s) { grid.appendChild(renderCard(s)); });
        groups.appendChild(grid);
      });
    }
    renderAdvice();
    renderSummary();
    document.getElementById("about-text").textContent = "Share left and time to reset of each usage window, in your browser's time zone. "
      + "The page reloads every minute; the server reads the subscriptions every " + Math.round(data.refresh_seconds / 60) + " min.";
    document.getElementById("meta").textContent = "Updated " + ago(data.last_collect) + " · " + timeFmt.format(new Date());
  }

  function load() {
    return api("api/usage").then(function (r) {
      if (r.status === 401) {
        document.getElementById("gate").hidden = false;
        throw new Error("this server needs its token");
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (j) {
      data = j;
      document.getElementById("gate").hidden = true;
      document.getElementById("error").hidden = true;
      render();
    }).catch(function (e) {
      var box = document.getElementById("error");
      box.hidden = false;
      box.textContent = "Could not read the data (" + e.message + ")." + (data ? " Showing the last reading." : "");
    });
  }

  document.getElementById("gate").addEventListener("submit", function (ev) {
    ev.preventDefault();
    localStorage.setItem(TOKEN_KEY, document.getElementById("gate-token").value.trim());
    load();
  });
  document.getElementById("refresh").addEventListener("click", function () {
    var b = this;
    b.disabled = true;
    api("api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .catch(function () {})
      .then(function () { return new Promise(function (ok) { setTimeout(ok, 5000); }); })
      .then(load)
      .then(function () { b.disabled = false; });
  });

  load();
  setInterval(load, 60000);
  setInterval(render, 15000);
})();
