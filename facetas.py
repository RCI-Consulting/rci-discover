"""Facetas da busca (filtros laterais com contagem): índice, tipo de arquivo
e data de indexação.

Cada faceta mostra as contagens considerando os filtros das OUTRAS facetas,
mas não o dela própria — assim, escolher "PDF" não faz "DOCX" sumir da lista:
o usuário vê quanto daria ao trocar ou somar. Isso é feito com `post_filter`
(filtra só os resultados) mais uma agregação `filter` por faceta.
"""

import re

import indices

_TIPO_VALIDO = re.compile(r"^[a-z0-9]{1,10}$")

# chave na URL, rótulo, expressão de data do OpenSearch
DATAS = [
    ("24h", "Últimas 24 horas", "now-1d"),
    ("7d", "Últimos 7 dias", "now-7d"),
    ("30d", "Últimos 30 dias", "now-30d"),
    ("1a", "Último ano", "now-365d"),
]
_DATA_EXPRESSAO = {chave: expr for chave, _, expr in DATAS}

MAX_VALORES = 15  # itens por faceta; o resto aparece só quando selecionado


def ler_filtros(args):
    """Filtros vindos da URL, já validados (valor desconhecido é ignorado)."""
    escolhidos_indices = []
    for nome in args.getlist("indice"):
        if indices.eh_gerenciado(nome) and nome not in escolhidos_indices:
            escolhidos_indices.append(nome)

    tipos = []
    for tipo in args.getlist("tipo"):
        tipo = tipo.strip().lower()
        if _TIPO_VALIDO.match(tipo) and tipo not in tipos:
            tipos.append(tipo)

    data = args.get("data", "")
    return {"indice": escolhidos_indices, "tipo": tipos, "data": data if data in _DATA_EXPRESSAO else ""}


def ha_filtros(filtros):
    return bool(filtros["indice"] or filtros["tipo"] or filtros["data"])


def _clausula(campo, filtros):
    if campo == "indice" and filtros["indice"]:
        return {"terms": {"_index": filtros["indice"]}}
    if campo == "tipo" and filtros["tipo"]:
        return {"terms": {"tipo": filtros["tipo"]}}
    if campo == "data" and filtros["data"]:
        return {"range": {"indexado_em": {"gte": _DATA_EXPRESSAO[filtros["data"]]}}}
    return None


def _clausulas(filtros, excluir=None):
    return [c for c in (_clausula(campo, filtros) for campo in ("indice", "tipo", "data") if campo != excluir) if c]


def _bool(clausulas):
    return {"bool": {"filter": clausulas}} if clausulas else {"match_all": {}}


def montar_consulta(filtros):
    """(post_filter, aggs) para somar ao corpo da busca. post_filter é None
    quando não há filtro ativo."""
    todas = _clausulas(filtros)
    aggs = {
        "f_indice": {
            "filter": _bool(_clausulas(filtros, "indice")),
            "aggs": {"v": {"terms": {"field": "_index", "size": 50}}},
        },
        "f_tipo": {
            "filter": _bool(_clausulas(filtros, "tipo")),
            "aggs": {"v": {"terms": {"field": "tipo", "size": 50}}},
        },
        "f_data": {
            "filter": _bool(_clausulas(filtros, "data")),
            "aggs": {"v": {"filters": {"filters": {
                chave: {"range": {"indexado_em": {"gte": expr}}} for chave, _, expr in DATAS
            }}}},
        },
    }
    return (_bool(todas) if todas else None), aggs


def _item(valor, rotulo, contagem, marcado):
    return {"valor": valor, "rotulo": rotulo, "contagem": contagem, "marcado": marcado}


def _cortar(itens):
    """Mantém os MAX_VALORES maiores e, além deles, qualquer item selecionado."""
    return [i for n, i in enumerate(itens) if n < MAX_VALORES or i["marcado"]]


def interpretar(aggs, filtros):
    """Transforma a resposta das agregações nas facetas para a tela. Facetas
    sem nenhum item (e sem seleção) são omitidas."""
    aggs = aggs or {}
    facetas = []

    por_indice = {b["key"]: b["doc_count"] for b in aggs.get("f_indice", {}).get("v", {}).get("buckets", [])}
    for nome in filtros["indice"]:
        por_indice.setdefault(nome, 0)
    itens = [
        _item(nome, indices.nome_exibicao(nome), n, nome in filtros["indice"])
        for nome, n in sorted(por_indice.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    if itens:
        facetas.append({"campo": "indice", "titulo": "Índice", "unico": False, "itens": _cortar(itens)})

    por_tipo = {b["key"]: b["doc_count"] for b in aggs.get("f_tipo", {}).get("v", {}).get("buckets", [])}
    for tipo in filtros["tipo"]:
        por_tipo.setdefault(tipo, 0)
    itens = [
        _item(tipo, tipo.upper(), n, tipo in filtros["tipo"])
        for tipo, n in sorted(por_tipo.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    if itens:
        facetas.append({"campo": "tipo", "titulo": "Tipo de arquivo", "unico": False, "itens": _cortar(itens)})

    por_data = aggs.get("f_data", {}).get("v", {}).get("buckets", {})
    if any(por_data.get(chave, {}).get("doc_count") for chave, _, _ in DATAS) or filtros["data"]:
        itens = [_item("", "Qualquer data", None, not filtros["data"])]
        itens += [
            _item(chave, rotulo, por_data.get(chave, {}).get("doc_count", 0), filtros["data"] == chave)
            for chave, rotulo, _ in DATAS
        ]
        facetas.append({"campo": "data", "titulo": "Indexado em", "unico": True, "itens": itens})

    return facetas


def resumo_texto(filtros):
    """Texto curto dos filtros ativos, para a auditoria (None se não há)."""
    partes = []
    if filtros["indice"]:
        partes.append("indice=" + ",".join(indices.nome_exibicao(i) for i in filtros["indice"]))
    if filtros["tipo"]:
        partes.append("tipo=" + ",".join(filtros["tipo"]))
    if filtros["data"]:
        partes.append("data=" + filtros["data"])
    return "; ".join(partes)[:500] or None
