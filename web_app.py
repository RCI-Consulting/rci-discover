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
from urllib.parse import quote

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory
from opensearchpy.exceptions import RequestError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import acesso
import auditoria
import config
import dicionario
import facetas
import indices
import jobs
import projetos
import sugestoes
import worker
from opensearch_client import OpenSearchClient
from utils import escapar_destaque, extensao, gerar_doc_id, montar_trecho, pode_embutir, renderizar_markdown

MODO_DEBUG = True

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # limite de 50 MB por envio
if config.PROXIES_CONFIAVEIS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.PROXIES_CONFIAVEIS)
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


@app.context_processor
def contexto_comum():
    return {"eh_admin": acesso.eh_admin()}


@app.before_request
def proteger_admin():
    if request.path.startswith("/admin") and not acesso.eh_admin():
        abort(403)


@app.route("/favicon.ico")
def favicon():
    """Os navegadores pedem /favicon.ico na raiz, mesmo sem <link> na página."""
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon", max_age=86400)


@app.errorhandler(403)
def sem_permissao(e):
    return render_template(
        "erro.html", titulo="Acesso restrito",
        mensagem="Você não tem permissão para acessar a área de administração.",
    ), 403


@app.errorhandler(404)
def nao_encontrado(e):
    mensagem = e.description or "A página ou o documento solicitado não foi encontrado."
    return render_template("erro.html", titulo="Não encontrado", mensagem=mensagem), 404


def _pesquisar(q, alvo, projeto=None):
    """Roda a busca em `alvo` (expressão de índices) e prepara o que a tela
    mostra. O `projeto`, quando existe, entra no registro da auditoria e nas
    sugestões (que ficam restritas às buscas daquele projeto)."""
    resultados = []
    total = 0
    erro = None
    filtros = facetas.ler_filtros(request.args)
    lista_facetas = []
    expansoes = []
    correcao = None
    populares = []
    slug = projeto["slug"] if projeto else None

    try:
        if q and alvo:
            try:
                expansoes = dicionario.expandir(q)
            except Exception:
                expansoes = []  # o dicionário é um extra: sem ele a busca segue normal

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

            # sinônimos/siglas do dicionário: cada consulta alternativa também pontua
            for alt in expansoes:
                body["query"]["bool"]["should"].append({
                    "multi_match": {"query": alt["consulta"], "fields": ["conteudo", "nome^3"], "operator": "and"}
                })

            post_filter, aggs = facetas.montar_consulta(filtros)
            body["aggs"] = aggs
            if post_filter:
                body["post_filter"] = post_filter

            resp = cliente.search(alvo, body, ignore_unavailable=True)
            total = resp["hits"]["total"]["value"]
            lista_facetas = facetas.interpretar(resp.get("aggregations"), filtros)
            if total <= 3:
                correcao = sugestoes.correcao(cliente, alvo, q)
            if total == 0:
                populares = sugestoes.populares(slug)
            resumo = "; ".join(
                parte for parte in (f"projeto={projeto['slug']}" if projeto else None, facetas.resumo_texto(filtros)) if parte
            )
            auditoria.registrar_busca(q, total, *auditoria.contexto_requisicao(), filtros=resumo[:500] or None)

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

    if correcao:
        correcao["destaque"] = escapar_destaque(correcao["destaque"])
    return {
        "resultados": resultados, "total": total, "erro": erro,
        "facetas": lista_facetas, "filtrando": facetas.ha_filtros(filtros),
        "expansoes": expansoes, "correcao": correcao, "populares": populares,
    }


def _tela_busca(q, alvo, projeto=None):
    return render_template(
        "index.html", q=q, projeto=projeto, acao=f"/p/{projeto['slug']}" if projeto else "/busca",
        **_pesquisar(q, alvo, projeto),
    )


@app.route("/sugestoes")
def sugestoes_autocomplete():
    """Sugestões para a caixa de busca enquanto o usuário digita (JSON)."""
    prefixo = request.args.get("q", "")
    slug = request.args.get("p", "").strip()
    alvo = indices.padrao_busca()
    projeto_slug = None
    if slug:
        try:
            projeto = projetos.obter_por_slug(slug)
        except Exception:
            projeto = None
        if projeto:
            alvo = ",".join(projeto["indices"])
            projeto_slug = projeto["slug"]
    resposta = jsonify({"itens": sugestoes.autocompletar(cliente, alvo, prefixo, projeto_slug)})
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.route("/")
def home():
    """Página inicial: boas-vindas e atalhos. Links antigos da busca geral
    (/?q=...) vão para /busca com a mesma consulta."""
    if "q" in request.args:
        return redirect(f"/busca?{request.query_string.decode('utf-8', 'replace')}")
    qtd_projetos = None  # None = não deu para saber (MySQL fora): o card some com o número
    try:
        qtd_projetos = len(projetos.listar())
    except Exception:
        pass
    return render_template("home.html", qtd_projetos=qtd_projetos)


