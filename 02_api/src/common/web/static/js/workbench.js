// 실습 모드: 작업자 선택, 검증/개발 작업대 동작
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const store = {
    get(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) {} },
  };

  // ---------- 작업자 ----------
  const actorSelect = $("[data-actor]");
  function actor() { return actorSelect ? actorSelect.value : ""; }
  function actorDept() {
    const opt = actorSelect && actorSelect.selectedOptions[0];
    return opt ? opt.dataset.dept || "" : "";
  }
  if (actorSelect) {
    // ?as=이름 으로 들어오면 작업자를 지정한다 (부서원별 바로가기 링크)
    const as = new URLSearchParams(location.search).get("as");
    if (as && [...actorSelect.options].some((o) => o.value === as)) store.set("actor", as);
    actorSelect.value = store.get("actor");
    actorSelect.addEventListener("change", () => {
      store.set("actor", actorSelect.value);
      if (actorSelect.value) window.toast("작업자 변경", `${actorSelect.selectedOptions[0].textContent} (으)로 작업합니다.`);
      applyView();
    });
  }

  // 작업대 화면 보조: 부서 불일치 안내, 내 항목 필터, 담당 강조
  function applyView() {
    const page = $("[data-desk]");
    if (!page) return;
    const warn = $("#actor-warning");
    if (warn) {
      const need = page.dataset.desk;
      warn.hidden = actorDept() === need;
      warn.querySelector("[data-warn-text]").textContent = actor()
        ? `현재 작업자(${actor()})는 ${actorDept() === "QA" ? "검증부서" : "개발부서"} 소속입니다. 이 화면의 작업은 ${need === "QA" ? "검증부서" : "개발부서"} 작업자만 할 수 있습니다.`
        : `상단에서 ${need === "QA" ? "검증부서" : "개발부서"} 작업자를 선택하세요.`;
    }
    const mineOnly = store.get("qaFilter") === "mine";
    document.querySelectorAll("[data-filter]").forEach((tab) => tab.classList.toggle("active", tab.dataset.filter === (mineOnly ? "mine" : "all")));
    document.querySelectorAll("tr[data-assignee]").forEach((row) => {
      const mine = row.dataset.assignee === actor();
      row.classList.toggle("mine", mine);
      row.hidden = mineOnly && !mine;
    });
    const count = $("[data-mine-count]");
    if (count) count.textContent = document.querySelectorAll(`tr[data-assignee="${CSS.escape(actor())}"]`).length;
  }
  document.addEventListener("live:refreshed", applyView);
  applyView();

  // ---------- 호출 ----------
  async function work(url, body = {}, okTitle) {
    try {
      const data = await window.apiPost(url, { actor: actor(), ...body });
      if (okTitle) window.toast(okTitle, "");
      window.scheduleRefresh(50);
      return data;
    } catch (err) {
      window.toast("처리할 수 없음", err.message);
      throw err;
    }
  }

  function openDialog(id, fill) {
    const dlg = document.getElementById(id);
    if (!dlg) return;
    dlg.querySelectorAll("input, textarea, select").forEach((el) => (el.value = el.dataset.default || ""));
    fill(dlg);
    dlg.showModal();
  }

  // 확인 대화상자 (되돌리기 어려운 작업)
  function confirmBox(title, body, okLabel) {
    return new Promise((resolve) => {
      const dlg = document.getElementById("confirm-dialog");
      if (!dlg) return resolve(true);
      dlg.querySelector("h3").textContent = title;
      dlg.querySelector("p").textContent = body;
      const ok = dlg.querySelector("[data-confirm-ok]");
      ok.textContent = okLabel;
      const done = (value) => { dlg.close(); ok.onclick = null; dlg.onclose = null; resolve(value); };
      ok.onclick = () => done(true);
      dlg.onclose = () => resolve(false);
      dlg.showModal();
    });
  }

  async function withBusy(btn, fn) {
    const label = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>' + btn.textContent.trim();
    try { await fn(); } catch (e) {} finally { btn.disabled = false; btn.innerHTML = label; }
  }

  document.addEventListener("click", async (e) => {
    const t = (sel) => e.target.closest(sel);
    let el;

    if ((el = t("[data-filter]"))) {
      e.preventDefault();
      store.set("qaFilter", el.dataset.filter);
      return applyView();
    }
    if ((el = t("[data-action=work-start]"))) {
      try {
        const data = await window.apiPost("/api/work/start");
        window.toast("실습 시작", `사이클 #${data.cycle_id} - 개발부서가 최초 버전을 발행하면 검증이 시작됩니다.`);
        setTimeout(() => location.assign("/"), 600);
      } catch (err) { window.toast("시작할 수 없음", err.message); }
      return;
    }
    if ((el = t("[data-action=work-abort]"))) {
      try { await window.apiPost("/api/work/abort"); location.reload(); } catch (err) { window.toast("중단할 수 없음", err.message); }
      return;
    }

    // ----- 검증 작업대 -----
    if ((el = t("[data-exec]"))) {
      return withBusy(el, async () => {
        const data = await work(`/api/work/results/${el.dataset.exec}/execute`);
        window.toast(`실행 결과: ${data.passed ? "PASS" : "FAIL"}`, data.passed ? "자동 실행이 통과했습니다. 판정은 직접 하세요." : data.log.split("\n")[0]);
      });
    }
    if ((el = t("[data-pass]"))) return work(`/api/work/results/${el.dataset.pass}/judge`, { result: "PASS" });
    if ((el = t("[data-fail]"))) {
      const d = el.dataset;
      return openDialog("issue-dialog", (dlg) => {
        dlg.querySelector("[name=result_id]").value = d.fail;
        dlg.querySelector("[data-tc]").textContent = `${d.tc} ${d.name}`;
        dlg.querySelector("[name=title]").value = `[${d.feature}] ${d.name} 실패`;
        dlg.querySelector("[name=description]").value = d.log ? `${d.tc} 실행 결과:\n${d.log}` : "";
        dlg.querySelector("[name=severity]").value = "";
        dlg.querySelector("[data-linked]").hidden = !d.issue;
        dlg.querySelector("[data-linked] b").textContent = d.issue || "";
      });
    }
    if ((el = t("[data-draft-issue]"))) {
      const dlg = el.closest("dialog");
      return withBusy(el, async () => {
        const data = await work(`/api/work/draft/issue/${dlg.querySelector("[name=result_id]").value}`);
        dlg.querySelector("[name=title]").value = data.title;
        dlg.querySelector("[name=description]").value = data.description;
        dlg.querySelector("[name=severity]").value = data.severity;
        window.toast("AI 초안", data.source === "llm" ? "Claude가 실패 로그를 분석해 작성했습니다." : "LLM 미사용(mock) 템플릿으로 작성했습니다.");
      });
    }
    if ((el = t("[data-submit-issue]"))) {
      const dlg = el.closest("dialog");
      const f = (name) => dlg.querySelector(`[name=${name}]`).value;
      return withBusy(el, async () => {
        const data = await work("/api/work/issues", { result_id: f("result_id"), title: f("title"), severity: f("severity"), description: f("description") });
        dlg.close();
        window.toast(data.created ? `${data.key} 등록` : `${data.key}에 연결`, "해당 항목을 FAIL로 판정했습니다.");
      });
    }
    if ((el = t("[data-verify]"))) {
      const ok = el.dataset.ok === "1";
      return work(`/api/work/issues/${el.dataset.verify}/verify`, { ok }, ok ? "수정 확인" : "재현 - 재오픈");
    }
    if ((el = t("[data-transfer]"))) {
      if (!(await confirmBox("개발부서로 이관할까요?", "이번 검증을 완료하고 미해결 이슈를 개발부서에 넘깁니다. 다음 차례는 개발부서입니다.", "Dev 이관"))) return;
      return work("/api/work/transfer", {}, "개발부서로 이관했습니다");
    }
    if ((el = t("[data-approve]"))) {
      if (!(await confirmBox("현재 버전을 확정할까요?", "검증을 완료하고 이 버전을 릴리즈 버전으로 확정합니다. 실습 사이클이 종료됩니다.", "버전 확정"))) return;
      return work("/api/work/approve", {}, "버전을 확정했습니다");
    }

    // ----- 개발 작업대 -----
    if ((el = t("[data-start-fix]"))) return work(`/api/work/issues/${el.dataset.startFix}/start`, {}, `${el.dataset.startFix} 착수`);
    if ((el = t("[data-fix]"))) {
      const d = el.dataset;
      return openDialog("fix-dialog", (dlg) => {
        dlg.querySelector("[name=key]").value = d.fix;
        dlg.querySelector("[data-key]").textContent = `${d.fix} ${d.title}`;
        dlg.querySelector("[data-desc]").textContent = d.desc || "(설명 없음)";
      });
    }
    if ((el = t("[data-draft-fix]"))) {
      const dlg = el.closest("dialog");
      return withBusy(el, async () => {
        const data = await work(`/api/work/draft/fix/${dlg.querySelector("[name=key]").value}`);
        dlg.querySelector("[name=fix_note]").value = data.fix_note;
      });
    }
    if ((el = t("[data-submit-fix]"))) {
      const dlg = el.closest("dialog");
      return withBusy(el, async () => {
        await work(`/api/work/issues/${dlg.querySelector("[name=key]").value}/fix`, { fix_note: dlg.querySelector("[name=fix_note]").value });
        dlg.close();
        window.toast("수정 완료", "다음 버전 발행 시 반영됩니다.");
      });
    }
    if ((el = t("[data-draft-release]"))) {
      return withBusy(el, async () => {
        const data = await work("/api/work/draft/release");
        $("#release-note").value = data.release_note || $("#release-note").value;
      });
    }
    if ((el = t("[data-release]"))) {
      const note = $("#release-note") ? $("#release-note").value : "";
      if (!(await confirmBox(`v${el.dataset.release}을(를) 발행할까요?`, "발행하면 검증부서의 검증이 시작됩니다.", "버전 발행"))) return;
      return withBusy(el, async () => {
        const data = await work("/api/work/release", { release_note: note });
        if ($("#release-note")) $("#release-note").value = "";
        window.toast(`v${data.version} 발행`, "검증부서 차례입니다.");
      });
    }
  });
})();
