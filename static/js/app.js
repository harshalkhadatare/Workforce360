/* Vision Infra HR Portal — UI behaviour only. No business logic or authorisation lives here. */
(function () {
  "use strict";
  const body = document.body;

  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; }
  }

  // Sidebar: collapse on desktop, slide-in on mobile
  if (store("hr.sidebar") === "collapsed" && window.innerWidth >= 992) body.classList.add("sidebar-collapsed");
  document.querySelectorAll("[data-sidebar-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (window.innerWidth < 992) {
        body.classList.toggle("sidebar-open");
        btn.setAttribute("aria-expanded", body.classList.contains("sidebar-open"));
      } else {
        body.classList.toggle("sidebar-collapsed");
        store("hr.sidebar", body.classList.contains("sidebar-collapsed") ? "collapsed" : "open");
        btn.setAttribute("aria-expanded", !body.classList.contains("sidebar-collapsed"));
      }
    });
  });
  const backdrop = document.querySelector(".sidebar-backdrop");
  if (backdrop) backdrop.addEventListener("click", function () { body.classList.remove("sidebar-open"); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") body.classList.remove("sidebar-open"); });

  // Toasts
  document.querySelectorAll(".toast-item").forEach(function (t) {
    const close = function () { t.style.transition = "opacity .2s"; t.style.opacity = 0; setTimeout(function () { t.remove(); }, 200); };
    const btn = t.querySelector(".close");
    if (btn) btn.addEventListener("click", close);
    if (!t.classList.contains("error")) setTimeout(close, 6000);
  });

  // Show / hide password
  document.querySelectorAll("[data-toggle-password]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const input = document.getElementById(btn.getAttribute("data-toggle-password"));
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.innerHTML = show ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
    });
  });

  // Confirmation dialog for forms/buttons with data-confirm
  const modalEl = document.getElementById("confirmModal");
  let pendingForm = null;
  let pendingSubmitter = null;
  if (modalEl && window.bootstrap) {
    const modal = new bootstrap.Modal(modalEl);
    document.addEventListener("submit", function (e) {
      const form = e.target;
      const submitter = e.submitter;
      const msg = (submitter && submitter.dataset.confirm) || form.dataset.confirm;
      if (!msg || form.dataset.confirmed === "1") return;
      e.preventDefault();
      pendingForm = form; pendingSubmitter = submitter;
      modalEl.querySelector("[data-confirm-text]").textContent = msg;
      const ok = modalEl.querySelector("[data-confirm-ok]");
      ok.className = "btn " + ((submitter && submitter.dataset.confirmTone) || form.dataset.confirmTone || "btn-primary");
      modal.show();
    }, true);
    modalEl.querySelector("[data-confirm-ok]").addEventListener("click", function () {
      if (!pendingForm) return;
      pendingForm.dataset.confirmed = "1";
      if (pendingSubmitter && pendingSubmitter.name) {
        const h = document.createElement("input");
        h.type = "hidden"; h.name = pendingSubmitter.name; h.value = pendingSubmitter.value;
        pendingForm.appendChild(h);
      }
      modal.hide();
      pendingForm.requestSubmit ? pendingForm.requestSubmit() : pendingForm.submit();
    });
  }

  // Loading state on submit (prevents double-submits; the server is still idempotent)
  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (e.defaultPrevented || form.method.toLowerCase() !== "post") return;
    setTimeout(function () {
      if (e.defaultPrevented) return;
      form.querySelectorAll("button[type=submit]").forEach(function (b) {
        b.disabled = true;
        if (b === e.submitter || !e.submitter) b.insertAdjacentHTML("afterbegin", '<span class="spinner-border" role="status" aria-hidden="true"></span>');
      });
    }, 0);
  });

  // Clickable table rows (keyboard accessible)
  document.querySelectorAll("tr[data-href]").forEach(function (tr) {
    tr.classList.add("row-link");
    tr.tabIndex = 0;
    tr.addEventListener("click", function (e) { if (!e.target.closest("a,button,input,form")) window.location = tr.dataset.href; });
    tr.addEventListener("keydown", function (e) { if (e.key === "Enter") window.location = tr.dataset.href; });
  });

  // Auto-submit filter selects
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  // Leave form helpers: toggle half-day session & emergency fields
  function sync(selector, predicate) {
    document.querySelectorAll(selector).forEach(function (el) { el.hidden = !predicate(); });
  }
  const duration = document.getElementById("id_duration_type");
  if (duration) {
    const f = function () { sync("[data-show-when-half]", function () { return duration.value === "HALF"; }); };
    duration.addEventListener("change", f); f();
  }
  const issue = document.getElementById("id_issue_type");
  if (issue) {
    const f = function () {
      const v = issue.value;
      sync("[data-show-in]", function () { return ["MISSING_IN", "INCORRECT_IN", "WRONG_DATE", "WRONG_STATUS", "OTHER"].includes(v); });
      sync("[data-show-out]", function () { return ["MISSING_OUT", "INCORRECT_OUT", "WRONG_DATE", "WRONG_STATUS", "OTHER"].includes(v); });
    };
    issue.addEventListener("change", f); f();
  }
})();