@app.route("/busca")
def index():
    return _tela_busca(request.args.get("q", "").strip(), indices.padrao_busca())


@app.route("/projetos")
def projetos_pagina():
    erro = None
    lista = []
    try:
        lista = projetos.listar()
        for p in lista:
            p["qtd_indices"] = len(p["indices"])
    except Exception as e:
        erro = f"Não consegui carregar os projetos: {e}"
    return render_template("projetos.html", projetos=lista, erro=erro)


@app.route("/p/<slug>")
def projeto_busca(slug):
    try:
        projeto = projetos.obter_por_slug(slug)
    except Exception as e:
        return render_template(
            "erro.html", titulo="Projetos indisponíveis",
            mensagem=f"Não consegui carregar o projeto agora ({e}). A busca geral continua funcionando.",
        ), 503
    if not projeto:
        abort(404, "Projeto não encontrado.")
    alvo = ",".join(projeto["indices"])
    return _tela_busca(request.args.get("q", "").strip(), alvo, projeto)


@app.route("/admin")
def admin():
    return redirect("/admin/indices")


@app.route("/admin/documentos", methods=["GET", "POST"])
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


@app.route("/admin/fila")
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


@app.route("/admin/fila/<int:job_id>/repetir", methods=["POST"])
def fila_repetir(job_id):
    try:
        jobs.tentar_novamente(job_id)
    except Exception:
        pass
    return redirect("/admin/fila")


@app.route("/admin/fila/limpar", methods=["POST"])
def fila_limpar():
    try:
        jobs.limpar_concluidos()
    except Exception:
        pass
    return redirect("/admin/fila")


@app.route("/admin/indices", methods=["GET", "POST"])
def indices_pagina():
    erro = None
    sucesso = None
    nome_digitado = None

    if request.method == "POST":
        nome_digitado = request.form.get("nome", "")
        try:
            nome_real = indices.validar_nome_novo(nome_digitado)
            cliente.criar_indice(nome_real, indices.MAPEAMENTO)
            return redirect(f"/admin/indices?criado={indices.nome_exibicao(nome_real)}")
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
    if request.args.get("reindexado"):
        n = request.args.get("n", 0, type=int)
        sem_arquivo = request.args.get("sem_arquivo", 0, type=int)
        ja_na_fila = request.args.get("ja_na_fila", 0, type=int)
        sucesso = f'Índice "{request.args["reindexado"]}": {n} documento(s) enviados para a fila de indexação'
        if ja_na_fila:
            sucesso += f", {ja_na_fila} já estavam na fila"
        if sem_arquivo:
            sucesso += f", {sem_arquivo} sem o arquivo original no servidor (precisam ser enviados de novo)"
        sucesso += "."

    lista = []
    try:
        lista = _listar_indices()
    except Exception as e:
        erro = erro or f"Não consegui listar os índices no OpenSearch: {e}"

    try:
        pendentes = cliente.contar_desatualizados(indices.padrao_busca()) if lista else {}
    except Exception:
        pendentes = {}
    for i in lista:
        i["pendentes"] = pendentes.get(i["nome"], 0)

    return render_template(
        "indices.html",
        indices=lista,
        erro=erro,
        sucesso=sucesso,
        nome_digitado=nome_digitado,
        prefixo=config.INDEX_PREFIX,
    )


def _indices_para_escolher():
    """Índices que existem no OpenSearch, para os checkboxes do projeto."""
    try:
        return _listar_indices(), None
    except Exception as e:
        return [], f"Não consegui listar os índices no OpenSearch: {e}"


@app.route("/admin/projetos")
def admin_projetos():
    erro = None
    lista = []
    try:
        lista = projetos.listar()
        for p in lista:
            p["indices_exibicao"] = [indices.nome_exibicao(i) for i in p["indices"]]
    except Exception as e:
        erro = f"Banco de dados dos projetos indisponível: {e}"
    mensagens = {"criado": "Projeto criado.", "salvo": "Projeto salvo.", "apagado": "Projeto apagado."}
    return render_template("projetos_admin.html", projetos=lista, erro=erro, sucesso=mensagens.get(request.args.get("ok")))


