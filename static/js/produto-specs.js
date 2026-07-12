(function () {
  var btn = document.getElementById("produto-tech-toggle");
  var panel = document.getElementById("produto-tech-panel");
  if (!btn || !panel) return;

  function setOpen(open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    panel.hidden = !open;
    btn.classList.toggle("produto-tech-toggle--open", open);
  }

  btn.addEventListener("click", function () {
    setOpen(panel.hidden);
  });

  setOpen(false);
})();
