"""
Frontend básico (Flask) para testar buscas no índice `documentos` do OpenSearch.

Uso:
    pip install flask
    python web_app.py

Depois acesse http://localhost:5000 no navegador.

Este app roda como um "proxy": o navegador nunca fala direto com o
OpenSearch (evita expor usuário/senha do cluster no front-end).
"""

import os
from pathlib import Path

from flask import Flask, abort, render_template, request, send_file
from werkzeug.utils import secure_filename

import config
from opensearch_client import OpenSearchClient
from utils import extensao, gerar_doc_id, pode_embutir
from docling_service import processar_e_indexar_pdf

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # limite de 50 MB por upload
cliente = OpenSearchClient()


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    resultados = []
    total = 0
    erro = None

    try:
        if q:
            # Busca melhorada com suporte a erros de digitação e flexibilidade
            body = {
                "size": 20,
                "query": {
                    "bool": {
                        "should": [
                            # 1. Busca por termos com tolerância a erros de digitação (fuzziness)
                            {
                                "multi_match": {
                                    "query": q,
                                    "fields": ["conteudo", "nome^3"],
                                    "operator": "and",
                                    "fuzziness": "AUTO",
                                    "prefix_length": 1,
                                }
                            },
                            # 2. Busca por termo/frase parcial (útil para palavras incompletas)
                            {
                                "multi_match": {
                                    "query": q,
                                    "fields": ["conteudo", "nome^3"],
                                    "type": "phrase_prefix",
                                    "boost": 2,
                                }
                            }
                        ],
                        "minimum_should_match": 1
                    }
                },
                "highlight": {
                    "fields": {
                        "conteudo": {
                            "fragment_size": 220,
                            "number_of_fragments": 1,
                        }
                    }
                },
                "_source": ["nome", "caminho", "conteudo"],
            }
        else:
            # Quando 'q' estiver vazio, executa match_all ordenado pelos últimos adicionados
            body = {
                "size": 20,
                "query": {
                    "match_all": {}
                },
                "sort": [
                    {
                        "_doc": "desc"
                    }
                ],
                "_source": ["nome", "caminho", "conteudo"],
            }

        resp = cliente.search(config.INDEX_NAME, body)
        total = resp["hits"]["total"]["value"]

        for hit in resp["hits"]["hits"]:
            fonte = hit["_source"]

            # Se for busca filtrada com highlight, pega o destaque. Senão, corta os 220 primeiros caracteres
            highlight = hit.get("highlight", {}).get("conteudo", [None])[0] if q else None
            conteudo_bruto = fonte.get("conteudo", "")

            trecho = highlight or (conteudo_bruto[:220] + "..." if len(conteudo_bruto) > 220 else conteudo_bruto)

            nome = fonte.get("nome", "(sem nome)")

            resultados.append({
                "id": hit["_id"],
                "nome": nome,
                "titulo": nome,
                "tipo": extensao(nome).upper() or "Documento",
                "trecho": trecho or "(Sem prévia de texto)",
            })

    except Exception as e:
        erro = str(e)

    return render_template("index.html", q=q, resultados=resultados, total=total, erro=erro)


@app.route("/ingest", methods=["GET", "POST"])
def ingest():
    resultados = []

    if request.method == "POST":
        arquivos = request.files.getlist("arquivos")

        for arquivo in arquivos:
            if not arquivo or not arquivo.filename:
                continue

            nome_seguro = secure_filename(arquivo.filename)

            if not nome_seguro.lower().endswith(tuple(config.EXTENSOES_VALIDAS)):
                resultados.append({"nome": arquivo.filename, "ok": False, "mensagem": "Extensão não permitida"})
                continue

            try:
                pasta_destino = Path(config.PASTA_ORIGEM)
                pasta_destino.mkdir(parents=True, exist_ok=True)

                destino = pasta_destino / nome_seguro
                arquivo.save(destino)

                doc_id = gerar_doc_id(str(destino))

                processar_e_indexar_pdf(str(destino), doc_id, config.INDEX_NAME)

                resultados.append({"nome": nome_seguro, "ok": True, "mensagem": ""})

            except Exception as e:
                resultados.append({"nome": arquivo.filename, "ok": False, "mensagem": str(e)})

    return render_template("ingest.html", resultados=resultados)


@app.route("/download/<doc_id>")
def download(doc_id):
    doc = cliente.get(config.INDEX_NAME, doc_id)
    if doc is None:
        abort(404, "Documento não encontrado no índice.")

    caminho = doc["_source"].get("caminho")
    if not caminho or not os.path.isfile(caminho):
        abort(404, "Arquivo não encontrado no caminho de origem.")

    return send_file(caminho, as_attachment=True, download_name=os.path.basename(caminho))


@app.route("/preview/<doc_id>")
def preview(doc_id):
    doc = cliente.get(config.INDEX_NAME, doc_id)
    if doc is None:
        abort(404, "Documento não encontrado no índice.")

    fonte = doc["_source"]
    nome = fonte.get("nome", "(sem nome)")

    return render_template(
        "preview.html",
        doc_id=doc_id,
        nome=nome,
        tipo=extensao(nome).upper() or "Documento",
        conteudo=fonte.get("conteudo", ""),
        pode_embutir=pode_embutir(nome),
    )


@app.route("/arquivo/<doc_id>")
def arquivo(doc_id):
    """Serve o arquivo original inline (sem forçar download), usado pelo
    preview para exibir PDFs/imagens direto no navegador."""
    doc = cliente.get(config.INDEX_NAME, doc_id)
    if doc is None:
        abort(404, "Documento não encontrado no índice.")

    caminho = doc["_source"].get("caminho")
    if not caminho or not os.path.isfile(caminho):
        abort(404, "Arquivo não encontrado no caminho de origem.")

    return send_file(caminho, as_attachment=False)


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5000)