def _formulario_projeto(projeto):
    """Cria (projeto=None) ou edita um projeto. Mesma tela para os dois."""
    disponiveis, erro = _indices_para_escolher()
    nome = projeto["nome"] if projeto else ""
    descricao = (projeto["descricao"] or "") if projeto else ""
    marcados = list(projeto["indices"]) if projeto else []

    if request.method == "POST":
        nome = request.form.get("nome", "")
        descricao = request.form.get("descricao", "")
        marcados = request.form.getlist("indice")
        try:
            if projeto:
                projetos.atualizar(projeto["id"], nome, descricao, marcados)
                return redirect("/admin/projetos?ok=salvo")
            projetos.criar(nome, descricao, marcados)
            return redirect("/admin/projetos?ok=criado")
        except ValueError as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro ao salvar o projeto: {e}"

    return render_template(
        "projeto_form.html", projeto=projeto, nome=nome, descricao=descricao,
        marcados=marcados, disponiveis=disponiveis, erro=erro,
        limite_nome=projetos.LIMITE_NOME, limite_descricao=projetos.LIMITE_DESCRICAO,
    )


def _projeto_ou_erro(projeto_id):
    """(projeto, resposta_de_erro): o projeto, ou a página de erro pronta."""
    try:
        projeto = projetos.obter(projeto_id)
    except Exception as e:
        return None, (render_template("erro.html", titulo="Projetos indisponíveis", mensagem=f"Banco de dados indisponível: {e}"), 503)
    if not projeto:
        abort(404, "Projeto não encontrado.")
    return projeto, None


@app.route("/admin/projetos/novo", methods=["GET", "POST"])
def admin_projeto_novo():
    return _formulario_projeto(None)


@app.route("/admin/projetos/<int:projeto_id>", methods=["GET", "POST"])
def admin_projeto_editar(projeto_id):
    projeto, erro = _projeto_ou_erro(projeto_id)
    return erro or _formulario_projeto(projeto)


@app.route("/admin/projetos/<int:projeto_id>/apagar", methods=["GET", "POST"])
def admin_projeto_apagar(projeto_id):
    projeto, erro = _projeto_ou_erro(projeto_id)
    if erro:
        return erro
    if request.method == "POST":
        projetos.apagar(projeto_id)
        return redirect("/admin/projetos?ok=apagado")
    return render_template("projeto_apagar.html", projeto=projeto)


@app.route("/admin/dicionario", methods=["GET", "POST"])
def admin_dicionario():
    erro = None
    termo = sinonimos = ""
    if request.method == "POST":
        termo = request.form.get("termo", "")
        sinonimos = request.form.get("sinonimos", "")
        try:
            dicionario.criar(termo, sinonimos)
            return redirect("/admin/dicionario?ok=criado")
        except ValueError as e:
            erro = str(e)
        except Exception as e:
            erro = f"Banco de dados do dicionário indisponível: {e}"

    lista = []
    try:
        lista = dicionario.listar()
    except Exception as e:
        erro = erro or f"Banco de dados do dicionário indisponível: {e}"
    mensagens = {"criado": "Termo adicionado.", "salvo": "Termo salvo.", "apagado": "Termo apagado."}
    return render_template(
        "dicionario_admin.html", entradas=lista, erro=erro, termo=termo, sinonimos=sinonimos,
        sucesso=mensagens.get(request.args.get("ok")),
        limite_termo=dicionario.LIMITE_TERMO, limite_sinonimos=dicionario.LIMITE_SINONIMOS,
    )


@app.route("/admin/dicionario/<int:entrada_id>", methods=["GET", "POST"])
def admin_dicionario_editar(entrada_id):
    try:
        entrada = dicionario.obter(entrada_id)
    except Exception as e:
        return render_template("erro.html", titulo="Dicionário indisponível", mensagem=f"Banco de dados indisponível: {e}"), 503
    if not entrada:
        abort(404, "Termo não encontrado.")

    erro = None
    termo, sinonimos = entrada["termo"], entrada["sinonimos"] or ""
    if request.method == "POST":
        termo = request.form.get("termo", "")
        sinonimos = request.form.get("sinonimos", "")
        try:
            dicionario.atualizar(entrada_id, termo, sinonimos)
            return redirect("/admin/dicionario?ok=salvo")
        except ValueError as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro ao salvar: {e}"
    return render_template(
        "dicionario_form.html", entrada=entrada, termo=termo, sinonimos=sinonimos, erro=erro,
        limite_termo=dicionario.LIMITE_TERMO, limite_sinonimos=dicionario.LIMITE_SINONIMOS,
    )


