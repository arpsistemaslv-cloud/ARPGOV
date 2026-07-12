(function () {
  function esc(s) {
    return (s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function formatBrl(value) {
    if (value == null || isNaN(value)) return "—";
    return value.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function initPicker(root) {
    var apiUrl = root.getAttribute("data-api-url");
    var search = root.querySelector("[data-catalog-search]");
    var sphere = root.querySelector("[data-catalog-sphere]");
    var results = root.querySelector("[data-catalog-results]");
    var selectedWrap = root.querySelector("[data-catalog-selected-wrap]");
    var selectedBox = root.querySelector("[data-catalog-selected]");
    var inputsBox = root.querySelector("[data-catalog-inputs]");
    var linesJsonInput = root.querySelector("[data-catalog-lines-json]");
    var summary = root.querySelector("[data-catalog-summary]");
    var totalEl = root.querySelector("[data-catalog-total]");
    var prompt = root.querySelector("[data-catalog-prompt]");
    var empty = root.querySelector("[data-catalog-empty]");
    var moreWrap = root.querySelector("[data-catalog-more]");
    var loadMoreBtn = root.querySelector("[data-catalog-load-more]");
    if (!apiUrl || !results || !selectedBox || !inputsBox) return;

    var selected = new Map();
    var currentPage = 1;
    var totalPages = 0;
    var lastQuery = "";
    var lastSphere = "";
    var debounceTimer = null;
    var loading = false;

    function parseInitialLines() {
      function parseRaw(raw) {
        if (!raw) return [];
        try {
          var data = JSON.parse(raw);
          if (!Array.isArray(data)) return [];
          return data
            .map(function (row) {
              var id = parseInt(row.id, 10);
              var qty = parseInt(row.qty, 10);
              if (isNaN(id) || id <= 0) return null;
              return { id: id, qty: !isNaN(qty) && qty > 0 ? qty : 1 };
            })
            .filter(Boolean);
        } catch (e) {
          return [];
        }
      }

      var fromAttr = parseRaw(root.getAttribute("data-initial-lines"));
      if (fromAttr.length) return fromAttr;
      if (linesJsonInput) {
        return parseRaw(linesJsonInput.value);
      }
      return [];
    }

    function getQty(id) {
      var item = selected.get(id);
      return item && item.qty ? item.qty : 1;
    }

    function parseQtyInput(raw) {
      var n = parseInt(String(raw || "").trim(), 10);
      return isNaN(n) || n < 1 ? 1 : n;
    }

    function setQty(id, qty) {
      var item = selected.get(id);
      if (!item) return;
      item.qty = parseQtyInput(qty);
      selected.set(id, item);
    }

    function updateChipSubtotal(chip, item) {
      if (!chip || !item) return;
      var sub = chip.querySelector(".catalog-picker__chip-subtotal");
      var subtotal = lineTotal(item);
      if (subtotal != null) {
        if (!sub) {
          sub = document.createElement("span");
          sub.className = "catalog-picker__chip-subtotal";
          var removeBtn = chip.querySelector(".catalog-picker__chip-remove");
          chip.insertBefore(sub, removeBtn);
        }
        sub.textContent = formatBrl(subtotal);
      } else if (sub) {
        sub.remove();
      }
    }

    function refreshQtyUi(input) {
      var id = parseInt(input.getAttribute("data-qty-id"), 10);
      if (isNaN(id)) return;
      var item = selected.get(id);
      if (!item) return;
      syncInputs();
      updateChipSubtotal(input.closest(".catalog-picker__chip"), item);
      updateSummary();
    }

    function commitQtyInput(input) {
      var id = parseInt(input.getAttribute("data-qty-id"), 10);
      if (isNaN(id)) return;
      var qty = parseQtyInput(input.value);
      input.value = String(qty);
      setQty(id, qty);
      refreshQtyUi(input);
    }

    function lineTotal(item) {
      if (item.unit_price == null || isNaN(item.unit_price)) return null;
      return item.unit_price * getQty(item.id);
    }

    function syncLeadSaleTotal(total, hasPrice) {
      var hidden = document.getElementById("value_brl");
      var display = document.getElementById("lead_sale_total_display");
      var show = hasPrice && selected.size > 0;
      if (display) {
        display.textContent = show ? formatBrl(total) : "—";
      }
      if (hidden) {
        hidden.value = show ? total.toFixed(2) : "";
        hidden.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }

    function updateSummary() {
      var n = selected.size;
      if (summary) {
        summary.textContent = n === 1 ? "1 produto selecionado" : n + " produtos selecionados";
      }
      if (selectedWrap) selectedWrap.hidden = n === 0;

      var total = 0;
      var hasPrice = false;
      selected.forEach(function (item) {
        var lt = lineTotal(item);
        if (lt != null) {
          hasPrice = true;
          total += lt;
        }
      });
      if (totalEl) {
        if (hasPrice && n > 0) {
          totalEl.hidden = false;
          totalEl.textContent = "Valor total da venda: " + formatBrl(total);
        } else {
          totalEl.hidden = true;
          totalEl.textContent = "";
        }
      }
      syncLeadSaleTotal(total, hasPrice);
      emitChange();
    }

    function emitChange() {
      var items = [];
      selected.forEach(function (item) {
        items.push({ id: item.id, title: item.title || "" });
      });
      root.dispatchEvent(
        new CustomEvent("catalog-picker:change", {
          bubbles: true,
          detail: { items: items },
        })
      );
    }

    function linesPayload() {
      var rows = [];
      selected.forEach(function (item) {
        rows.push({ id: item.id, qty: getQty(item.id) });
      });
      return rows;
    }

    function syncLinesJson() {
      if (!linesJsonInput) return;
      linesJsonInput.value = JSON.stringify(linesPayload());
    }

    function seedFromLines(lines) {
      lines.forEach(function (line) {
        if (selected.has(line.id)) return;
        selected.set(line.id, {
          id: line.id,
          title: "Produto #" + line.id,
          qty: line.qty,
          unit_price: null,
          unit_price_label: "",
        });
      });
      renderSelected();
    }

    function syncInputs() {
      if (!inputsBox) return;
      var jsonInput = inputsBox.querySelector("[data-catalog-lines-json]");
      inputsBox.innerHTML = "";
      if (jsonInput) {
        inputsBox.appendChild(jsonInput);
        linesJsonInput = jsonInput;
      } else if (linesJsonInput) {
        inputsBox.appendChild(linesJsonInput);
      } else {
        jsonInput = document.createElement("input");
        jsonInput.type = "hidden";
        jsonInput.name = "catalog_lines_json";
        jsonInput.setAttribute("data-catalog-lines-json", "");
        inputsBox.appendChild(jsonInput);
        linesJsonInput = jsonInput;
      }
      selected.forEach(function (item) {
        var idInput = document.createElement("input");
        idInput.type = "hidden";
        idInput.name = "catalog_item_id";
        idInput.value = String(item.id);
        inputsBox.appendChild(idInput);

        var qtyInput = document.createElement("input");
        qtyInput.type = "hidden";
        qtyInput.name = "catalog_qty";
        qtyInput.value = String(getQty(item.id));
        inputsBox.appendChild(qtyInput);
      });
      syncLinesJson();
      updateSummary();
    }

    function renderSelected() {
      selectedBox.innerHTML = "";
      selected.forEach(function (item) {
        var chip = document.createElement("div");
        chip.className = "catalog-picker__chip";
        chip.setAttribute("data-chip-id", String(item.id));
        var subtotal = lineTotal(item);
        var subLabel =
          subtotal != null
            ? '<span class="catalog-picker__chip-subtotal">' + esc(formatBrl(subtotal)) + "</span>"
            : "";
        chip.innerHTML =
          '<span class="catalog-picker__chip-title">' +
          esc(item.title) +
          "</span>" +
          '<label class="catalog-picker__chip-qty-label">Qtd' +
          '<input type="number" class="catalog-picker__chip-qty" min="1" step="1" inputmode="numeric" value="' +
          getQty(item.id) +
          '" data-qty-id="' +
          item.id +
          '" aria-label="Quantidade de ' +
          esc(item.title) +
          '">' +
          "</label>" +
          subLabel +
          '<button type="button" class="catalog-picker__chip-remove" data-remove-id="' +
          item.id +
          '" aria-label="Remover ' +
          esc(item.title) +
          '">×</button>';
        selectedBox.appendChild(chip);
      });
      syncInputs();
    }

    function toggleItem(item) {
      if (selected.has(item.id)) {
        selected.delete(item.id);
      } else {
        item.qty = 1;
        selected.set(item.id, item);
      }
      renderSelected();
      renderResultRows();
    }

    function buildRow(item) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "catalog-picker__row";
      if (selected.has(item.id)) row.classList.add("catalog-picker__row--selected");
      row.setAttribute("data-item-id", String(item.id));

      var thumb = item.image_url
        ? '<span class="catalog-picker__row-thumb"><img src="' +
          esc(item.image_url) +
          '" alt="" loading="lazy"></span>'
        : '<span class="catalog-picker__row-thumb catalog-picker__row-thumb--empty" aria-hidden="true"></span>';

      var meta = ["#" + item.id, item.sphere];
      if (item.manufacturer) meta.push(item.manufacturer);
      if (item.valid_until) meta.push("Val. " + item.valid_until);

      row.innerHTML =
        thumb +
        '<span class="catalog-picker__row-body">' +
        '<span class="catalog-picker__row-title">' +
        esc(item.title) +
        "</span>" +
        '<span class="catalog-picker__row-meta">' +
        esc(meta.join(" · ")) +
        "</span>" +
        (item.ata_owner_company
          ? '<span class="catalog-picker__row-owner">' + esc(item.ata_owner_company) + "</span>"
          : "") +
        "</span>" +
        '<span class="catalog-picker__row-price">' +
        esc(item.unit_price_label) +
        "</span>" +
        '<span class="catalog-picker__row-action" aria-hidden="true">' +
        (selected.has(item.id) ? "✓" : "+") +
        "</span>";

      row.addEventListener("click", function () {
        toggleItem(item);
      });
      return row;
    }

    var lastItems = [];

    function renderResultRows() {
      if (!lastItems.length) return;
      results.innerHTML = "";
      lastItems.forEach(function (item) {
        results.appendChild(buildRow(item));
      });
    }

    function fetchItems(page, append) {
      if (loading) return;
      var q = search ? search.value.trim() : "";
      var sp = sphere ? sphere.value : "";
      if (page === 1) {
        lastQuery = q;
        lastSphere = sp;
      }
      loading = true;
      if (loadMoreBtn) loadMoreBtn.disabled = true;

      var params = new URLSearchParams();
      if (q) params.set("q", q);
      if (sp) params.set("sphere", sp);
      params.set("page", String(page));
      params.set("per_page", "15");

      fetch(apiUrl + "?" + params.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          var items = data.items || [];
          currentPage = data.page || 1;
          totalPages = data.pages || 0;

          if (!append) {
            lastItems = items;
            results.innerHTML = "";
          } else {
            lastItems = lastItems.concat(items);
          }

          items.forEach(function (item) {
            results.appendChild(buildRow(item));
          });

          var hasQuery = q.length >= 2 || sp;
          if (prompt) prompt.hidden = hasQuery;
          if (results) results.hidden = !hasQuery || (items.length === 0 && !append);
          if (empty) empty.hidden = !hasQuery || items.length > 0 || append;
          if (moreWrap) moreWrap.hidden = !(hasQuery && currentPage < totalPages);
        })
        .catch(function () {
          if (empty) {
            empty.hidden = false;
            empty.textContent = "Não foi possível buscar produtos. Tente novamente.";
          }
        })
        .finally(function () {
          loading = false;
          if (loadMoreBtn) loadMoreBtn.disabled = false;
        });
    }

    function scheduleSearch() {
      if (debounceTimer) window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(function () {
        currentPage = 1;
        fetchItems(1, false);
      }, 320);
    }

    function loadInitialSelected() {
      var lines = parseInitialLines();
      if (!lines.length) {
        syncLinesJson();
        updateSummary();
        return;
      }
      seedFromLines(lines);
      var ids = lines.map(function (l) {
        return l.id;
      });
      fetch(apiUrl + "?ids=" + encodeURIComponent(ids.join(",")), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          var byId = {};
          (data.items || []).forEach(function (item) {
            byId[item.id] = item;
          });
          lines.forEach(function (line) {
            var item = byId[line.id];
            if (item) {
              item.qty = line.qty;
              selected.set(item.id, item);
            }
          });
          renderSelected();
        })
        .catch(function () {
          updateSummary();
        });
    }

    function prepareFormSubmit() {
      selectedBox.querySelectorAll("[data-qty-id]").forEach(function (input) {
        commitQtyInput(input);
      });
      syncInputs();
    }

    selectedBox.addEventListener("click", function (e) {
      if (e.target.closest(".catalog-picker__chip-qty")) return;
      var btn = e.target.closest("[data-remove-id]");
      if (!btn) return;
      var id = parseInt(btn.getAttribute("data-remove-id"), 10);
      if (!isNaN(id)) {
        selected.delete(id);
        renderSelected();
        renderResultRows();
      }
    });

    selectedBox.addEventListener("input", function (e) {
      var input = e.target.closest("[data-qty-id]");
      if (!input) return;
      var raw = input.value.trim();
      if (raw === "") return;
      var qty = parseInt(raw, 10);
      if (isNaN(qty) || qty < 1) return;
      setQty(parseInt(input.getAttribute("data-qty-id"), 10), qty);
      refreshQtyUi(input);
    });

    selectedBox.addEventListener("change", function (e) {
      var input = e.target.closest("[data-qty-id]");
      if (!input) return;
      commitQtyInput(input);
    });

    selectedBox.addEventListener(
      "blur",
      function (e) {
        var input = e.target.closest("[data-qty-id]");
        if (!input) return;
        commitQtyInput(input);
      },
      true
    );

    if (search) search.addEventListener("input", scheduleSearch);
    if (sphere) {
      sphere.addEventListener("change", function () {
        currentPage = 1;
        fetchItems(1, false);
      });
    }
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", function () {
        if (currentPage < totalPages) fetchItems(currentPage + 1, true);
      });
    }

    var form = root.closest("form");
    if (form) {
      form.addEventListener(
        "submit",
        function () {
          prepareFormSubmit();
        },
        true
      );
      if (root.getAttribute("data-catalog-required") === "1") {
        form.addEventListener(
          "submit",
          function (e) {
            prepareFormSubmit();
            if (selected.size === 0) {
              e.preventDefault();
              window.alert("Selecione ao menos um produto do catálogo.");
              if (search) search.focus();
            }
          },
          true
        );
      }
    }

    loadInitialSelected();
  }

  document.querySelectorAll("[data-catalog-picker]").forEach(initPicker);
})();
