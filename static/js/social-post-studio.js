(function () {
  function initSocialPostStudio() {
    var form = document.getElementById("social_studio_form");
    var img = document.getElementById("social_preview_img");
    var frame = document.getElementById("social_preview_frame");
    var copyBtn = document.getElementById("copy_product_url");
    var urlInput = document.getElementById("product_public_url");
    var copyWaBtn = document.getElementById("copy_whatsapp_url");
    var waInput = document.getElementById("whatsapp_public_url");
    if (!form || !img) return;

    var previewBase = img.getAttribute("src").split("?")[0];
    var timer = null;

    function bindCopy(btn, input, defaultLabel) {
      if (!btn || !input) return;
      btn.addEventListener("click", function () {
        input.select();
        input.setSelectionRange(0, 99999);
        var text = input.value;
        var done = function () {
          btn.textContent = "Copiado!";
          window.setTimeout(function () {
            btn.textContent = defaultLabel;
          }, 1800);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done);
        } else {
          document.execCommand("copy");
          done();
        }
      });
    }

    bindCopy(copyBtn, urlInput, "Copiar link");
    bindCopy(copyWaBtn, waInput, "Copiar WhatsApp");

    function selectedValue(name) {
      var el = form.querySelector('[name="' + name + '"]:checked');
      return el ? el.value : "";
    }

    function checkboxOn(name) {
      var el = form.querySelector('[name="' + name + '"]');
      return el && el.checked ? "1" : "0";
    }

    function buildPreviewUrl() {
      var params = new URLSearchParams();
      params.set("format", selectedValue("format") || "instagram_feed");
      params.set("layout", selectedValue("layout") || "gov_pro");
      params.set("show_price", checkboxOn("show_price"));
      params.set("show_manufacturer", checkboxOn("show_manufacturer"));
      params.set("show_sphere", checkboxOn("show_sphere"));
      params.set("show_category", checkboxOn("show_category"));
      params.set("show_product_link", checkboxOn("show_product_link"));
      params.set("show_whatsapp", checkboxOn("show_whatsapp"));
      var headline = form.querySelector('[name="headline"]');
      var cta = form.querySelector('[name="cta"]');
      var linkCta = form.querySelector('[name="link_cta"]');
      var waCta = form.querySelector('[name="whatsapp_cta"]');
      if (headline && headline.value.trim()) params.set("headline", headline.value.trim());
      if (cta && cta.value.trim()) params.set("cta", cta.value.trim());
      if (linkCta && linkCta.value.trim()) params.set("link_cta", linkCta.value.trim());
      if (waCta && waCta.value.trim()) params.set("whatsapp_cta", waCta.value.trim());
      params.set("_", String(Date.now()));
      return previewBase + "?" + params.toString();
    }

    function updatePreviewFrame() {
      var format = selectedValue("format") || "instagram_feed";
      if (frame) {
        frame.classList.remove("social-studio__preview-frame--vertical", "social-studio__preview-frame--square");
        if (format === "instagram_feed") {
          frame.classList.add("social-studio__preview-frame--square");
        } else {
          frame.classList.add("social-studio__preview-frame--vertical");
        }
      }
    }

    function refreshPreview() {
      updatePreviewFrame();
      img.src = buildPreviewUrl();
    }

    form.addEventListener("submit", function () {
      form.querySelectorAll('input[type="checkbox"][name^="show_"]').forEach(function (cb) {
        if (!cb.checked) {
          var hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = cb.name;
          hidden.value = "0";
          form.appendChild(hidden);
        }
      });
    });

    function scheduleRefresh() {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(refreshPreview, 280);
    }

    form.addEventListener("change", scheduleRefresh);
    form.addEventListener("input", scheduleRefresh);
    updatePreviewFrame();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSocialPostStudio);
  } else {
    initSocialPostStudio();
  }
})();
