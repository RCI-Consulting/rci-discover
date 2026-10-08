"""
Frontend (Flask) do RCI Search: busca em todos os índices do OpenSearch,
upload com indexação em segundo plano (fila no MySQL) e gestão de índices.

Uso:
    pip install -r requirements.txt
    python web_app.py

Depois acesse http://localhost:5000 no navegador.

Este app roda como um "proxy": o navegador nunca fala direto com o
OpenSearch (evita expor usuário/senha do cluster no front-end).
"""

import os
from datetime import timedelta

from flask import Flask, abort, redirect, render_template, request, send_file
from opensearchpy.exceptions import RequestError
from werkzeug.utils import secure_filename

import config
import indices
import jobs
import worker
from opensearch_client import OpenSearchClient
from utils import extensao, gerar_doc_id, montar_trecho, pode_embutir, renderizar_markdown

MODO_DEBUG = True

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # limite de 50 MB por envio
cliente = OpenSearchClient()

STATUS_TEXTO = {
    "fila": "Na fila",
    "processando": "Processando",
    "concluido": "Concluído",
    "erro": "Erro",
}


def _listar_indices():
    """Índices gerenciados que existem no OpenSearch (padrão primeiro)."""
    linhas = cliente.listar_indices(indices.padrao_listagem())
    resultado = []
    for linha in linhas:
        nome = linha.get("index", "")
        if not indices.eh_gerenciado(nome):
            continue
        resultado.append({
            "nome": nome,
            "exibicao": indices.nome_exibicao(nome),
            "docs": linha.get("docs.count") or 0,
            "tamanho": linha.get("store.size") or "0b",
            "saude": linha.get("health") or "-",
            "padrao": nome == config.INDEX_NAME,
        })
    resultado.sort(key=lambda i: (not i["padrao"], i["nome"]))
    return resultado


def _documento(indice, doc_id):
    """Busca um documento num índice gerenciado. Retorna (indice, doc) ou
    (None, None). O índice vem da URL, então nunca confiamos nele sem checar."""
    indice = indice or config.INDEX_NAME
    if not indices.eh_gerenciado(indice):
        return None, None
    return indice, cliente.get(indice, doc_id)


