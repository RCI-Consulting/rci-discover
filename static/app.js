// Comportamentos comuns a todas as páginas. Ficam aqui (e não em onclick=/onchange=
// no HTML) porque a Content-Security-Policy do app bloqueia JavaScript inline sem nonce.
//
//   data-confirmar="Texto?"  botão/link: pede confirmação antes de seguir
//   data-autoenviar          campo: envia o formulário ao mudar de valor
(function () {
  document.addEventListener('click', function (ev) {
    var alvo = ev.target.closest ? ev.target.closest('[data-confirmar]') : null;
    if (alvo && !window.confirm(alvo.getAttribute('data-confirmar'))) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, true);

  document.addEventListener('change', function (ev) {
    var alvo = ev.target;
    if (alvo && alvo.hasAttribute && alvo.hasAttribute('data-autoenviar') && alvo.form) {
      alvo.form.submit();
    }
  });
})();
