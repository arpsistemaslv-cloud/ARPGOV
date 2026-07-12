(function () {
  function initLojaCategorySearch() {
    var input = document.getElementById("loja_cat_search");
    var filters = document.getElementById("loja_cat_filters");
    var emptyMsg = document.getElementById("loja_cat_search_empty");
    if (!input || !filters) return;

    var groups = filters.querySelectorAll(".loja-cat-group");
    var children = filters.querySelectorAll(".loja-cat-link--child");

    function normalize(text) {
      return (text || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .trim();
    }

    function setHidden(el, hide) {
      if (hide) el.classList.add("loja-cat-link--hidden");
      else el.classList.remove("loja-cat-link--hidden");
    }

    function filterCategories() {
      var q = normalize(input.value);
      var visibleChildren = 0;

      if (!q) {
        children.forEach(function (link) {
          setHidden(link, false);
        });
        groups.forEach(function (group) {
          setHidden(group, false);
        });
        if (emptyMsg) emptyMsg.hidden = true;
        return;
      }

      var groupsWithMatches = {};

      children.forEach(function (link) {
        var name = normalize(link.textContent);
        var show = name.indexOf(q) !== -1;
        setHidden(link, !show);
        if (show) {
          visibleChildren += 1;
          var pid = link.getAttribute("data-cat-parent");
          if (pid) groupsWithMatches[pid] = true;
        }
      });

      groups.forEach(function (group) {
        var pid = String(group.getAttribute("data-cat-parent"));
        var labelEl = group.querySelector(".loja-cat-group__label");
        var parentName = normalize(labelEl ? labelEl.textContent : "");
        var showGroup = parentName.indexOf(q) !== -1 || groupsWithMatches[pid];
        setHidden(group, !showGroup);
        if (showGroup) {
          group.open = true;
          if (parentName.indexOf(q) !== -1) visibleChildren += 1;
        }
      });

      if (emptyMsg) {
        emptyMsg.hidden = visibleChildren > 0;
      }
    }

    input.addEventListener("input", filterCategories);
    input.addEventListener("keyup", filterCategories);
    input.addEventListener("search", filterCategories);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLojaCategorySearch);
  } else {
    initLojaCategorySearch();
  }
})();
