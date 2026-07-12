(function () {
  var gallery = document.getElementById("produto-gallery");
  var lightbox = document.getElementById("produto-lightbox");
  if (!gallery || !lightbox) return;

  var dataEl = document.getElementById("produto-gallery-data");
  var images = [];
  try {
    images = JSON.parse((dataEl && dataEl.textContent) || "[]");
  } catch (e) {
    images = [];
  }
  if (!images.length) return;

  if (lightbox.parentElement !== document.body) {
    document.body.appendChild(lightbox);
  }
  lightbox.setAttribute("role", "dialog");
  lightbox.setAttribute("aria-modal", "true");
  lightbox.setAttribute("aria-label", "Galeria de imagens do produto");

  var mainBtn = document.getElementById("produto-gallery-main-btn");
  var mainImg = document.getElementById("produto-gallery-main-img");
  var lightboxImg = document.getElementById("produto-lightbox-img");
  var counter = document.getElementById("produto-lightbox-counter");
  var thumbs = gallery.querySelectorAll(".product-gallery__thumb");
  var current = 0;
  var lastFocus = null;

  function setActive(index) {
    current = (index + images.length) % images.length;
    if (mainImg) mainImg.src = images[current];
    thumbs.forEach(function (btn, i) {
      var active = i === current;
      btn.classList.toggle("is-active", active);
      if (active) btn.setAttribute("aria-current", "true");
      else btn.removeAttribute("aria-current");
    });
  }

  function updateLightbox() {
    if (!lightboxImg) return;
    lightboxImg.src = images[current];
    if (counter) counter.textContent = current + 1 + " / " + images.length;
  }

  function openLightbox(index) {
    lastFocus = document.activeElement;
    setActive(index);
    updateLightbox();
    lightbox.hidden = false;
    lightbox.setAttribute("aria-hidden", "false");
    document.body.classList.add("produto-lightbox-open");
    var closeBtn = lightbox.querySelector(".produto-lightbox__close");
    if (closeBtn) closeBtn.focus();
  }

  function closeLightbox() {
    lightbox.hidden = true;
    lightbox.setAttribute("aria-hidden", "true");
    document.body.classList.remove("produto-lightbox-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function step(delta) {
    setActive(current + delta);
    updateLightbox();
  }

  if (mainBtn) {
    mainBtn.addEventListener("click", function (e) {
      e.preventDefault();
      openLightbox(current);
    });
  }

  thumbs.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      var idx = parseInt(btn.getAttribute("data-gallery-index"), 10) || 0;
      openLightbox(idx);
    });
  });

  lightbox.querySelectorAll("[data-gallery-close]").forEach(function (el) {
    el.addEventListener("click", closeLightbox);
  });

  var prevBtn = lightbox.querySelector("[data-gallery-prev]");
  var nextBtn = lightbox.querySelector("[data-gallery-next]");
  if (prevBtn) {
    prevBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      step(-1);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      step(1);
    });
  }

  document.addEventListener("keydown", function (e) {
    if (lightbox.hidden) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft" && images.length > 1) step(-1);
    if (e.key === "ArrowRight" && images.length > 1) step(1);
  });
})();
