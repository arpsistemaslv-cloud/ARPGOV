(function () {
  function truncate(text, max) {
    text = (text || "").trim();
    if (text.length <= max) return text;
    return text.slice(0, max - 1).trim() + "…";
  }

  function clientLabel(client) {
    if (!client) return "";
    return (
      (client.organization || "").trim() ||
      (client.name || "").trim()
    );
  }

  function buildTitle(client, products) {
    var org = clientLabel(client);
    var productPart = "Produto";
    if (products && products.length) {
      var first = products[0];
      productPart = truncate(first.title || "Produto #" + first.id, 50);
      if (products.length > 1) {
        productPart += " (+" + (products.length - 1) + ")";
      }
    }
    var title = "Adesão — " + productPart;
    if (org) title += " — " + org;
    if (title.length > 200) title = title.slice(0, 199).trim() + "…";
    return title;
  }

  function initForm(form) {
    var display = form.querySelector("[data-lead-auto-title]");
    var hint = form.querySelector("[data-lead-auto-title-hint]");
    if (!display) return;

    var state = { client: null, products: [] };

    function update() {
      var ready = state.client && state.products.length > 0;
      if (!ready) {
        display.textContent = "—";
        if (hint) hint.hidden = false;
        return;
      }
      if (hint) hint.hidden = true;
      display.textContent = buildTitle(state.client, state.products);
    }

    form.addEventListener("portal-client-picker:change", function (e) {
      state.client = e.detail && e.detail.client ? e.detail.client : null;
      update();
    });

    form.addEventListener("catalog-picker:change", function (e) {
      state.products = (e.detail && e.detail.items) || [];
      update();
    });

    var initialClient = (function () {
      var root = form.querySelector("[data-portal-client-picker]");
      if (!root) return null;
      try {
        var raw = root.getAttribute("data-initial-client");
        if (!raw || raw === "null") return null;
        var data = JSON.parse(raw);
        return data && data.id ? data : null;
      } catch (err) {
        return null;
      }
    })();
    if (initialClient) {
      state.client = initialClient;
      update();
    }
  }

  document.querySelectorAll("form[data-lead-auto-title-form]").forEach(initForm);
})();
