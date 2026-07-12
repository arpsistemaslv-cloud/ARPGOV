(function () {
  function initLeadPipelineStages() {
    var stageSel = document.getElementById("stage");
    var host = document.getElementById("pipeline-stage-fields");
    var jsonInput = document.getElementById("pipeline_data_json");
    if (!stageSel || !host) return;

    var store = {};
    var previousStage = stageSel.value;

    function loadStore() {
      if (!jsonInput) return;
      try {
        var parsed = JSON.parse(jsonInput.value || "{}");
        store = parsed && typeof parsed === "object" ? parsed : {};
      } catch (e) {
        store = {};
      }
    }

    function blockHasContent(block) {
      if (!block || typeof block !== "object") return false;
      return !!(
        block.date ||
        block.forecast_date ||
        (block.notes && String(block.notes).trim()) ||
        (Array.isArray(block.attachments) && block.attachments.length)
      );
    }

    function commitPanel(stageKey) {
      if (!stageKey) return;
      var panel = document.getElementById("pipeline-panel-" + stageKey);
      if (!panel) return;

      var block = Object.assign({}, store[stageKey] || {});

      var dateEl = panel.querySelector('[name="pipeline_date_' + stageKey + '"]');
      var forecastEl = panel.querySelector('[name="pipeline_forecast_' + stageKey + '"]');
      var notesEl = panel.querySelector('[name="pipeline_notes_' + stageKey + '"]');

      if (dateEl) block.date = dateEl.value || null;
      if (forecastEl) block.forecast_date = forecastEl.value || null;
      if (notesEl) block.notes = (notesEl.value || "").trim() || null;

      var attList = panel.querySelector(".lead-pipeline-attachments");
      if (attList) {
        var attachments = [];
        attList.querySelectorAll("li[data-relpath]").forEach(function (li) {
          var removeCb = li.querySelector('input[type="checkbox"]');
          if (removeCb && removeCb.checked) return;
          attachments.push({
            relpath: li.getAttribute("data-relpath"),
            name: li.getAttribute("data-name") || "Anexo",
          });
        });
        block.attachments = attachments;
      }

      if (blockHasContent(block)) {
        store[stageKey] = block;
      } else if (store[stageKey]) {
        delete store[stageKey];
      }
    }

    function loadPanel(stageKey) {
      if (!stageKey) return;
      var panel = document.getElementById("pipeline-panel-" + stageKey);
      if (!panel) return;
      var block = store[stageKey] || {};

      var dateEl = panel.querySelector('[name="pipeline_date_' + stageKey + '"]');
      var forecastEl = panel.querySelector('[name="pipeline_forecast_' + stageKey + '"]');
      var notesEl = panel.querySelector('[name="pipeline_notes_' + stageKey + '"]');

      if (dateEl) dateEl.value = block.date || "";
      if (forecastEl) forecastEl.value = block.forecast_date || "";
      if (notesEl) notesEl.value = block.notes || "";
    }

    function syncJsonInput() {
      if (jsonInput) jsonInput.value = JSON.stringify(store);
    }

    function updatePanels() {
      var current = stageSel.value;
      var anyVisible = false;

      host.querySelectorAll("[data-pipeline-panel]").forEach(function (panel) {
        var key = panel.getAttribute("data-pipeline-panel");
        var show = key === current;
        panel.hidden = !show;
        panel.querySelectorAll("input, textarea, select").forEach(function (el) {
          el.disabled = !show;
        });
        if (show) anyVisible = true;
      });

      host.hidden = !anyVisible;
      var hint = document.getElementById("pipeline-fields-hint");
      if (hint) hint.hidden = !anyVisible;
    }

    function onStageChange() {
      if (previousStage && previousStage !== stageSel.value) {
        commitPanel(previousStage);
      }
      previousStage = stageSel.value;
      updatePanels();
      loadPanel(stageSel.value);
      syncJsonInput();
    }

    function prepareSubmit() {
      commitPanel(stageSel.value);
      syncJsonInput();
      updatePanels();
    }

    loadStore();
    updatePanels();
    loadPanel(stageSel.value);

    stageSel.addEventListener("change", onStageChange);

    var form = stageSel.closest("form");
    if (form) {
      form.addEventListener("submit", prepareSubmit, true);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLeadPipelineStages);
  } else {
    initLeadPipelineStages();
  }
})();
