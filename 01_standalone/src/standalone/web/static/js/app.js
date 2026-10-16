// Release Desk - live dashboard behaviors (SSE, partial refresh, toasts, dialogs)
(function () {
  const TOAST_TYPES = {
    "release.published": "릴리즈 발행",
    "issues.transferred": "이슈 이관",
    "release.approved": "릴리즈 승인",
    "cycle.completed": "사이클 완료",
    "cycle.failed": "사이클 실패",
    "cycle.error": "오류",
    "cycle.cancelled": "사이클 중단",
    "issue.created": "이슈 등록",
    "issue.fixed": "수정 완료",
  };
  const $ = (sel, root = document) => root.querySelector(sel);

  // ---------- theme ----------
  try {
    const saved = localStorage.getItem("theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (e) {}
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-theme-toggle]")) return;
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (err) {}
  });

  // ---------- toast ----------
  function toast(title, body) {
    const box = $(".toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = `<div class="t-title"></div><div class="t-body"></div>`;
    el.querySelector(".t-title").textContent = title;
    el.querySelector(".t-body").textContent = body || "";
    box.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }
  window.toast = toast;

  // ---------- partial refresh ----------
  let refreshTimer = null;
  function scheduleRefresh(delay = 350) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, delay);
  }
  function isEditing() {
    // 대화상자가 열려 있거나 화면 안의 입력 칸에 포커스가 있으면 갱신을 미룬다 (입력 내용 보호)
    if (document.querySelector("dialog[open]")) return true;
    const el = document.activeElement;
    return !!(el && el.closest("#page") && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
  }
  async function refresh() {
    if (isEditing()) return scheduleRefresh(1500);
    try {
      const res = await fetch(location.href, { headers: { "X-Partial": "1" } });
      if (!res.ok) return;
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const kept = {};
      document.querySelectorAll("[data-preserve][id]").forEach((el) => (kept[el.id] = el.value));
      doc.querySelectorAll("[data-live][id]").forEach((fresh) => {
        const cur = document.getElementById(fresh.id);
        if (cur) cur.replaceWith(fresh);
      });
      Object.entries(kept).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el && value) el.value = value;
      });
      document.dispatchEvent(new CustomEvent("live:refreshed"));
    } catch (e) {}
  }
  window.refreshLive = refresh;
  window.scheduleRefresh = scheduleRefresh;

  // ---------- activity indicator (test progress) ----------
  function showActivity(p) {
    const el = $("#activity");
    if (!el) return;
    el.classList.add("on");
    $(".act-text", el).textContent = `테스트 실행 ${p.done}/${p.total} · ${p.test_case_id} ${p.passed ? "PASS" : "FAIL"}`;
    $(".progress > span", el).style.width = `${Math.round((p.done * 100) / p.total)}%`;
    if (p.done === p.total) setTimeout(() => el.classList.remove("on"), 1200);
  }

  // ---------- live timeline ----------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function prependTimeline(ev) {
    document.querySelectorAll("[data-timeline]").forEach((list) => {
      const li = document.createElement("li");
      li.className = "fresh";
      li.innerHTML = `<span class="ts">${escapeHtml((ev.ts || "").slice(11, 19))}</span>
        <span class="actor">${escapeHtml(ev.actor)}</span>
        <span>${escapeHtml(ev.message)}<span class="type">${escapeHtml(ev.type)}</span></span>`;
      list.prepend(li);
      const empty = list.parentElement.querySelector(".empty");
      if (empty) empty.remove();
      const max = Number(list.dataset.max || 0);
      if (max) while (list.children.length > max) list.lastElementChild.remove();
    });
  }

  // ---------- SSE ----------
  function connect() {
    const url = document.body.dataset.stream;
    if (!url) return;
    const es = new EventSource(url);
    const status = $("#conn");
    es.onopen = () => status && (status.textContent = "연결됨");
    es.onerror = () => status && (status.textContent = "재연결 중");
    es.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.type === "test.progress") return showActivity(ev.payload);
      if (ev.type === "system.reset") return location.assign(location.pathname);
      if (ev.id) prependTimeline(ev);
      if (TOAST_TYPES[ev.type]) toast(TOAST_TYPES[ev.type], ev.message);
      scheduleRefresh();
    };
  }

  // ---------- actions ----------
  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
    return data;
  }
  window.apiPost = post;

  document.addEventListener("click", async (e) => {
    const row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a, button")) return location.assign(row.dataset.href);

    const start = e.target.closest("[data-action=start-cycle]");
    if (start) {
      start.disabled = true;
      start.innerHTML = '<span class="spinner"></span>시작 중';
      try {
        const data = await post(start.dataset.url || "/api/cycles");
        toast("사이클 시작", `사이클 #${data.cycle_id}이(가) 시작되었습니다.`);
        if (location.search.includes("cycle=")) location.assign(location.pathname);
        else scheduleRefresh(50);
      } catch (err) {
        toast("시작할 수 없음", err.message);
        scheduleRefresh(50);
      }
      return;
    }

    const opener = e.target.closest("[data-dialog]");
    if (opener) return document.getElementById(opener.dataset.dialog)?.showModal();

    const close = e.target.closest("[data-close]");
    if (close) return close.closest("dialog")?.close();

    const confirmReset = e.target.closest("[data-action=reset]");
    if (confirmReset) {
      try {
        await post(confirmReset.dataset.url || "/api/reset");
        location.assign(location.pathname.split("/").slice(0, 2).join("/") || "/");
      } catch (err) {
        confirmReset.closest("dialog")?.close();
        toast("초기화할 수 없음", err.message);
      }
    }
  });

  connect();
})();
