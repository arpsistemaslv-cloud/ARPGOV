/**
 * Valores em reais por extenso e inteiros formatados (padrão brasileiro).
 * Alinhado a br_extenso.py (mesma regra de milhões/de reais).
 */
(function (global) {
  'use strict';

  var UNIDADES = ['zero','um','dois','três','quatro','cinco','seis','sete','oito','nove','dez','onze','doze','treze','quatorze','quinze','dezesseis','dezessete','dezoito','dezenove'];
  var DEZENAS = ['','','vinte','trinta','quarenta','cinquenta','sessenta','setenta','oitenta','noventa'];
  var CENTENAS = ['','cento','duzentos','trezentos','quatrocentos','quinhentos','seiscentos','setecentos','oitocentos','novecentos'];

  function extenso0a999(n) {
    if (n < 0 || n > 999) throw new Error(n);
    if (n === 0) return '';
    if (n < 20) return UNIDADES[n];
    if (n === 100) return 'cem';
    var c = Math.floor(n / 100);
    var r = n % 100;
    var parts = [];
    if (c) parts.push(CENTENAS[c]);
    if (r < 20) parts.push(UNIDADES[r]);
    else {
      var d = Math.floor(r / 10);
      var u = r % 10;
      if (u === 0) parts.push(DEZENAS[d]);
      else parts.push(DEZENAS[d] + ' e ' + UNIDADES[u]);
    }
    return parts.join(' e ');
  }

  function concatGrupos(partes) {
    if (!partes.length) return '';
    if (partes.length === 1) return partes[0];
    if (partes.length === 2) return partes[0] + ' e ' + partes[1];
    return partes.slice(0, -1).join(', ') + ' e ' + partes[partes.length - 1];
  }

  function extensoInteiroPtBr(n) {
    if (n < 0) return 'menos ' + extensoInteiroPtBr(-n);
    if (n === 0) return 'zero';
    var grupos = [];
    var x = n;
    while (x > 0) {
      grupos.push(x % 1000);
      x = Math.floor(x / 1000);
    }
    var sufixos = [
      ['',''], ['mil','mil'], ['milhão','milhões'], ['bilhão','bilhões'], ['trilhão','trilhões']
    ];
    var partes = [];
    for (var i = grupos.length - 1; i >= 0; i--) {
      var g = grupos[i];
      if (g === 0) continue;
      var nivel = Math.min(i, sufixos.length - 1);
      var sing = sufixos[nivel][0];
      var plur = sufixos[nivel][1];
      if (i === 0) partes.push(extenso0a999(g));
      else if (i === 1) {
        if (g === 1) partes.push('mil');
        else partes.push(extenso0a999(g) + ' mil');
      } else {
        if (g === 1) partes.push('um ' + sing);
        else partes.push(extenso0a999(g) + ' ' + plur);
      }
    }
    return concatGrupos(partes);
  }

  function moedaExtensoBrl(valor) {
    var d = Math.round(Number(valor) * 100) / 100;
    if (!isFinite(d)) return '';
    var neg = d < 0;
    d = Math.abs(d);
    var centavosTotais = Math.round(d * 100);
    var inteiro = Math.floor(centavosTotais / 100);
    var centavos = centavosTotais % 100;
    var frases = [];
    if (inteiro > 0) {
      var ext = extensoInteiroPtBr(inteiro);
      if (/milhão|milhões|bilhão|bilhões|trilhão|trilhões/.test(ext)) frases.push(ext + ' de reais');
      else if (inteiro === 1) frases.push(ext + ' real');
      else frases.push(ext + ' reais');
    }
    if (centavos > 0) {
      var cext = extensoInteiroPtBr(centavos);
      frases.push(cext + (centavos === 1 ? ' centavo' : ' centavos'));
    }
    var s;
    if (!frases.length) s = 'zero real';
    else if (frases.length === 1) s = frases[0];
    else s = frases[0] + ' e ' + frases[1];
    if (neg) s = 'menos ' + s;
    else s = s.charAt(0).toUpperCase() + s.slice(1);
    return s;
  }

  function formatInteiroPtBr(n) {
    if (n == null || n === '') return '—';
    var x = parseInt(String(n), 10);
    if (isNaN(x)) return '—';
    var neg = x < 0;
    x = Math.abs(x);
    var s = String(x).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return (neg ? '-' : '') + s;
  }

  global.moedaExtensoBrl = moedaExtensoBrl;
  global.formatInteiroPtBr = formatInteiroPtBr;
  global.extensoInteiroPtBr = extensoInteiroPtBr;
})(typeof window !== 'undefined' ? window : this);
