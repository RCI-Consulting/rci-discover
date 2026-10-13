"""Sugestões enquanto o usuário digita (autocomplete) e "buscas populares".

Três fontes, todas opcionais: se uma falhar (MySQL ou OpenSearch fora), as
outras continuam respondendo.
- **dicionário**: termos preferidos cadastrados na administração (dicionario.py);
- **buscas anteriores**: o que outras pessoas já pesquisaram e achou algo
  (auditoria.py; só termos repetidos e sem dados pessoais óbvios);
- **documentos**: nomes de documentos que começam com o que foi digitado.
"""

import auditoria
import config
import dicionario
import indices

MIN_CARACTERES = 2
MAX_PREFIXO = 100
LIMITE_TOTAL = 8


def _nomes_de_documentos(cliente, alvo, prefixo, limite=4):
    """Documentos cujo nome começa com o texto (palavra a palavra)."""
    corpo = {
        "size": limite * 3,
        "_source": ["nome"],
        "query": {"match_phrase_prefix": {"nome": {"query": prefixo, "max_expansions": 30}}},
    }
    resp = cliente.search(alvo, corpo, ignore_unavailable=True)
    itens, vistos = [], set()
    for hit in resp["hits"]["hits"]:
        nome = hit.get("_source", {}).get("nome")
        if not nome or nome.casefold() in vistos:
            continue
        vistos.add(nome.casefold())
        itens.append({
            "tipo": "documento",
            "texto": nome,
            "url": f"/preview/{hit['_index']}/{hit['_id']}",
            "indice": indices.nome_exibicao(hit["_index"]),
        })
        if len(itens) >= limite:
            break
    return itens


def autocompletar(cliente, alvo, prefixo, slug=None):
    """Lista de sugestões (dicts com tipo, texto e, nos documentos, url/indice)."""
    prefixo = (prefixo or "").strip()[:MAX_PREFIXO]
    if len(prefixo) < MIN_CARACTERES:
        return []

    itens = []
    try:
        itens += [{"tipo": "termo", "texto": t} for t in dicionario.sugerir(prefixo, limite=3)]
    except Exception:
        pass
    try:
        itens += [
            {"tipo": "consulta", "texto": t}
            for t in auditoria.termos_populares(prefixo, slug, config.SUGESTOES_MIN_BUSCAS, limite=4)
        ]
    except Exception:
        pass
    if alvo:
        try:
            itens += _nomes_de_documentos(cliente, alvo, prefixo)
        except Exception:
            pass

    # sem repetir o mesmo texto (um termo do dicionário que também foi muito buscado, por exemplo)
    vistos, finais = set(), []
    for item in itens:
        chave = (item["tipo"] == "documento", item["texto"].casefold())
        if chave in vistos:
            continue
        vistos.add(chave)
        finais.append(item)
    return finais[:LIMITE_TOTAL]


def populares(slug=None, limite=5):
    """Buscas mais repetidas (que acharam algo), para sugerir quando uma busca não acha nada."""
    try:
        return auditoria.termos_populares(None, slug, config.SUGESTOES_MIN_BUSCAS, limite)
    except Exception:
        return []


def correcao(cliente, alvo, consulta):
    """'Você quis dizer': a correção que o OpenSearch propõe para a consulta,
    ou None. É um pedido separado e opcional: se falhar, a busca não é afetada."""
    corpo = {
        "size": 0,
        "suggest": {
            "correcao": {
                "text": consulta,
                "phrase": {
                    "field": "conteudo",
                    "size": 1,
                    "max_errors": 2,
                    "direct_generator": [{"field": "conteudo", "suggest_mode": "missing", "min_word_length": 3}],
                    "highlight": {"pre_tag": "<em>", "post_tag": "</em>"},
                },
            }
        },
    }
    try:
        resp = cliente.search(alvo, corpo, ignore_unavailable=True)
        opcoes = resp.get("suggest", {}).get("correcao", [{}])[0].get("options", [])
    except Exception:
        return None
    if not opcoes:
        return None
    melhor = opcoes[0]
    texto = (melhor.get("text") or "").strip()
    if not texto or texto.casefold() == consulta.casefold():
        return None
    return {"texto": texto, "destaque": melhor.get("highlighted") or texto}