@app.errorhandler(404)
def nao_encontrado(e):
    mensagem = e.description or "A página ou o documento solicitado não foi encontrado."
    return render_template("erro.html", titulo="Não encontrado", mensagem=mensagem), 404


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

            resp = cliente.search(indices.padrao_busca(), body, ignore_unavailable=True)
            total = resp["hits"]["total"]["value"]

            for hit in resp["hits"]["hits"]:
                fonte = hit["_source"]

                # Destaque do OpenSearch; sem ele, os primeiros 220 caracteres (já escapado)
                highlight = hit.get("highlight", {}).get("conteudo", [None])[0]
                trecho = montar_trecho(highlight, fonte.get("conteudo", ""))

                nome = fonte.get("nome", "(sem nome)")

                resultados.append({
                    "id": hit["_id"],
                    "indice": hit["_index"],
                    "indice_nome": indices.nome_exibicao(hit["_index"]),
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
    erro = None
    disponiveis = []

    try:
        disponiveis = _listar_indices()
    except Exception as e:
        erro = f"Não consegui listar os índices no OpenSearch: {e}"

    nomes = {i["nome"] for i in disponiveis}
    indice_selecionado = request.form.get("indice") or (
        config.INDEX_NAME if config.INDEX_NAME in nomes else (disponiveis[0]["nome"] if disponiveis else "")
    )

    if request.method == "POST" and not erro:
        if indice_selecionado not in nomes:
            erro = "Escolha um índice válido."
        else:
            for arquivo in request.files.getlist("arquivos"):
                if not arquivo or not arquivo.filename:
                    continue

                nome_seguro = secure_filename(arquivo.filename)

                if not nome_seguro.lower().endswith(tuple(config.EXTENSOES_VALIDAS)):
                    resultados.append({"nome": arquivo.filename, "ok": False, "mensagem": "Extensão não permitida"})
                    continue

                try:
                    pasta_destino = indices.pasta_do_indice(indice_selecionado)
                    destino = pasta_destino / nome_seguro
                    doc_id = gerar_doc_id(str(destino))

                    if jobs.job_ativo(indice_selecionado, doc_id):
                        resultados.append({
                            "nome": nome_seguro, "ok": False,
                            "mensagem": "Já está na fila ou sendo processado",
                        })
                        continue

                    pasta_destino.mkdir(parents=True, exist_ok=True)
                    arquivo.save(destino)
                    jobs.enfileirar(indice_selecionado, nome_seguro, destino, doc_id)

                    resultados.append({"nome": nome_seguro, "ok": True, "mensagem": ""})

                except Exception as e:
                    resultados.append({"nome": arquivo.filename, "ok": False, "mensagem": f"Falha ao enfileirar: {e}"})

    return render_template(
        "ingest.html",
        resultados=resultados,
        erro=erro,
        indices=disponiveis,
        indice_selecionado=indice_selecionado,
    )


@app.route("/fila")
def fila():
    erro = None
    lista = []
    contagem = {}

    try:
        contagem = jobs.contagem_por_status()
        lista = jobs.listar(100)
        for job in lista:
            job["indice_nome"] = indices.nome_exibicao(job["indice"])
            job["status_texto"] = STATUS_TEXTO.get(job["status"], job["status"])
            job["criado_local"] = job["criado_em"] + timedelta(hours=config.FUSO_HORAS)
    except Exception as e:
        erro = f"Banco de dados da fila indisponível: {e}"

    ativos = contagem.get("fila", 0) + contagem.get("processando", 0) > 0
    return render_template("fila.html", jobs=lista, contagem=contagem, ativos=ativos, erro=erro)


@app.route("/fila/<int:job_id>/repetir", methods=["POST"])
def fila_repetir(job_id):
    try:
        jobs.tentar_novamente(job_id)
    except Exception:
        pass
    return redirect("/fila")


@app.route("/fila/limpar", methods=["POST"])
def fila_limpar():
    try:
        jobs.limpar_concluidos()
    except Exception:
        pass
    return redirect("/fila")


@app.route("/indices", methods=["GET", "POST"])
def indices_pagina():
    erro = None
    sucesso = None
    nome_digitado = None

    if request.method == "POST":
        nome_digitado = request.form.get("nome", "")
        try:
            nome_real = indices.validar_nome_novo(nome_digitado)
            cliente.criar_indice(nome_real, indices.MAPEAMENTO)
            return redirect(f"/indices?criado={indices.nome_exibicao(nome_real)}")
        except ValueError as e:
            erro = str(e)
        except RequestError as e:
            if getattr(e, "error", "") == "resource_already_exists_exception":
                erro = "Já existe um índice com esse nome."
            else:
                erro = f"O OpenSearch recusou a criação: {e}"
        except Exception as e:
            erro = f"Erro ao criar o índice: {e}"

    if request.args.get("criado"):
        sucesso = f'Índice "{request.args["criado"]}" criado.'
    if request.args.get("apagado"):
        sucesso = f'Índice "{request.args["apagado"]}" apagado.'

    lista = []
    try:
        lista = _listar_indices()
    except Exception as e:
        erro = erro or f"Não consegui listar os índices no OpenSearch: {e}"

    return render_template(
        "indices.html",
        indices=lista,
        erro=erro,
        sucesso=sucesso,
        nome_digitado=nome_digitado,
        prefixo=config.INDEX_PREFIX,
    )


@app.route("/indices/<nome>/apagar", methods=["GET", "POST"])
def indice_apagar(nome):
    if nome == config.INDEX_NAME or not indices.eh_gerenciado(nome):
        abort(404, "Esse índice não pode ser apagado por aqui.")

    try:
        resumo = cliente.listar_indices(nome)
    except Exception:
        resumo = []
    if not resumo:
        abort(404, "Índice não encontrado.")

    exibicao = indices.nome_exibicao(nome)
    docs = resumo[0].get("docs.count") or 0
    erro = None

    if request.method == "POST":
        if request.form.get("confirmacao", "").strip() != exibicao:
            erro = "O nome digitado não confere. Nada foi apagado."
        else:
            try:
                cliente.apagar_indice(nome)
            except Exception as e:
                erro = f"Erro ao apagar o índice: {e}"
            else:
                try:
                    jobs.apagar_do_indice(nome)
                except Exception:
                    pass
                return redirect(f"/indices?apagado={exibicao}")

    return render_template("indice_apagar.html", exibicao=exibicao, docs=docs, erro=erro)


@app.route("/download/<doc_id>", defaults={"indice": None})
@app.route("/download/<indice>/<doc_id>")
def download(indice, doc_id):
    indice, doc = _documento(indice, doc_id)
    if doc is None:
        abort(404, "Documento não encontrado no índice.")

    caminho = doc["_source"].get("caminho")
    if not caminho or not os.path.isfile(caminho):
        abort(404, "O arquivo original não está mais disponível no servidor.")

    return send_file(caminho, as_attachment=True, download_name=os.path.basename(caminho))


@app.route("/preview/<doc_id>", defaults={"indice": None})
@app.route("/preview/<indice>/<doc_id>")
def preview(indice, doc_id):
    indice, doc = _documento(indice, doc_id)
    if doc is None:
        abort(404, "Documento não encontrado no índice.")

    fonte = doc["_source"]
    nome = fonte.get("nome", "(sem nome)")

    return render_template(
        "preview.html",
        indice=indice,
        indice_nome=indices.nome_exibicao(indice),
        doc_id=doc_id,
        nome=nome,
        tipo=extensao(nome).upper() or "Documento",
        conteudo_html=renderizar_markdown(fonte.get("conteudo", "")),
        pode_embutir=pode_embutir(nome),
    )


@app.route("/arquivo/<doc_id>", defaults={"indice": None})
@app.route("/arquivo/<indice>/<doc_id>")
def arquivo(indice, doc_id):
    """Serve o arquivo original inline (sem forçar download), usado pelo
    preview para exibir PDFs/imagens direto no navegador. Como essa rota
    é carregada dentro de um <iframe>, o erro é uma página mínima (sem o
    topbar do site) em vez da página de erro completa."""
    indice, doc = _documento(indice, doc_id)
    if doc is None:
        return render_template("preview_erro.html", mensagem="Documento não encontrado no índice."), 404

    caminho = doc["_source"].get("caminho")
    if not caminho or not os.path.isfile(caminho):
        return render_template(
            "preview_erro.html",
            mensagem="O arquivo original não está mais disponível no servidor.",
        ), 404

    return send_file(caminho, as_attachment=False)


def _deve_iniciar_workers():
    """Com debug=True o Werkzeug sobe dois processos (um só observa arquivos);
    só o filho que atende requisições deve rodar os workers."""
    if __name__ == "__main__" and MODO_DEBUG and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    return True


if _deve_iniciar_workers():
    worker.iniciar()


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=MODO_DEBUG, port=5000)
