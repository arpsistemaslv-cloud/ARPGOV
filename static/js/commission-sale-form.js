(function () {
  function initCommissionSaleForm() {
    var host = document.getElementById("participant-rows");
    var addBtn = document.getElementById("add-participant");
    if (!host || !addBtn) return;

    function bindRow(row) {
      var btn = row.querySelector("[data-remove-participant]");
      if (btn) {
        btn.addEventListener("click", function () {
          var rows = host.querySelectorAll("[data-participant-row]");
          if (rows.length <= 1) {
            row.querySelectorAll("input").forEach(function (inp) {
              inp.value = "";
            });
            return;
          }
          row.remove();
        });
      }
    }

    host.querySelectorAll("[data-participant-row]").forEach(bindRow);

    addBtn.addEventListener("click", function () {
      var first = host.querySelector("[data-participant-row]");
      if (!first) return;
      var clone = first.cloneNode(true);
      clone.querySelectorAll("input").forEach(function (inp) {
        inp.value = "";
      });
      host.appendChild(clone);
      bindRow(clone);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCommissionSaleForm);
  } else {
    initCommissionSaleForm();
  }
})();
