"""Temas dos resultados: os assuntos que se destacam nos documentos achados,
como o agrupamento por assunto (clustering) do Watson Explorer.

Usa a agregação `significant_text` do OpenSearch: compara as palavras dos
primeiros documentos da busca (uma amostra, para ficar leve) com as do
índice inteiro e devolve as que aparecem bem mais ali do que no geral. Em
"contrato", por exemplo, podem surgir "aditivo", "rescisão", "fornecedor".

Clicar num tema refina a busca: só ficam os documentos que contêm a palavra
(`tema=` na URL, até MAX_ATIVOS ao mesmo tempo, todos obrigatórios), e os
temas são recalculados dentro desse recorte.

É um pedido separado da busca: se falhar ou demorar, os resultados não são
afetados — o painel só não aparece. São palavras soltas; expressões como
"plano de saúde" precisariam de um campo novo no índice (e reindexar).
"""

import re

from dicionario import normalizar

AMOSTRA = 100     # documentos mais relevantes analisados (por shard; os índices têm 1)
MAX_TEMAS = 10
MAX_ATIVOS = 3
MIN_RESULTADOS = 3  # com menos que isso, não há o que agrupar

# Termos que nunca viram tema (regex do Lucene, casa o termo inteiro):
# começa com número, até 3 letras, marcador de imagem do Docling, pedaços de URL.
_EXCLUIR = "[0-9].*|.{1,3}|imagem|https?|www|html?|pdf"

_TEMA_VALIDO = re.compile(r"^[^\W\d_][\w-]{1,49}$")


def ler_temas(args):
    """Temas escolhidos na URL, validados (uma palavra cada, sem repetir)."""
    escolhidos = []
    for tema in args.getlist("tema"):
        tema = tema.strip().lower()
        if _TEMA_VALIDO.match(tema) and tema not in escolhidos:
            escolhidos.append(tema)
    return escolhidos[:MAX_ATIVOS]


def restringir(consulta, temas):
    """A consulta da busca, exigindo também cada tema escolhido."""
    if not temas:
        return consulta
    return {"bool": {"must": [consulta], "filter": [{"match": {"conteudo": t}} for t in temas]}}


def resumo_texto(temas):
    return ("tema=" + ",".join(temas)) if temas else None


def consultar(cliente, alvo, consulta, filtro, q, ativos, total):
    """Temas para o painel: [{"termo", "contagem"}]. `consulta` já vem com os
    temas escolhidos; `filtro` é o das facetas (post_filter da busca), que
    aqui entra na consulta para os temas valerem para o que está na tela.
    A contagem só é mostrada quando a amostra cobre todos os resultados."""
    if total < MIN_RESULTADOS or len(ativos) >= MAX_ATIVOS:
        return []
    body = {
        "size": 0,
        "query": {"bool": {"must": [consulta], "filter": [filtro]}} if filtro else consulta,
        "aggs": {
            "amostra": {
                "sampler": {"shard_size": AMOSTRA},
                "aggs": {
                    "temas": {
                        "significant_text": {
                            "field": "conteudo",
                            "size": MAX_TEMAS + 5,
                            "min_doc_count": 2,
                            "filter_duplicate_text": True,
                            "exclude": _EXCLUIR,
                        }
                    }
                },
            }
        },
    }
    try:
        resp = cliente.search(alvo, body, ignore_unavailable=True, request_timeout=5)
        baldes = resp["aggregations"]["amostra"]["temas"]["buckets"]
    except Exception as e:
        print(f"[Temas] Não consegui calcular os temas: {e}")
        return []

    # as próprias palavras da busca (e os temas já escolhidos) não são novidade
    ja_usadas = set(normalizar(q).split()) | {normalizar(t) for t in ativos}
    mostrar_contagem = total <= AMOSTRA
    temas = []
    for b in baldes:
        termo = str(b.get("key", ""))
        if not _TEMA_VALIDO.match(termo) or normalizar(termo) in ja_usadas:
            continue
        temas.append({"termo": termo, "contagem": b.get("doc_count") if mostrar_contagem else None})
        if len(temas) == MAX_TEMAS:
            break
    return temas
