(function () {
  function parseMoney(raw) {
    if (!raw) return null;
    var s = String(raw).trim().replace(/\s/g, "");
    if (!s) return null;
    if (s.indexOf(",") >= 0) {
      s = s.replace(/\./g, "").replace(",", ".");
    }
    var n = parseFloat(s);
    return isNaN(n) ? null : n;
  }

  function fmtDecimalBrl(n) {
    if (n === null || n === undefined || isNaN(n)) return "";
    return n.toLocaleString("pt-BR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function syncMonthlyFromAnnual() {
    var annualInput = document.getElementById("goal_annual_brl");
    var monthlyInput = document.getElementById("goal_monthly_brl");
    if (!annualInput || !monthlyInput) return;
    var annual = parseMoney(annualInput.value);
    monthlyInput.value = annual !== null ? fmtDecimalBrl(annual / 12) : "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var annualInput = document.getElementById("goal_annual_brl");
    if (!annualInput) return;
    annualInput.addEventListener("input", syncMonthlyFromAnnual);
    annualInput.addEventListener("change", syncMonthlyFromAnnual);
    syncMonthlyFromAnnual();
  });
})();
