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

# Campos usados pelas facetas da busca. Índices criados antes deles recebem
# esses campos via put_mapping (OpenSearchClient.garantir_mapeamento).
CAMPOS_FACETAS = {
    "tipo": {"type": "keyword"},
    "indexado_em": {"type": "date"},
}

# Título do documento (primeiro título do Markdown ou o nome do arquivo
# limpo), com peso maior na busca. Também entra via put_mapping nos antigos.
CAMPOS_RELEVANCIA = {
    "titulo": {"type": "text"},
}

# Campos acrescentados depois da criação dos primeiros índices.
CAMPOS_NOVOS = {**CAMPOS_FACETAS, **CAMPOS_RELEVANCIA}

# Português: o subcampo `.pt` de nome, título e conteúdo guarda as palavras
# sem acento e reduzidas ao radical ("contratos", "contratação" e
# "contrato" viram a mesma coisa; "acao" acha "ação"), como o stemming por
# idioma do Watson Explorer. O campo original continua igual (palavra exata),
# então a busca usa os dois: a palavra exata pesa mais que a variação.
ANALISADOR_PT = "rci_pt"
ANALISE_PT = {
    "analysis": {
        "filter": {
            "rci_pt_stop": {"type": "stop", "stopwords": "_portuguese_"},
            "rci_pt_radical": {"type": "stemmer", "language": "light_portuguese"},
        },
        "analyzer": {
            ANALISADOR_PT: {
                "type": "custom",
                "tokenizer": "standard",
                # stopwords antes de tirar os acentos (a lista tem "é", "à", "não"...)
                "filter": ["lowercase", "rci_pt_stop", "asciifolding", "rci_pt_radical"],
            }
        },
    }
}
SUBCAMPO_PT = {"type": "text", "analyzer": ANALISADOR_PT}
CAMPOS_COM_PT = ("nome", "titulo", "conteudo")

MAPEAMENTO = {
    "settings": {"index": {"number_of_shards": 1, **ANALISE_PT}},
    "mappings": {
        "properties": {
            **CAMPOS_NOVOS,
            "nome": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}, "pt": SUBCAMPO_PT}},
            "caminho": {"type": "keyword"},
            "conteudo": {"type": "text", "fields": {"pt": SUBCAMPO_PT}},
            "titulo": {"type": "text", "fields": {"pt": SUBCAMPO_PT}},
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
