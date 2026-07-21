(function () {
  function digits(value) {
    return String(value || "").replace(/\D/g, "");
  }

  function setField(id, value, force) {
    if (value == null || value === "") return;
    var el = document.getElementById(id);
    if (!el) return;
    if (!force && el.value.trim()) return;
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function setSelect(id, value, force) {
    if (!value) return;
    var el = document.getElementById(id);
    if (!el) return;
    if (!force && el.value) return;
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function showStatus(el, message, isError) {
    if (!el) return;
    el.hidden = !message;
    el.textContent = message || "";
    el.style.color = isError ? "var(--danger, #b42318)" : "";
  }

  function applyLookupData(data, opts) {
    if (!data) return;
    var force = !!(opts && opts.forceCompany);
    var map = {
      razao_social: "razao_social",
      company_name: "company_name",
      organization: "organization",
      phone: "phone",
      email: "email",
      address_zip: "address_zip",
      address_street: "address_street",
      address_number: "address_number",
      address_complement: "address_complement",
      address_neighborhood: "address_neighborhood",
      address_city: "address_city",
    };
    var companyKeys = {
      razao_social: true,
      company_name: true,
      organization: true,
    };
    Object.keys(map).forEach(function (key) {
      setField(map[key], data[key], force && companyKeys[key]);
    });
    setSelect("address_state", data.address_state, false);
  }

  function fetchCep(cep, statusEl) {
    var d = digits(cep);
    if (d.length !== 8) return;
    showStatus(statusEl, "Buscando CEP…", false);
    fetch("/api/lookup/cep/" + d)
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, body: body };
        });
      })
      .then(function (res) {
        if (!res.ok || !res.body.ok) {
          showStatus(statusEl, (res.body && res.body.error) || "CEP não encontrado.", true);
          return;
        }
        applyLookupData(res.body.data, { forceCompany: false });
        showStatus(statusEl, "Endereço preenchido.", false);
      })
      .catch(function () {
        showStatus(statusEl, "Falha ao consultar CEP.", true);
      });
  }

  function fetchCnpj(cnpj, statusEl) {
    var d = digits(cnpj);
    if (d.length !== 14) {
      showStatus(statusEl, "Informe um CNPJ com 14 dígitos.", true);
      return;
    }
    showStatus(statusEl, "Buscando CNPJ…", false);
    fetch("/api/lookup/cnpj/" + d)
      .then(function (r) {
        return r.json().then(function (body) {
          return { ok: r.ok, status: r.status, body: body };
        });
      })
      .then(function (res) {
        if (!res.ok || !res.body.ok) {
          showStatus(
            statusEl,
            (res.body && res.body.error) || "CNPJ não encontrado ou indisponível.",
            true
          );
          return;
        }
        applyLookupData(res.body.data, { forceCompany: true });
        showStatus(statusEl, "Dados da empresa preenchidos.", false);
      })
      .catch(function () {
        showStatus(statusEl, "Falha ao consultar CNPJ. Tente novamente.", true);
      });
  }

  function findStatusNear(input, attr) {
    var wrap = input.closest(".br-lookup-inline") || input.parentElement;
    if (wrap) {
      var inWrap = wrap.querySelector("[" + attr + "]");
      if (inWrap) return inWrap;
      var parent = wrap.parentElement;
      if (parent) {
        var sibling = parent.querySelector("[" + attr + "]");
        if (sibling) return sibling;
      }
    }
    return document.querySelector("[" + attr + "]");
  }

  function initBrAddressLookup() {
    document.querySelectorAll("[data-br-cep]").forEach(function (input) {
      var statusEl = findStatusNear(input, "data-br-cep-status");
      input.addEventListener("blur", function () {
        fetchCep(input.value, statusEl);
      });
    });

    document.querySelectorAll("[data-br-cnpj]").forEach(function (input) {
      var wrap = input.closest(".br-lookup-inline") || input.parentElement;
      var statusEl = findStatusNear(input, "data-br-cnpj-status");
      var btn = wrap && wrap.querySelector("[data-br-cnpj-btn]");
      function run(ev) {
        if (ev) {
          ev.preventDefault();
          ev.stopPropagation();
        }
        fetchCnpj(input.value, statusEl);
      }
      input.addEventListener("blur", function () {
        if (digits(input.value).length === 14) run();
      });
      if (btn) btn.addEventListener("click", run);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBrAddressLookup);
  } else {
    initBrAddressLookup();
  }
})();
