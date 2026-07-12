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

  function fmtBrl(n) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return "R$ " + n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtPct(n) {
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 }) + "%";
  }

  function fmtPctPlain(n) {
    return n.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 4 });
  }

  function tierLabel(tier) {
    var pct = tier.percent_total;
    var pctTxt = String(pct).replace(".", ",");
    if (pctTxt.indexOf(",") >= 0) {
      pctTxt = pctTxt.replace(/0+$/, "").replace(/,$/, "");
    }
    var mode = tier.with_seller ? "com vendedor" : "sem vendedor";
    return pctTxt + "% — " + mode;
  }

  function initCommissionTierPicker() {
    var jsonEl = document.getElementById("commission_projects_json");
    var projectSel = document.getElementById("commission_project_id");
    var tierSel = document.getElementById("commission_tier_id");
    var valueInput = document.getElementById("value_brl");
    var rateioWrap = document.getElementById("commission_rateio_wrap");
    var rateioLabel = document.getElementById("commission_rateio_label");
    var rateioBody = document.getElementById("commission_rateio_body");
    var repField = document.getElementById("rep_commission_brl");
    var noteField = document.getElementById("rep_commission_note");
    if (!jsonEl || !projectSel || !tierSel) return;

    var projects = [];
    try {
      projects = JSON.parse(jsonEl.textContent || "[]");
    } catch (e) {
      return;
    }

    function currentProject() {
      var id = parseInt(projectSel.value, 10);
      if (!id) return null;
      for (var i = 0; i < projects.length; i++) {
        if (projects[i].id === id) return projects[i];
      }
      return null;
    }

    function selectedTier() {
      var id = parseInt(tierSel.value, 10);
      if (!id) return null;
      var project = currentProject();
      if (!project) return null;
      for (var i = 0; i < (project.tiers || []).length; i++) {
        if (project.tiers[i].id === id) return project.tiers[i];
      }
      return null;
    }

    function sellerSplit(tier) {
      if (!tier || !tier.splits) return null;
      for (var i = 0; i < tier.splits.length; i++) {
        if (tier.splits[i].recipient_kind === "seller") return tier.splits[i];
      }
      return null;
    }

    function updateRateio() {
      var tier = selectedTier();
      var base = parseMoney(valueInput && valueInput.value);

      if (!tier || !tier.splits || !tier.splits.length) {
        if (rateioWrap) rateioWrap.hidden = true;
        if (rateioBody) rateioBody.innerHTML = "";
        return;
      }

      if (rateioWrap) rateioWrap.hidden = false;
      if (rateioLabel) {
        rateioLabel.innerHTML =
          "<strong>Rateio</strong> — " +
          tierLabel(tier) +
          ' <span class="meta">(salve o lead para gravar)</span>';
      }

      if (rateioBody) {
        rateioBody.innerHTML = "";
        tier.splits.forEach(function (s) {
          var amount = base !== null ? (base * s.share_percent) / 100 : null;
          var tr = document.createElement("tr");
          tr.innerHTML =
            "<td>" +
            s.label +
            "</td><td>" +
            fmtPct(s.share_percent) +
            "</td><td>" +
            fmtBrl(amount) +
            '</td><td class="meta">pendente</td>';
          rateioBody.appendChild(tr);
        });
      }

      var seller = sellerSplit(tier);
      if (repField) {
        if (tier.with_seller && seller && base !== null) {
          repField.value = ((base * seller.share_percent) / 100).toFixed(2);
        } else if (!tier.with_seller) {
          repField.value = "";
        }
      }

      if (noteField) {
        if (tier.with_seller && seller && base !== null) {
          var sellerAmt = ((base * seller.share_percent) / 100).toFixed(2);
          noteField.value =
            "Faixa " +
            tierLabel(tier) +
            " — R$ " +
            sellerAmt +
            " para o vendedor (" +
            fmtPctPlain(seller.share_percent) +
            "% da operação).";
        } else if (!tier.with_seller) {
          noteField.value = "Faixa " + tierLabel(tier) + " — rateio entre sócios (sem vendedor).";
        }
      }
    }

    function rebuildTiers() {
      var project = currentProject();
      var keep = tierSel.value;
      tierSel.innerHTML = '<option value="">—</option>';
      if (!project) {
        updateRateio();
        return;
      }
      (project.tiers || []).forEach(function (t) {
        var opt = document.createElement("option");
        opt.value = String(t.id);
        opt.textContent = t.label;
        tierSel.appendChild(opt);
      });
      if (keep) tierSel.value = keep;
      updateRateio();
    }

    projectSel.addEventListener("change", function () {
      tierSel.value = "";
      rebuildTiers();
    });
    if (valueInput) valueInput.addEventListener("input", updateRateio);
    tierSel.addEventListener("change", updateRateio);

    if (projectSel.value) {
      rebuildTiers();
      var preset = tierSel.getAttribute("data-selected");
      if (preset) tierSel.value = preset;
      updateRateio();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCommissionTierPicker);
  } else {
    initCommissionTierPicker();
  }
})();
