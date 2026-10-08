"""Regras sobre quais índices do OpenSearch a aplicação gerencia.

Só enxergamos/alteramos o índice padrão (legado, `config.INDEX_NAME`) e os
índices com o prefixo `config.INDEX_PREFIX`. Qualquer nome vindo da URL ou de
formulário passa por aqui antes de tocar no OpenSearch — sem isso, uma rota
como /preview/<indice>/<id> permitiria ler índices internos (ex: segurança).
"""

import re
from pathlib import Path

import config

# Nome informado pelo usuário (sem o prefixo)
_NOME_VALIDO = re.compile(r"^[a-z0-9][a-z0-9_-]{1,49}$")

MAPEAMENTO = {
    "settings": {"index": {"number_of_shards": 1}},
    "mappings": {
        "properties": {
            "nome": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
            "caminho": {"type": "keyword"},
            "conteudo": {"type": "text"},
        }
    },
}


def padrao_busca() -> str:
    """Expressão de índices para o OpenSearch: o padrão + todos com prefixo."""
    return f"{config.INDEX_NAME},{config.INDEX_PREFIX}*"


def padrao_listagem() -> str:
    """Como o `padrao_busca`, mas só com curingas: a API _cat do OpenSearch
    trava (timeout) quando recebe um nome explícito que não existe."""
    return f"{config.INDEX_NAME}*,{config.INDEX_PREFIX}*"


def eh_gerenciado(nome: str) -> bool:
    if not nome:
        return False
    if nome == config.INDEX_NAME:
        return True
    resto = nome[len(config.INDEX_PREFIX):]
    return nome.startswith(config.INDEX_PREFIX) and bool(_NOME_VALIDO.match(resto))


def nome_exibicao(nome: str) -> str:
    """Nome amigável: tira o prefixo (o índice padrão fica como está)."""
    if nome != config.INDEX_NAME and nome.startswith(config.INDEX_PREFIX):
        return nome[len(config.INDEX_PREFIX):]
    return nome


def validar_nome_novo(nome: str) -> str:
    """Valida o nome digitado e devolve o nome real (com prefixo).
    Levanta ValueError com uma mensagem pronta para mostrar na tela."""
    nome = (nome or "").strip().lower()
    if not _NOME_VALIDO.match(nome):
        raise ValueError(
            "Nome inválido: use de 2 a 50 caracteres entre letras minúsculas, "
            "números, hífen e underline (começando por letra ou número)."
        )
    return f"{config.INDEX_PREFIX}{nome}"


def pasta_do_indice(nome: str) -> Path:
    """Onde ficam os arquivos originais de cada índice. O índice padrão mantém
    a pasta raiz (os arquivos antigos já estão lá e o `doc_id` é um hash do
    caminho — mudar o caminho duplicaria os documentos)."""
    base = Path(config.PASTA_ORIGEM)
    if nome == config.INDEX_NAME:
        return base
    return base / nome_exibicao(nome)
