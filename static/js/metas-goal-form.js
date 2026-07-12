(function () {
  var MONTH_SHORT = [
    "",
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
  ];

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

  function periodMonths() {
    var startEl = document.getElementById("goal_start_month");
    var endEl = document.getElementById("goal_end_month");
    var start = startEl ? parseInt(startEl.value, 10) : 1;
    var end = endEl ? parseInt(endEl.value, 10) : 12;
    if (isNaN(start)) start = 1;
    if (isNaN(end)) end = 12;
    if (start > end) return 12;
    return end - start + 1;
  }

  function periodLabelShort() {
    var startEl = document.getElementById("goal_start_month");
    var endEl = document.getElementById("goal_end_month");
    var start = startEl ? parseInt(startEl.value, 10) : 1;
    var end = endEl ? parseInt(endEl.value, 10) : 12;
    if (isNaN(start)) start = 1;
    if (isNaN(end)) end = 12;
    if (start === 1 && end === 12) return "ano inteiro";
    return (MONTH_SHORT[start] || start) + "–" + (MONTH_SHORT[end] || end);
  }

  function syncMonthlyFromAnnual() {
    var annualInput = document.getElementById("goal_annual_brl");
    var monthlyInput = document.getElementById("goal_monthly_brl");
    var hint = document.getElementById("goal_monthly_hint");
    if (!annualInput || !monthlyInput) return;
    var annual = parseMoney(annualInput.value);
    var months = periodMonths();
    monthlyInput.value = annual !== null ? fmtDecimalBrl(annual / months) : "";
    if (hint) {
      hint.textContent =
        "Calculado: meta total ÷ " +
        months +
        " meses (" +
        periodLabelShort() +
        ").";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var annualInput = document.getElementById("goal_annual_brl");
    var startEl = document.getElementById("goal_start_month");
    var endEl = document.getElementById("goal_end_month");
    if (!annualInput) return;
    annualInput.addEventListener("input", syncMonthlyFromAnnual);
    annualInput.addEventListener("change", syncMonthlyFromAnnual);
    if (startEl) {
      startEl.addEventListener("change", syncMonthlyFromAnnual);
    }
    if (endEl) {
      endEl.addEventListener("change", syncMonthlyFromAnnual);
    }
    syncMonthlyFromAnnual();
  });
})();
