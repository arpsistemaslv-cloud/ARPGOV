(function () {
  var quotes = [
    "O setor público compra com planejamento. Quem se antecipa, fecha adesões.",
    "Representação comercial exige método: catálogo claro, follow-up e conformidade.",
    "Cada ata vigente é uma oportunidade — organize, divulgue e converta com disciplina.",
    "Empresários que dominam o B2G crescem com previsibilidade, não com sorte.",
  ];
  var el = document.getElementById("staff-login-quote");
  if (!el || quotes.length < 2) return;
  var idx = 0;
  window.setInterval(function () {
    idx = (idx + 1) % quotes.length;
    el.style.opacity = "0";
    window.setTimeout(function () {
      el.textContent = quotes[idx];
      el.style.opacity = "1";
    }, 220);
  }, 9000);
})();
