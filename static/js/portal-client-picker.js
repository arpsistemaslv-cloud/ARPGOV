(function () {
  function esc(s) {
    return (s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function parseInitialClient(root) {
    var raw = root.getAttribute("data-initial-client");
    if (!raw || raw === "null") return null;
    try {
      var data = JSON.parse(raw);
      if (!data || !data.id) return null;
      return data;
    } catch (e) {
      return null;
    }
  }

  function initPicker(root) {
    var apiUrl = root.getAttribute("data-api-url");
    var idInput = root.querySelector("[data-portal-client-id]");
    var searchWrap = root.querySelector("[data-portal-client-search-wrap]");
    var search = root.querySelector("[data-portal-client-search]");
    var selectedWrap = root.querySelector("[data-portal-client-selected]");
    var card = root.querySelector("[data-portal-client-card]");
    var clearBtn = root.querySelector("[data-portal-client-clear]");
    var results = root.querySelector("[data-portal-client-results]");
    var prompt = root.querySelector("[data-portal-client-prompt]");
    var empty = root.querySelector("[data-portal-client-empty]");
    var moreWrap = root.querySelector("[data-portal-client-more]");
    var loadMoreBtn = root.querySelector("[data-portal-client-load-more]");
    if (!apiUrl || !idInput || !results || !card) return;

    var selected = parseInitialClient(root);
    var currentPage = 1;
    var totalPages = 0;
    var lastQuery = "";
    var debounceTimer = null;
    var loading = false;

    function renderCard(client) {
      if (!client) {
        card.innerHTML = "";
        return;
      }
      var parts = [];
      if (client.organization) parts.push(esc(client.organization));
      if (client.cnpj) parts.push(esc(client.cnpj));
      var line1 = parts.join(" · ");
      var line2 = [];
      if (client.email) line2.push(esc(client.email));
      if (client.phone) line2.push(esc(client.phone));
      var meta = line1;
      if (line2.length) meta += (meta ? "<br>" : "") + line2.join(" · ");
      if (client.sphere) meta += (meta ? "<br>" : "") + esc(client.sphere);
      card.innerHTML =
        '<p class="portal-client-picker__card-title">' +
        esc(client.name) +
        "</p>" +
        (meta ? '<p class="portal-client-picker__card-meta meta">' + meta + "</p>" : "");
    }

    function setSelected(client) {
      selected = client;
      if (client) {
        idInput.value = String(client.id);
        renderCard(client);
        if (selectedWrap) selectedWrap.hidden = false;
        if (searchWrap) searchWrap.hidden = true;
        results.hidden = true;
        if (empty) empty.hidden = true;
        if (moreWrap) moreWrap.hidden = true;
      } else {
        idInput.value = "";
        renderCard(null);
        if (selectedWrap) selectedWrap.hidden = true;
        if (searchWrap) searchWrap.hidden = false;
      }
      root.dispatchEvent(
        new CustomEvent("portal-client-picker:change", {
          bubbles: true,
          detail: { client: client },
        })
      );
    }

    function buildRow(item) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "catalog-picker__row portal-client-picker__row";
      if (selected && selected.id === item.id) {
        row.classList.add("catalog-picker__row--selected");
      }
      row.setAttribute("data-client-id", String(item.id));

      var meta = [item.email];
      if (item.organization) meta.push(item.organization);
      if (item.cnpj) meta.push(item.cnpj);

      row.innerHTML =
        '<span class="catalog-picker__row-body">' +
        '<span class="catalog-picker__row-title">' +
        esc(item.name) +
        "</span>" +
        '<span class="catalog-picker__row-meta">' +
        esc(meta.join(" · ")) +
        "</span>" +
        "</span>" +
        '<span class="catalog-picker__row-action" aria-hidden="true">' +
        (selected && selected.id === item.id ? "✓" : "+") +
        "</span>";

      row.addEventListener("click", function () {
        setSelected(item);
      });
      return row;
    }

    function fetchItems(page, append) {
      if (loading) return;
      var q = search ? search.value.trim() : "";
      if (page === 1) lastQuery = q;
      if (q.length < 2) {
        results.hidden = true;
        if (empty) empty.hidden = true;
        if (moreWrap) moreWrap.hidden = true;
        if (prompt) prompt.hidden = false;
        return;
      }
      loading = true;
      if (loadMoreBtn) loadMoreBtn.disabled = true;

      var params = new URLSearchParams();
      params.set("q", q);
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
          if (prompt) prompt.hidden = items.length > 0 || !!data.hint;
          if (!append) results.innerHTML = "";
          items.forEach(function (item) {
            results.appendChild(buildRow(item));
          });
          results.hidden = items.length === 0 && !append;
          if (empty) empty.hidden = items.length > 0 || currentPage > 1;
          if (moreWrap) moreWrap.hidden = currentPage >= totalPages;
        })
        .catch(function () {
          if (!append) results.innerHTML = "";
          results.hidden = true;
          if (empty) {
            empty.hidden = false;
            empty.textContent = "Não foi possível buscar clientes. Tente novamente.";
          }
        })
        .finally(function () {
          loading = false;
          if (loadMoreBtn) loadMoreBtn.disabled = false;
        });
    }

    function scheduleSearch() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        currentPage = 1;
        fetchItems(1, false);
      }, 280);
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        setSelected(null);
        if (search) {
          search.value = "";
          search.focus();
        }
      });
    }

    if (search) {
      search.addEventListener("input", scheduleSearch);
      var initialSearch = root.getAttribute("data-initial-search") || "";
      if (!selected && initialSearch.trim().length >= 2) {
        scheduleSearch();
      }
    }

    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", function () {
        if (currentPage < totalPages) fetchItems(currentPage + 1, true);
      });
    }

    if (selected) {
      setSelected(selected);
    }

    var form = root.closest("form");
    if (form && root.getAttribute("data-portal-client-required") === "1") {
      form.addEventListener(
        "submit",
        function (e) {
          if (!idInput.value) {
            e.preventDefault();
            window.alert("Selecione um cliente cadastrado antes de criar o lead.");
            if (search && searchWrap && !searchWrap.hidden) search.focus();
          }
        },
        true
      );
    }
  }

  document.querySelectorAll("[data-portal-client-picker]").forEach(initPicker);
})();
