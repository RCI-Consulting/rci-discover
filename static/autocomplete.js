/* Autocomplete da caixa de busca: sugestões enquanto o usuário digita.
   Teclado: ↑ ↓ navegam, Enter escolhe, Esc fecha. Sem sugestão marcada, o
   Enter faz a busca normal. Só mostra o que o servidor devolver em /sugestoes. */
(function () {
  var campo = document.querySelector('.search-form input[name="q"]');
  if (!campo) return;

  var caixa = campo.closest('.search-box');
  var formulario = campo.form;
  var lista = document.createElement('ul');
  lista.id = 'lista-sugestoes';
  lista.className = 'sugestoes';
  lista.setAttribute('role', 'listbox');
  lista.hidden = true;
  caixa.appendChild(lista);

  var ROTULO = { termo: 'dicionário', consulta: 'busca popular', documento: 'documento' };
  var itens = [];
  var ativo = -1;
  var temporizador = null;
  var pedido = null;
  var ultimoTexto = '';

  function fechar() {
    lista.hidden = true;
    campo.setAttribute('aria-expanded', 'false');
    campo.removeAttribute('aria-activedescendant');
    ativo = -1;
  }

  function marcar(indice) {
    var lis = lista.children;
    for (var i = 0; i < lis.length; i++) {
      var selecionado = i === indice;
      lis[i].classList.toggle('ativo', selecionado);
      lis[i].setAttribute('aria-selected', selecionado ? 'true' : 'false');
    }
    ativo = indice;
    if (indice >= 0) campo.setAttribute('aria-activedescendant', lis[indice].id);
    else campo.removeAttribute('aria-activedescendant');
  }

  function escolher(item) {
    fechar();
    if (item.tipo === 'documento' && item.url) {
      window.location.href = item.url;
      return;
    }
    campo.value = item.texto;
    formulario.submit();
  }

  function texto(item, prefixo) {
    // o começo igual ao que foi digitado em negrito; tudo montado com nós DOM e textContent
    var span = document.createElement('span');
    span.className = 'sugestao-texto';
    var inicio = item.texto.toLowerCase().indexOf(prefixo.toLowerCase());
    if (prefixo && inicio === 0) {
      var forte = document.createElement('strong');
      forte.textContent = item.texto.slice(0, prefixo.length);
      span.appendChild(forte);
      span.appendChild(document.createTextNode(item.texto.slice(prefixo.length)));
    } else {
      span.textContent = item.texto;
    }
    return span;
  }

  function desenhar(prefixo) {
    lista.textContent = '';
    itens.forEach(function (item, i) {
      var li = document.createElement('li');
      li.id = 'sugestao-' + i;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', 'false');
      li.appendChild(texto(item, prefixo));
      var rotulo = document.createElement('span');
      rotulo.className = 'sugestao-tipo sugestao-tipo-' + item.tipo;
      rotulo.textContent = item.tipo === 'documento' && item.indice ? item.indice : ROTULO[item.tipo] || '';
      li.appendChild(rotulo);
      // mousedown (e não click) para agir antes do blur do campo fechar a lista
      li.addEventListener('mousedown', function (e) {
        e.preventDefault();
        escolher(item);
      });
      li.addEventListener('mousemove', function () { if (ativo !== i) marcar(i); });
      lista.appendChild(li);
    });
    lista.hidden = itens.length === 0;
    campo.setAttribute('aria-expanded', itens.length ? 'true' : 'false');
    ativo = -1;
  }

  function buscar() {
    var prefixo = campo.value.trim();
    if (prefixo.length < 2) {
      fechar();
      return;
    }
    if (prefixo === ultimoTexto && !lista.hidden) return;
    ultimoTexto = prefixo;
    if (pedido) pedido.abort();
    pedido = new AbortController();
    var url = '/sugestoes?q=' + encodeURIComponent(prefixo);
    if (campo.dataset.projeto) url += '&p=' + encodeURIComponent(campo.dataset.projeto);
    fetch(url, { signal: pedido.signal, headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : { itens: [] }; })
      .then(function (dados) {
        if (campo.value.trim() !== prefixo) return;  // o usuário já digitou outra coisa
        itens = dados.itens || [];
        desenhar(prefixo);
      })
      .catch(function () { /* aborto ou rede: sem sugestões, a busca normal continua */ });
  }

  campo.addEventListener('input', function () {
    clearTimeout(temporizador);
    temporizador = setTimeout(buscar, 150);
  });

  campo.addEventListener('keydown', function (e) {
    if (lista.hidden || !itens.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      marcar(ativo + 1 >= itens.length ? 0 : ativo + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      marcar(ativo <= 0 ? itens.length - 1 : ativo - 1);
    } else if (e.key === 'Enter' && ativo >= 0) {
      e.preventDefault();
      escolher(itens[ativo]);
    } else if (e.key === 'Escape') {
      fechar();
    }
  });

  campo.addEventListener('blur', function () { setTimeout(fechar, 100); });
  campo.addEventListener('focus', function () { if (itens.length && campo.value.trim() === ultimoTexto) { lista.hidden = false; campo.setAttribute('aria-expanded', 'true'); } });
  formulario.addEventListener('submit', function () { clearTimeout(temporizador); if (pedido) pedido.abort(); fechar(); });
})();
