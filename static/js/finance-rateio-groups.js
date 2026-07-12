(function () {
  function initFinanceRateioGroups() {
    document.querySelectorAll("[data-finance-group]").forEach(function (group) {
      var btn = group.querySelector("[data-finance-toggle]");
      var body = group.querySelector(".finance-rateio-group__body");
      var icon = group.querySelector(".finance-rateio-group__icon");
      if (!btn || !body) return;

      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        open = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        body.hidden = !open;
        if (icon) icon.textContent = open ? "−" : "+";
        group.classList.toggle("finance-rateio-group--open", open);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFinanceRateioGroups);
  } else {
    initFinanceRateioGroups();
  }
})();