@app.route("/admin/dicionario/<int:entrada_id>/apagar", methods=["POST"])
def admin_dicionario_apagar(entrada_id):
    try:
        dicionario.apagar(entrada_id)
    except Exception as e:
        return render_template("erro.html", titulo="Dicionário indisponível", mensagem=f"Banco de dados indisponível: {e}"), 503
    return redirect("/admin/dicionario?ok=apagado")


@app.route("/admin/indices/<nome>/reindexar", methods=["POST"])
def indice_reindexar(nome):
    """Recoloca na fila os documentos do índice que ainda não têm os campos das
    facetas (tipo e data), usando o arquivo original guardado no servidor."""
    if not indices.eh_gerenciado(nome):
        abort(404, "Índice não encontrado.")

    enfileirados = sem_arquivo = ja_na_fila = 0
    try:
        for doc_id, nome_arquivo, caminho in cliente.listar_desatualizados(nome):
            if not caminho or not os.path.isfile(caminho):
                sem_arquivo += 1
            elif jobs.job_ativo(nome, doc_id):
                ja_na_fila += 1
            else:
                jobs.enfileirar(nome, nome_arquivo or os.path.basename(caminho), caminho, doc_id)
                enfileirados += 1
    except Exception as e:
        return render_template(
            "erro.html", titulo="Não consegui reindexar",
            mensagem=f"Erro ao reindexar \"{indices.nome_exibicao(nome)}\": {e}",
        ), 500

    return redirect(
        f"/admin/indices?reindexado={quote(indices.nome_exibicao(nome))}"
        f"&n={enfileirados}&sem_arquivo={sem_arquivo}&ja_na_fila={ja_na_fila}"
    )


@app.route("/admin/indices/<nome>/apagar", methods=["GET", "POST"])
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
                try:
                    projetos.desvincular_indice(nome)
                except Exception:
                    pass
                return redirect(f"/admin/indices?apagado={exibicao}")

    return render_template("indice_apagar.html", exibicao=exibicao, docs=docs, erro=erro)


POR_PAGINA_AUDITORIA = 50
PERIODOS_AUDITORIA = [(1, "Últimas 24 horas"), (7, "Últimos 7 dias"), (30, "Últimos 30 dias"), (90, "Últimos 90 dias"), (0, "Todo o período")]


@app.route("/admin/auditoria")
def auditoria_pagina():
    try:
        dias = int(request.args.get("dias", 7))
    except ValueError:
        dias = 7
    if dias not in {d for d, _ in PERIODOS_AUDITORIA}:
        dias = 7
    termo = request.args.get("termo", "").strip()[:200]
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except ValueError:
        pagina = 1

    erro = None
    resumo = {"buscas": 0, "sem_resultado": 0, "termos": 0}
    populares, sem_resultado, linhas, total = [], [], [], 0
    try:
        resumo = auditoria.resumo(dias, termo)
        populares = auditoria.mais_buscados(dias, termo)
        sem_resultado = auditoria.mais_buscados(dias, termo, sem_resultado=True)
        linhas, total = auditoria.listar(dias, termo, pagina, POR_PAGINA_AUDITORIA)
        for linha in linhas:
            linha["criado_local"] = linha["criado_em"] + timedelta(hours=config.FUSO_HORAS)
    except Exception as e:
        erro = f"Banco de dados da auditoria indisponível: {e}"

    paginas = max(1, -(-total // POR_PAGINA_AUDITORIA))
    return render_template(
        "auditoria.html",
        erro=erro, dias=dias, termo=termo, periodos=PERIODOS_AUDITORIA,
        resumo=resumo, populares=populares, sem_resultado=sem_resultado,
        linhas=linhas, total=total, pagina=pagina, paginas=paginas,
    )


ROTAS_ANTIGAS = {"/ingest": "/admin/documentos", "/fila": "/admin/fila", "/indices": "/admin/indices"}


def _redirecionar_antiga(destino):
    return lambda: redirect(destino, 308)  # 308 mantém o método (um POST de upload não se perde)


for _antiga, _nova in ROTAS_ANTIGAS.items():
    app.add_url_rule(_antiga, f"antiga{_antiga.replace('/', '_')}", _redirecionar_antiga(_nova), methods=["GET", "POST"])


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
