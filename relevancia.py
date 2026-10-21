"""Relevância, ordenação e paginação da busca.

- **Pesos por campo:** o `titulo` (primeiro título do documento, ou o nome do
  arquivo limpo) vale mais que o `nome` do arquivo, que vale mais que o texto.
  A frase exata também ganha pontos, além de bater todas as palavras.
- **Recência:** documentos indexados há pouco ganham um bônus de até
  `PESO_RECENCIA` (0,5 = até 50% a mais), que cai pela metade em um ano.
  É um bônus, não um filtro: um documento antigo muito relevante continua
  na frente. Documentos sem data (indexados antes das facetas) ficam neutros.
- **Ordenação:** relevância (padrão), mais recentes ou nome (A–Z).
- **Paginação:** `POR_PAGINA` resultados por página, até o limite de 10.000
  resultados que o OpenSearch pagina por padrão.
"""

import config

CAMPOS = ["titulo^4", "nome^3", "conteudo"]

POR_PAGINA = 10
LIMITE_JANELA = 10000  # index.max_result_window padrão do OpenSearch

# chave na URL, rótulo, critério de ordenação do OpenSearch
ORDENACOES = [
    ("relevancia", "Relevância", None),
    ("recentes", "Mais recentes", [
        {"indexado_em": {"order": "desc", "missing": "_last", "unmapped_type": "date"}}, "_score",
    ]),
    ("nome", "Nome (A–Z)", [
        {"nome.keyword": {"order": "asc", "missing": "_last", "unmapped_type": "keyword"}}, "_score",
    ]),
]
_CRITERIO = {chave: criterio for chave, _, criterio in ORDENACOES}


def ler_ordem(args):
    ordem = args.get("ordem", "")
    return ordem if ordem in _CRITERIO else "relevancia"


def ler_pagina(args):
    try:
        pagina = int(args.get("pagina", 1))
    except ValueError:
        return 1
    return min(max(1, pagina), ultima_pagina_possivel())


def ultima_pagina_possivel():
    return LIMITE_JANELA // POR_PAGINA


def total_paginas(total):
    return max(1, min(-(-total // POR_PAGINA), ultima_pagina_possivel()))


def paginas_visiveis(pagina, paginas, vizinhas=2):
    """Números para a barra de páginas, com None onde entra "…".
    Ex.: página 7 de 20 -> [1, None, 5, 6, 7, 8, 9, None, 20]."""
    numeros = sorted({1, paginas, *range(max(1, pagina - vizinhas), min(paginas, pagina + vizinhas) + 1)})
    saida = []
    for n in numeros:
        if saida and n - saida[-1] > 1:
            saida.append(None)
        saida.append(n)
    return saida


def montar_consulta(q, expansoes=()):
    """Consulta com pesos por campo e bônus de recência. `expansoes` são as
    consultas alternativas do dicionário (sinônimos/siglas)."""
    should = [
        # todas as palavras, com tolerância a erros de digitação
        {"multi_match": {"query": q, "fields": CAMPOS, "operator": "and", "fuzziness": "AUTO", "prefix_length": 1}},
        # as palavras na ordem digitada (frase exata) valem mais
        {"multi_match": {"query": q, "fields": CAMPOS, "type": "phrase", "boost": 3}},
        # palavra incompleta no fim ("contrat" acha "contrato")
        {"multi_match": {"query": q, "fields": CAMPOS, "type": "phrase_prefix", "boost": 2}},
    ]
    for alt in expansoes:
        should.append({"multi_match": {"query": alt["consulta"], "fields": CAMPOS, "operator": "and"}})

    texto = {"bool": {"should": should, "minimum_should_match": 1}}
    if config.PESO_RECENCIA <= 0:
        return texto
    return {
        "function_score": {
            "query": texto,
            "functions": [
                {"weight": 1},  # base: sem isso, documento sem data teria fator 0
                {
                    "filter": {"exists": {"field": "indexado_em"}},
                    "gauss": {"indexado_em": {"origin": "now", "offset": "30d", "scale": "365d", "decay": 0.5}},
                    "weight": config.PESO_RECENCIA,
                },
            ],
            "score_mode": "sum",      # fator = 1 + bônus de recência
            "boost_mode": "multiply",
        }
    }


def aplicar(body, ordem, pagina):
    """Soma ordenação e paginação ao corpo da busca."""
    body["from"] = (pagina - 1) * POR_PAGINA
    body["size"] = POR_PAGINA
    criterio = _CRITERIO.get(ordem)
    if criterio:
        body["sort"] = criterio
    return body
