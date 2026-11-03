"""
Frontend (Flask) do buscador (nome do produto em config.APP_NOME): busca em todos os índices do OpenSearch,
upload com indexação em segundo plano (fila no MySQL) e gestão de índices.

Uso:
    pip install -r requirements.txt
    python web_app.py

Depois acesse http://localhost:5000 no navegador.

Este app roda como um "proxy": o navegador nunca fala direto com o
OpenSearch (evita expor usuário/senha do cluster no front-end).
"""

import os
from html import escape as escapar_html
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, send_from_directory
from opensearchpy.exceptions import RequestError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import acesso
import auditoria
import buscas_salvas
import config
import configuracoes
import crawler
import destaques
import dicionario
import email_envio
import facetas
import fontes
import indices
import jobs
import notificacoes
import pastas
import projetos
import relatorios
import relevancia
import sugestoes
import temas
import worker
from opensearch_client import OpenSearchClient
from utils import escapar_destaque, extensao, gerar_doc_id, montar_trecho, pode_embutir, renderizar_markdown

MODO_DEBUG = config.MODO_DEBUG  # FLASK_DEBUG=1 no .env liga; em produção fica sempre desligado

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


# Nome e logo do produto (config.APP_*), disponíveis em todos os templates.
_destaque = config.APP_NOME_DESTAQUE if config.APP_NOME_DESTAQUE and config.APP_NOME.endswith(config.APP_NOME_DESTAQUE) else ""
_MARCA = {
    "app_nome": config.APP_NOME,
    "app_nome_inicio": config.APP_NOME[: len(config.APP_NOME) - len(_destaque)],
    "app_nome_destaque": _destaque,
    "app_logo": config.APP_LOGO,
    "app_logo_alt": config.APP_LOGO_ALT,
}


@app.context_processor
def contexto_comum():
    return {"eh_admin": acesso.eh_admin(), **_MARCA}


@app.template_global()
def url_com(**mudancas):
    """URL da página atual com alguns parâmetros trocados (None remove o
    parâmetro; uma lista grava o parâmetro repetido). Usado pela ordenação,
    pela paginação e pelos temas, que mantêm a consulta e os filtros."""
    args = request.args.copy()
    for chave, valor in mudancas.items():
        args.pop(chave, None)
        if isinstance(valor, (list, tuple)):
            args.setlist(chave, list(valor))
        elif valor is not None:
            args[chave] = valor
    texto = urlencode(list(args.items(multi=True)))
    return f"{request.path}?{texto}" if texto else request.path


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


def _destaques_da_busca(q, projeto):
    """Documentos destacados pelo administrador para essa consulta (bloco
    acima dos resultados). Na busca de um projeto, só os índices dele.
    Documento que não existe mais no índice é ignorado."""
    permitido = set(projeto["indices"]).__contains__ if projeto else indices.eh_gerenciado
    try:
        entradas = destaques.casam(q, permitido)
    except Exception:
        return []  # destaque é um extra: sem ele a busca segue normal
    lista = []
    for e in entradas:
        doc = cliente.get(e["indice"], e["doc_id"])
        if not doc:
            continue
        fonte = doc["_source"]
        nome = fonte.get("nome") or e["nome"] or "(sem nome)"
        lista.append({
            "id": e["doc_id"],
            "indice": e["indice"],
            "indice_nome": indices.nome_exibicao(e["indice"]),
            "nome": nome,
            "titulo": fonte.get("titulo") or nome,
            "tipo": extensao(nome).upper() or "Documento",
            "descricao": e["descricao"],
            "trecho": montar_trecho(None, fonte.get("conteudo", "")),
        })
    return lista


def _pesquisar(q, alvo, projeto=None):
    """Roda a busca em `alvo` (expressão de índices) e prepara o que a tela
    mostra. O `projeto`, quando existe, entra no registro da auditoria e nas
    sugestões (que ficam restritas às buscas daquele projeto)."""
    resultados = []
    total = 0
    erro = None
    filtros = facetas.ler_filtros(request.args)
    temas_ativos = temas.ler_temas(request.args)
    ordem = relevancia.ler_ordem(request.args)
    pagina = relevancia.ler_pagina(request.args)
    lista_facetas = []
    lista_destaques = []
    lista_temas = []
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

            # pesos por campo, frase exata, erros de digitação, sinônimos e bônus de recência
            body = {
                "query": temas.restringir(relevancia.montar_consulta(q, expansoes), temas_ativos),
                "highlight": {
                    "fields": {
                        # palavra exata; se só a variação em português casou, o destaque vem do .pt
                        "conteudo": {"fragment_size": 220, "number_of_fragments": 1},
                        "conteudo.pt": {"fragment_size": 220, "number_of_fragments": 1},
                    }
                },
                "_source": ["nome", "titulo", "caminho", "conteudo"],
            }
            relevancia.aplicar(body, ordem, pagina)

            post_filter, aggs = facetas.montar_consulta(filtros)
            body["aggs"] = aggs
            if post_filter:
                body["post_filter"] = post_filter

            resp = cliente.search(alvo, body, ignore_unavailable=True)
            total = resp["hits"]["total"]["value"]
            lista_facetas = facetas.interpretar(resp.get("aggregations"), filtros)
            lista_temas = temas.consultar(cliente, alvo, body["query"], post_filter, q, temas_ativos, total)
            if total <= 3:
                correcao = sugestoes.correcao(cliente, alvo, q)
            if total == 0:
                populares = sugestoes.populares(slug)
            if pagina == 1:
                lista_destaques = _destaques_da_busca(q, projeto)
            if pagina == 1 and ordem == "relevancia":
                # trocar de página ou de ordem não é uma busca nova
                resumo = "; ".join(
                    parte for parte in (
                        f"projeto={projeto['slug']}" if projeto else None,
                        facetas.resumo_texto(filtros),
                        temas.resumo_texto(temas_ativos),
                    ) if parte
                )
                auditoria.registrar_busca(q, total, *auditoria.contexto_requisicao(), filtros=resumo[:500] or None)

            for hit in resp["hits"]["hits"]:
                fonte = hit["_source"]

                # Destaque do OpenSearch; sem ele, os primeiros 220 caracteres (já escapado)
                destaques_hit = hit.get("highlight", {})
                highlight = (destaques_hit.get("conteudo") or destaques_hit.get("conteudo.pt") or [None])[0]
                trecho = montar_trecho(highlight, fonte.get("conteudo", ""))

                nome = fonte.get("nome", "(sem nome)")

                resultados.append({
                    "id": hit["_id"],
                    "indice": hit["_index"],
                    "indice_nome": indices.nome_exibicao(hit["_index"]),
                    "nome": nome,
                    "titulo": fonte.get("titulo") or nome,
                    "tipo": extensao(nome).upper() or "Documento",
                    "trecho": trecho or "(Sem prévia de texto)",
                })

    except Exception as e:
        erro = str(e)

    if correcao:
        correcao["destaque"] = escapar_destaque(correcao["destaque"])
    paginas = relevancia.total_paginas(total)
    return {
        "resultados": resultados, "total": total, "erro": erro,
        "facetas": lista_facetas, "filtrando": facetas.ha_filtros(filtros),
        "expansoes": expansoes, "correcao": correcao, "populares": populares,
        "destaques": lista_destaques,
        "temas": lista_temas, "temas_ativos": temas_ativos,
        "ordem": ordem, "ordenacoes": relevancia.ORDENACOES,
        "pagina": pagina, "paginas": paginas, "barra_paginas": relevancia.paginas_visiveis(pagina, paginas),
    }


def _tela_busca(q, alvo, projeto=None):
    return render_template(
        "index.html", q=q, projeto=projeto, acao=f"/p/{projeto['slug']}" if projeto else "/busca",
        parametros_salvar=buscas_salvas.parametros_da_tela(request.args),
        frequencias_alerta=buscas_salvas.FREQUENCIAS,
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
        # mesma lista que o servidor aceita (config.EXTENSOES_VALIDAS), para o seletor não esconder nenhum tipo
        extensoes_aceitas=",".join(sorted(config.EXTENSOES_VALIDAS)),
        # e o texto de ajuda sai da mesma lista (antes era fixo e omitia JPG/PNG)
        extensoes_texto=", ".join(sorted(e.lstrip(".").upper() for e in config.EXTENSOES_VALIDAS)),
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
    if request.args.get("sem_texto"):
        n = request.args.get("n", 0, type=int)
        sem_arquivo = request.args.get("sem_arquivo", 0, type=int)
        ja_na_fila = request.args.get("ja_na_fila", 0, type=int)
        sucesso = f'Índice "{request.args["sem_texto"]}": {n} documento(s) sem texto enviados para a fila (Docling com OCR)'
        if ja_na_fila:
            sucesso += f", {ja_na_fila} já estavam na fila"
        if sem_arquivo:
            sucesso += f", {sem_arquivo} sem o arquivo original no servidor"
        sucesso += "." if n or ja_na_fila or sem_arquivo else " Nenhum documento sem texto encontrado."
    if request.args.get("portugues"):
        sucesso = (f'Português ativado em "{request.args["portugues"]}". Os documentos estão sendo reprocessados em segundo plano '
                   "(a partir do texto já salvo, sem Docling); a busca funciona normalmente enquanto isso.")
    if request.args.get("reindexado"):
        n = request.args.get("n", 0, type=int)
        sem_arquivo = request.args.get("sem_arquivo", 0, type=int)
        ja_na_fila = request.args.get("ja_na_fila", 0, type=int)
        titulos = request.args.get("titulos", 0, type=int)
        sucesso = f'Índice "{request.args["reindexado"]}": {n} documento(s) enviados para a fila de indexação'
        if titulos:
            sucesso += f", {titulos} título(s) preenchido(s)"
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
    em_atualizacao = cliente.indices_em_atualizacao() if lista else set()
    for i in lista:
        i["pendentes"] = pendentes.get(i["nome"], 0)
        try:
            i["portugues"] = cliente.portugues_ativo(i["nome"])
        except Exception:
            i["portugues"] = None
        i["atualizando"] = i["nome"] in em_atualizacao

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


def _destaques_indisponivel(e):
    return render_template("erro.html", titulo="Destaques indisponíveis", mensagem=f"Banco de dados indisponível: {e}"), 503


@app.route("/admin/destaques")
def admin_destaques():
    erro = None
    lista = []
    try:
        lista = destaques.listar()
        for d in lista:
            d["indice_nome"] = indices.nome_exibicao(d["indice"])
    except Exception as e:
        erro = f"Banco de dados dos destaques indisponível: {e}"
    mensagens = {"criado": "Destaque criado.", "salvo": "Destaque salvo.", "apagado": "Destaque apagado."}
    return render_template("destaques_admin.html", destaques=lista, erro=erro, sucesso=mensagens.get(request.args.get("ok")))


def _buscar_documentos(texto):
    """Documentos cujo título ou nome casa com o texto, para escolher o destaque."""
    resp = cliente.search(indices.padrao_busca(), {
        "size": 10,
        "query": {"multi_match": {"query": texto, "fields": ["titulo^2", "nome"], "type": "bool_prefix", "operator": "and"}},
        "_source": ["nome", "titulo"],
    }, ignore_unavailable=True)
    return [
        {
            "indice": hit["_index"], "indice_nome": indices.nome_exibicao(hit["_index"]), "doc_id": hit["_id"],
            "nome": hit["_source"].get("nome", "(sem nome)"), "titulo": hit["_source"].get("titulo"),
        }
        for hit in resp["hits"]["hits"]
    ]


def _formulario_destaque(destaque):
    """Cria (destaque=None) ou edita um destaque. Na criação, o documento vem
    de ?indice=...&doc=... (link "Destacar na busca" do preview ou escolha na
    lista); na edição o documento é fixo (para trocar, apague e crie outro)."""
    erro = None
    termos = request.values.get("termos", destaque["termos"] if destaque else "")
    descricao = request.values.get("descricao", (destaque["descricao"] or "") if destaque else "")
    busca_doc = request.args.get("busca_doc", "").strip()[:200]
    candidatos = []

    if destaque:
        indice, doc_id = destaque["indice"], destaque["doc_id"]
    else:
        indice, doc_id = request.values.get("indice", ""), request.values.get("doc", "")
    escolhido = None
    if indice and doc_id:
        indice_ok, doc = _documento(indice, doc_id)
        if doc:
            fonte = doc["_source"]
            escolhido = {
                "indice": indice_ok, "indice_nome": indices.nome_exibicao(indice_ok), "doc_id": doc_id,
                "nome": fonte.get("nome", "(sem nome)"), "titulo": fonte.get("titulo"),
            }
        elif destaque:
            escolhido = {
                "indice": indice, "indice_nome": indices.nome_exibicao(indice), "doc_id": doc_id,
                "nome": destaque["nome"] or "(sem nome)", "titulo": None, "sumiu": True,
            }
        else:
            erro = "Documento não encontrado no índice."

    if request.method == "POST":
        try:
            if destaque:
                destaques.atualizar(destaque["id"], termos, descricao)
                return redirect("/admin/destaques?ok=salvo")
            if not escolhido:
                raise ValueError(erro or "Escolha o documento.")
            destaques.criar(termos, descricao, escolhido["indice"], escolhido["doc_id"], escolhido["nome"])
            return redirect("/admin/destaques?ok=criado")
        except ValueError as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro ao salvar o destaque: {e}"
    elif busca_doc and not escolhido:
        try:
            candidatos = _buscar_documentos(busca_doc)
        except Exception as e:
            erro = f"Não consegui buscar os documentos: {e}"

    return render_template(
        "destaque_form.html", destaque=destaque, escolhido=escolhido, candidatos=candidatos,
        busca_doc=busca_doc, termos=termos, descricao=descricao, erro=erro,
        limite_termos=destaques.LIMITE_TERMOS, limite_descricao=destaques.LIMITE_DESCRICAO,
    )


@app.route("/admin/destaques/novo", methods=["GET", "POST"])
def admin_destaque_novo():
    return _formulario_destaque(None)


@app.route("/admin/destaques/<int:destaque_id>", methods=["GET", "POST"])
def admin_destaque_editar(destaque_id):
    try:
        destaque = destaques.obter(destaque_id)
    except Exception as e:
        return _destaques_indisponivel(e)
    if not destaque:
        abort(404, "Destaque não encontrado.")
    return _formulario_destaque(destaque)


@app.route("/admin/destaques/<int:destaque_id>/apagar", methods=["POST"])
def admin_destaque_apagar(destaque_id):
    try:
        destaques.apagar(destaque_id)
    except Exception as e:
        return _destaques_indisponivel(e)
    return redirect("/admin/destaques?ok=apagado")


@app.route("/admin/indices/<nome>/reindexar", methods=["POST"])
def indice_reindexar(nome):
    """Atualiza os documentos antigos do índice: preenche o `titulo` a partir
    do texto já salvo (rápido, sem Docling) e recoloca na fila os que ainda
    não têm os campos das facetas (tipo e data), usando o arquivo original
    guardado no servidor."""
    if not indices.eh_gerenciado(nome):
        abort(404, "Índice não encontrado.")

    enfileirados = sem_arquivo = ja_na_fila = titulos = 0
    try:
        titulos = cliente.preencher_titulos(nome)
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
        f"&n={enfileirados}&sem_arquivo={sem_arquivo}&ja_na_fila={ja_na_fila}&titulos={titulos}"
    )


@app.route("/admin/indices/<nome>/sem-texto", methods=["POST"])
def indice_sem_texto(nome):
    """Recoloca na fila os documentos que entraram sem texto (PDF escaneado,
    imagem), para passarem de novo pelo Docling, agora com OCR."""
    if not indices.eh_gerenciado(nome):
        abort(404, "Índice não encontrado.")
    enfileirados = sem_arquivo = ja_na_fila = 0
    try:
        for doc_id, nome_arquivo, caminho in cliente.listar_sem_texto(nome):
            if not caminho or not os.path.isfile(caminho):
                sem_arquivo += 1
            elif jobs.job_ativo(nome, doc_id):
                ja_na_fila += 1
            else:
                jobs.enfileirar(nome, nome_arquivo or os.path.basename(caminho), caminho, doc_id)
                enfileirados += 1
    except Exception as e:
        return render_template(
            "erro.html", titulo="Não consegui reprocessar",
            mensagem=f"Erro no índice \"{indices.nome_exibicao(nome)}\": {e}",
        ), 500
    return redirect(
        f"/admin/indices?sem_texto={quote(indices.nome_exibicao(nome))}"
        f"&n={enfileirados}&sem_arquivo={sem_arquivo}&ja_na_fila={ja_na_fila}"
    )


@app.route("/admin/indices/<nome>/portugues", methods=["POST"])
def indice_portugues(nome):
    """Liga o analisador português num índice criado antes dele."""
    if not indices.eh_gerenciado(nome):
        abort(404, "Índice não encontrado.")
    try:
        cliente.ativar_portugues(nome)
    except Exception as e:
        return render_template(
            "erro.html", titulo="Não consegui ativar o português",
            mensagem=f"Erro no índice \"{indices.nome_exibicao(nome)}\": {e}",
        ), 500
    return redirect(f"/admin/indices?portugues={quote(indices.nome_exibicao(nome))}")


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
                try:
                    destaques.apagar_do_indice(nome)
                except Exception:
                    pass
                try:
                    fontes.apagar_do_indice(nome)
                except Exception:
                    pass
                try:
                    pastas.remover_do_indice(nome)
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


STATUS_EXECUCAO = {
    "executando": "Executando…",
    "concluida": "Concluída",
    "erro": "Erro",
    "interrompida": "Interrompida",
}


def _hora_local(valor):
    return valor + timedelta(hours=config.FUSO_HORAS) if valor else None


def _preparar_execucao(e):
    if e:
        e["status_texto"] = STATUS_EXECUCAO.get(e["status"], e["status"])
        e["classe"] = {"executando": "processando", "concluida": "concluido"}.get(e["status"], "erro")
        e["iniciado_local"] = _hora_local(e["iniciado_em"])
        e["finalizado_local"] = _hora_local(e["finalizado_em"])
    return e


def _fontes_indisponivel(e):
    return render_template("erro.html", titulo="Fontes indisponíveis", mensagem=f"Banco de dados indisponível: {e}"), 503


@app.route("/admin/fontes")
def admin_fontes():
    erro = None
    lista = []
    try:
        lista = fontes.listar()
        for f in lista:
            f["indice_nome"] = indices.nome_exibicao(f["indice"])
            f["proxima_local"] = _hora_local(f["proxima_em"])
            _preparar_execucao(f["ultima"])
    except Exception as e:
        erro = f"Banco de dados das fontes indisponível: {e}"
    executando = any(f["solicitada"] or (f["ultima"] and f["ultima"]["status"] == "executando") for f in lista)
    mensagens = {
        "criada": "Fonte criada. A primeira passada começa em alguns segundos.",
        "salva": "Fonte salva.", "apagada": "Fonte apagada.",
        "solicitada": "Passada solicitada: começa em alguns segundos.",
    }
    return render_template(
        "fontes_admin.html", fontes=lista, erro=erro, executando=executando,
        sucesso=mensagens.get(request.args.get("ok")), raizes=fontes.raizes_permitidas(),
    )


def _formulario_fonte(fonte):
    """Cria (fonte=None) ou edita uma fonte. O botão "Testar" lista o que a
    pasta tem, sem gravar nem indexar nada."""
    disponiveis, erro = _indices_para_escolher()
    validas = sorted(e.lstrip(".") for e in config.EXTENSOES_VALIDAS)
    if fonte:
        conf = fonte["config"]
        dados = {
            "nome": fonte["nome"], "indice": fonte["indice"], "caminho": conf["caminho"],
            "extensoes": conf["extensoes"], "ignorar": conf.get("ignorar", ""), "subpastas": conf.get("subpastas"),
            "intervalo": fonte["intervalo_horas"], "ativa": bool(fonte["ativa"]),
        }
    else:
        dados = {
            "nome": "", "indice": config.INDEX_NAME, "caminho": "", "extensoes": validas,
            "ignorar": fontes.IGNORAR_PADRAO, "subpastas": True, "intervalo": 24, "ativa": True,
        }
    teste = None

    if request.method == "POST":
        dados = {
            "nome": request.form.get("nome", ""),
            "indice": fonte["indice"] if fonte else request.form.get("indice", ""),
            "caminho": request.form.get("caminho", ""),
            "extensoes": request.form.getlist("extensao"),
            "ignorar": request.form.get("ignorar", ""),
            "subpastas": bool(request.form.get("subpastas")),
            "intervalo": request.form.get("intervalo", 0, type=int),
            "ativa": bool(request.form.get("ativa")) if fonte else True,
        }
        try:
            nome, indice, conf, intervalo = fontes.validar(
                dados["nome"], dados["indice"], dados["caminho"], dados["extensoes"],
                dados["ignorar"], dados["subpastas"], dados["intervalo"],
            )
            if request.form.get("acao") == "testar":
                total, primeiros, problemas, parou = crawler.previa(conf)
                teste = {"total": total, "primeiros": primeiros, "problemas": problemas, "parou": parou}
            elif fonte:
                fontes.atualizar(fonte["id"], nome, conf, intervalo, dados["ativa"])
                return redirect("/admin/fontes?ok=salva")
            else:
                fontes.criar(nome, indice, conf, intervalo)
                return redirect("/admin/fontes?ok=criada")
        except (ValueError, RuntimeError) as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro ao salvar a fonte: {e}"

    historico = []
    if fonte:
        try:
            historico = [_preparar_execucao(e) for e in fontes.execucoes(fonte["id"])]
        except Exception:
            pass
    return render_template(
        "fonte_form.html", fonte=fonte, dados=dados, disponiveis=disponiveis, validas=validas,
        intervalos=fontes.INTERVALOS, teste=teste, indice_nome=indices.nome_exibicao(fonte["indice"]) if fonte else None, historico=historico, erro=erro,
        raizes=fontes.raizes_permitidas(), limite_nome=fontes.LIMITE_NOME, limite_ignorar=fontes.LIMITE_IGNORAR,
    )


def _fonte_ou_erro(fonte_id):
    try:
        fonte = fontes.obter(fonte_id)
    except Exception as e:
        return None, _fontes_indisponivel(e)
    if not fonte:
        abort(404, "Fonte não encontrada.")
    return fonte, None


@app.route("/admin/fontes/nova", methods=["GET", "POST"])
def admin_fonte_nova():
    return _formulario_fonte(None)


@app.route("/admin/fontes/<int:fonte_id>", methods=["GET", "POST"])
def admin_fonte_editar(fonte_id):
    fonte, erro = _fonte_ou_erro(fonte_id)
    return erro or _formulario_fonte(fonte)


@app.route("/admin/fontes/<int:fonte_id>/rodar", methods=["POST"])
def admin_fonte_rodar(fonte_id):
    try:
        fontes.solicitar(fonte_id)
    except Exception as e:
        return _fontes_indisponivel(e)
    return redirect("/admin/fontes?ok=solicitada")


@app.route("/admin/fontes/<int:fonte_id>/apagar", methods=["POST"])
def admin_fonte_apagar(fonte_id):
    """Apaga a fonte; com a caixa marcada, tira também do índice os documentos
    que vieram dela. Os arquivos na pasta de origem nunca são tocados."""
    fonte, erro = _fonte_ou_erro(fonte_id)
    if erro:
        return erro
    try:
        if request.form.get("remover_documentos"):
            ids = fontes.doc_ids(fonte_id)
            if ids:
                cliente.apagar_documentos(fonte["indice"], ids)
        fontes.apagar(fonte_id)
    except Exception as e:
        return render_template("erro.html", titulo="Não consegui apagar a fonte", mensagem=str(e)), 500
    return redirect("/admin/fontes?ok=apagada")


@app.route("/admin/auditoria/exportar")
def auditoria_exportar():
    """CSV com todas as buscas do filtro da tela (período e termo)."""
    try:
        dias = int(request.args.get("dias", 7))
    except ValueError:
        dias = 7
    if dias not in {d for d, _ in PERIODOS_AUDITORIA}:
        dias = 7
    termo = request.args.get("termo", "").strip()[:200]
    try:
        linhas, _cortou = auditoria.exportar(dias, termo)
    except Exception as e:
        return render_template("erro.html", titulo="Auditoria indisponível", mensagem=f"Banco de dados indisponível: {e}"), 503
    nome = f"auditoria-buscas-{(datetime.now(timezone.utc) + timedelta(hours=config.FUSO_HORAS)):%Y%m%d-%H%M}.csv"
    return Response(
        relatorios.csv_auditoria(linhas), mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"', "Cache-Control": "no-store"},
    )


def _email_indisponivel(e):
    return render_template("erro.html", titulo="Configurações indisponíveis", mensagem=f"Banco de dados indisponível: {e}"), 503


@app.route("/admin/email", methods=["GET", "POST"])
def admin_email():
    """Servidor SMTP, endereço do site para os links e alertas do sistema.
    A senha do SMTP só vem da variável SMTP_SENHA (Dokploy)."""
    erro = sucesso = None
    try:
        conf = configuracoes.ler_todas()
    except Exception as e:
        return _email_indisponivel(e)

    if request.method == "POST":
        acao = request.form.get("acao")
        try:
            if acao == "smtp":
                porta = request.form.get("smtp_porta", "").strip()
                if porta and not (porta.isdigit() and 0 < int(porta) < 65536):
                    raise ValueError("Porta inválida.")
                seguranca = request.form.get("smtp_seguranca")
                if seguranca not in {"starttls", "ssl", "nenhuma"}:
                    raise ValueError("Escolha o tipo de conexão.")
                remetente = request.form.get("smtp_remetente", "").strip()
                if remetente:
                    email_envio.separar_emails(remetente)
                url_base = request.form.get("url_base", "").strip().rstrip("/")
                if url_base and not url_base.startswith(("http://", "https://")):
                    raise ValueError(f"O endereço do {config.APP_NOME} deve começar com http:// ou https://.")
                conf.update({
                    "smtp_host": request.form.get("smtp_host", "").strip()[:255], "smtp_porta": porta,
                    "smtp_seguranca": seguranca, "smtp_usuario": request.form.get("smtp_usuario", "").strip()[:255],
                    "smtp_remetente": remetente[:255], "url_base": url_base[:255],
                })
                configuracoes.salvar(conf)
                sucesso = "Servidor de e-mail salvo."
            elif acao == "teste":
                destino = email_envio.separar_emails(request.form.get("destino", ""))
                if not destino:
                    raise ValueError("Informe para quem mandar o e-mail de teste.")
                email_envio.enviar(
                    destino, f"{config.APP_NOME} — e-mail de teste",
                    f"<p style='font-family:Arial,sans-serif'>Se você recebeu esta mensagem, o envio de e-mails do {escapar_html(config.APP_NOME)} está funcionando.</p>",
                    origem="teste", conf=conf,
                )
                sucesso = f"E-mail de teste enviado para {', '.join(destino)}."
            elif acao == "alertas":
                destinatarios = email_envio.separar_emails(request.form.get("alertas_destinatarios", ""))
                ativos = bool(request.form.get("alertas_ativos"))
                if ativos and not destinatarios:
                    raise ValueError("Informe quem recebe os alertas.")
                conf.update({
                    "alertas_ativos": "1" if ativos else "0",
                    "alertas_destinatarios": ", ".join(destinatarios),
                    "alertas_fontes": "1" if request.form.get("alertas_fontes") else "0",
                    "alertas_fila": "1" if request.form.get("alertas_fila") else "0",
                    "alertas_indices": "1" if request.form.get("alertas_indices") else "0",
                })
                configuracoes.salvar(conf)
                sucesso = "Alertas salvos."
            elif acao == "verificar":
                n = notificacoes.verificar_alertas(conf, forcar=True)
                sucesso = f"{n} alerta(s) novo(s) enviado(s)." if n else "Nenhum problema novo para avisar agora."
        except (ValueError, RuntimeError) as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro: {e}"

    envios = []
    try:
        envios = email_envio.ultimos_envios()
        for e in envios:
            e["enviado_local"] = _hora_local(e["enviado_em"])
    except Exception:
        pass
    return render_template(
        "email_admin.html", conf=conf, erro=erro, sucesso=sucesso, envios=envios,
        senha_definida=email_envio.senha_definida(), configurado=email_envio.configurado(conf),
    )


@app.route("/admin/relatorios")
def admin_relatorios():
    erro = None
    lista = []
    try:
        lista = relatorios.listar()
        for r in lista:
            r["proxima_local"] = _hora_local(r["proxima_em"])
            r["ultimo_local"] = _hora_local(r["ultimo_envio"])
    except Exception as e:
        erro = f"Banco de dados dos relatórios indisponível: {e}"
    configurado = False
    try:
        configurado = email_envio.configurado()
    except Exception:
        pass
    mensagens = {"criado": "Relatório agendado.", "salvo": "Relatório salvo.", "apagado": "Relatório apagado.", "enviado": "Relatório enviado agora."}
    return render_template(
        "relatorios_admin.html", relatorios=lista, erro=erro, configurado=configurado,
        sucesso=mensagens.get(request.args.get("ok")),
    )


def _formulario_relatorio(relatorio):
    erro = None
    if relatorio:
        dados = {k: relatorio[k] for k in ("nome", "periodo_dias", "frequencia", "hora", "destinatarios")}
        dados["ativo"] = bool(relatorio["ativo"])
    else:
        dados = {"nome": "Buscas da semana", "periodo_dias": 7, "frequencia": "semanal", "hora": 8, "destinatarios": "", "ativo": True}
    if request.method == "POST":
        dados = {
            "nome": request.form.get("nome", ""), "periodo_dias": request.form.get("periodo_dias", 7, type=int),
            "frequencia": request.form.get("frequencia", ""), "hora": request.form.get("hora", 8, type=int),
            "destinatarios": request.form.get("destinatarios", ""),
            "ativo": bool(request.form.get("ativo")) if relatorio else True,
        }
        try:
            campos = relatorios.validar(dados["nome"], dados["periodo_dias"], dados["frequencia"], dados["hora"], dados["destinatarios"])
            if relatorio:
                relatorios.atualizar(relatorio["id"], *campos, dados["ativo"])
                return redirect("/admin/relatorios?ok=salvo")
            relatorios.criar(*campos)
            return redirect("/admin/relatorios?ok=criado")
        except ValueError as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro ao salvar o relatório: {e}"
    return render_template(
        "relatorio_form.html", relatorio=relatorio, dados=dados, erro=erro,
        periodos=relatorios.PERIODOS, frequencias=relatorios.FREQUENCIAS, limite_nome=relatorios.LIMITE_NOME,
    )


def _relatorio_ou_erro(relatorio_id):
    try:
        relatorio = relatorios.obter(relatorio_id)
    except Exception as e:
        return None, _email_indisponivel(e)
    if not relatorio:
        abort(404, "Relatório não encontrado.")
    return relatorio, None


@app.route("/admin/relatorios/novo", methods=["GET", "POST"])
def admin_relatorio_novo():
    return _formulario_relatorio(None)


@app.route("/admin/relatorios/<int:relatorio_id>", methods=["GET", "POST"])
def admin_relatorio_editar(relatorio_id):
    relatorio, erro = _relatorio_ou_erro(relatorio_id)
    return erro or _formulario_relatorio(relatorio)


@app.route("/admin/relatorios/<int:relatorio_id>/enviar", methods=["POST"])
def admin_relatorio_enviar(relatorio_id):
    """Envia agora, sem mudar a agenda."""
    relatorio, erro = _relatorio_ou_erro(relatorio_id)
    if erro:
        return erro
    try:
        relatorios.enviar(relatorio)
    except Exception as e:
        return render_template("erro.html", titulo="Não consegui enviar o relatório", mensagem=str(e)), 502
    return redirect("/admin/relatorios?ok=enviado")


@app.route("/admin/relatorios/<int:relatorio_id>/apagar", methods=["POST"])
def admin_relatorio_apagar(relatorio_id):
    try:
        relatorios.apagar(relatorio_id)
    except Exception as e:
        return _email_indisponivel(e)
    return redirect("/admin/relatorios?ok=apagado")


def _buscas_indisponivel(e):
    return render_template("erro.html", titulo="Buscas salvas indisponíveis", mensagem=f"Banco de dados indisponível: {e}"), 503


@app.route("/buscas-salvas")
def buscas_salvas_pagina():
    erro = None
    lista = []
    try:
        lista = buscas_salvas.listar(acesso.usuario_atual())
        for b in lista:
            b["proxima_local"] = _hora_local(b["proxima_em"])
    except Exception as e:
        erro = f"Banco de dados das buscas salvas indisponível: {e}"
    mensagens = {"criada": "Busca salva.", "salva": "Busca salva.", "apagada": "Busca apagada."}
    return render_template(
        "buscas_salvas.html", buscas=lista, erro=erro, sucesso=mensagens.get(request.args.get("ok")),
        sem_login=acesso.usuario_atual() is None,
    )


@app.route("/buscas-salvas/salvar", methods=["POST"])
def buscas_salvas_salvar():
    """Vem do botão "Salvar busca" da tela de resultados."""
    projeto = request.form.get("projeto", "").strip() or None
    if projeto and not projetos.slug_valido(projeto):
        projeto = None
    try:
        buscas_salvas.criar(
            acesso.usuario_atual(), request.form.get("nome", ""), request.form.get("q", ""),
            request.form.get("parametros", ""), projeto,
            request.form.get("frequencia", "nenhuma"), request.form.get("alerta_email", ""),
        )
    except ValueError as e:
        return render_template("erro.html", titulo="Não consegui salvar a busca", mensagem=str(e)), 400
    except Exception as e:
        return _buscas_indisponivel(e)
    return redirect("/buscas-salvas?ok=criada")


@app.route("/buscas-salvas/<int:busca_id>", methods=["GET", "POST"])
def buscas_salvas_editar(busca_id):
    dono = acesso.usuario_atual()
    try:
        busca = buscas_salvas.obter(busca_id, dono)
    except Exception as e:
        return _buscas_indisponivel(e)
    if not busca:
        abort(404, "Busca salva não encontrada.")
    erro = None
    dados = {"nome": busca["nome"], "frequencia": busca["frequencia"], "alerta_email": busca["alerta_email"] or ""}
    if request.method == "POST":
        dados = {k: request.form.get(k, "") for k in dados}
        try:
            buscas_salvas.atualizar(busca_id, dono, dados["nome"], dados["frequencia"], dados["alerta_email"])
            return redirect("/buscas-salvas?ok=salva")
        except ValueError as e:
            erro = str(e)
        except Exception as e:
            erro = f"Erro ao salvar: {e}"
    return render_template(
        "busca_salva_form.html", busca=busca, dados=dados, erro=erro,
        frequencias=buscas_salvas.FREQUENCIAS, limite_nome=buscas_salvas.LIMITE_NOME,
    )


@app.route("/buscas-salvas/<int:busca_id>/apagar", methods=["POST"])
def buscas_salvas_apagar(busca_id):
    try:
        buscas_salvas.apagar(busca_id, acesso.usuario_atual())
    except Exception as e:
        return _buscas_indisponivel(e)
    return redirect("/buscas-salvas?ok=apagada")


def _pastas_indisponivel(e):
    return render_template("erro.html", titulo="Pastas indisponíveis", mensagem=f"Banco de dados indisponível: {e}"), 503


def _pasta_ou_erro(pasta_id, editar=False):
    """(pasta, resposta de erro). Pasta privada de outra pessoa responde 404,
    para não revelar que ela existe."""
    usuario = acesso.usuario_atual()
    try:
        pasta = pastas.obter(pasta_id)
    except Exception as e:
        return None, _pastas_indisponivel(e)
    if not pastas.pode_ver(pasta, usuario) and not acesso.eh_admin():
        abort(404, "Pasta não encontrada.")
    if not pasta:
        abort(404, "Pasta não encontrada.")
    if editar and not pastas.pode_editar(pasta, usuario, acesso.eh_admin()):
        abort(403)
    return pasta, None


@app.route("/pastas")
def pastas_pagina():
    usuario = acesso.usuario_atual()
    erro = None
    minhas, publicas = [], []
    try:
        minhas, publicas = pastas.listar(usuario)
    except Exception as e:
        erro = f"Banco de dados das pastas indisponível: {e}"
    mensagens = {"apagada": "Pasta apagada."}
    return render_template(
        "pastas.html", minhas=minhas, publicas=publicas, erro=erro, sem_login=usuario is None,
        sucesso=mensagens.get(request.args.get("ok")), limite_nome=pastas.LIMITE_NOME,
    )


@app.route("/pastas/nova", methods=["POST"])
def pastas_nova():
    try:
        pasta_id = pastas.criar(acesso.usuario_atual(), request.form.get("nome", ""), request.form.get("descricao", ""),
                                bool(request.form.get("publica")))
    except ValueError as e:
        return render_template("erro.html", titulo="Não consegui criar a pasta", mensagem=str(e)), 400
    except Exception as e:
        return _pastas_indisponivel(e)
    return redirect(f"/pastas/{pasta_id}?ok=criada")


@app.route("/pastas/<int:pasta_id>", methods=["GET", "POST"])
def pasta_ver(pasta_id):
    pasta, erro = _pasta_ou_erro(pasta_id)
    if erro:
        return erro
    usuario = acesso.usuario_atual()
    editavel = pastas.pode_editar(pasta, usuario, acesso.eh_admin())
    mensagem_erro = None
    if request.method == "POST":
        if not editavel:
            abort(403)
        try:
            pastas.atualizar(pasta_id, request.form.get("nome", ""), request.form.get("descricao", ""), bool(request.form.get("publica")))
            return redirect(f"/pastas/{pasta_id}?ok=salva")
        except ValueError as e:
            mensagem_erro = str(e)
        except Exception as e:
            return _pastas_indisponivel(e)
    try:
        lista = pastas.itens(pasta_id)
    except Exception as e:
        return _pastas_indisponivel(e)
    try:
        vivos = cliente.existentes([(i["indice"], i["doc_id"]) for i in lista])
    except Exception:
        vivos = None  # OpenSearch fora: não marca nada como sumido
    for item in lista:
        item["indice_nome"] = indices.nome_exibicao(item["indice"])
        item["tipo"] = extensao(item["nome"]).upper() or "Documento"
        item["sumiu"] = vivos is not None and (item["indice"], item["doc_id"]) not in vivos
        item["adicionado_local"] = _hora_local(item["adicionado_em"])
    mensagens = {"criada": "Pasta criada. Para guardar documentos, use \"Salvar na pasta\" na busca ou no preview.",
                 "salva": "Pasta salva.", "removido": "Documento tirado da pasta."}
    return render_template(
        "pasta.html", pasta=pasta, itens=lista, editavel=editavel, erro=mensagem_erro,
        sucesso=mensagens.get(request.args.get("ok")), limite_nome=pastas.LIMITE_NOME,
        limite_descricao=pastas.LIMITE_DESCRICAO, limite_nota=pastas.LIMITE_NOTA,
    )


@app.route("/pastas/<int:pasta_id>/apagar", methods=["POST"])
def pasta_apagar(pasta_id):
    _pasta, erro = _pasta_ou_erro(pasta_id, editar=True)
    if erro:
        return erro
    try:
        pastas.apagar(pasta_id)
    except Exception as e:
        return _pastas_indisponivel(e)
    return redirect("/pastas?ok=apagada")


@app.route("/pastas/<int:pasta_id>/remover", methods=["POST"])
def pasta_remover(pasta_id):
    _pasta, erro = _pasta_ou_erro(pasta_id, editar=True)
    if erro:
        return erro
    try:
        pastas.remover(pasta_id, request.form.get("indice", ""), request.form.get("doc", ""))
    except Exception as e:
        return _pastas_indisponivel(e)
    return redirect(f"/pastas/{pasta_id}?ok=removido")


@app.route("/pastas/<int:pasta_id>/nota", methods=["POST"])
def pasta_nota(pasta_id):
    _pasta, erro = _pasta_ou_erro(pasta_id, editar=True)
    if erro:
        return erro
    try:
        pastas.anotar(pasta_id, request.form.get("indice", ""), request.form.get("doc", ""), request.form.get("nota", ""))
    except ValueError as e:
        return render_template("erro.html", titulo="Nota longa demais", mensagem=str(e)), 400
    except Exception as e:
        return _pastas_indisponivel(e)
    return redirect(f"/pastas/{pasta_id}")


@app.route("/pastas/do-documento")
def pastas_do_documento():
    """JSON para o popup "Salvar na pasta": as pastas do usuário e se cada
    uma já tem o documento."""
    try:
        lista = pastas.editaveis(acesso.usuario_atual(), request.args.get("indice"), request.args.get("doc"))
    except Exception as e:
        return jsonify({"erro": f"Pastas indisponíveis: {e}"}), 503
    resposta = jsonify({"pastas": [{"id": p["id"], "nome": p["nome"], "publica": bool(p["publica"]), "contem": bool(p["contem"])} for p in lista]})
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.route("/pastas/documento", methods=["POST"])
def pastas_documento():
    """JSON {indice, doc, pasta_id | nova_pasta, guardar}: guarda ou tira o
    documento de uma pasta do usuário. Só aceita JSON (um formulário de outro
    site não consegue mandar esse tipo sem permissão do navegador)."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Pedido inválido."}), 400
    usuario = acesso.usuario_atual()
    doc_id = str(dados.get("doc") or "")
    indice, doc = _documento(str(dados.get("indice") or ""), doc_id)
    if doc is None:
        return jsonify({"erro": "Documento não encontrado no índice."}), 404
    try:
        if dados.get("nova_pasta"):
            pasta_id = pastas.criar(usuario, str(dados["nova_pasta"]), "", False)
        else:
            pasta_id = int(dados.get("pasta_id") or 0)
        pasta = pastas.obter(pasta_id)
        if not pastas.pode_editar(pasta, usuario, acesso.eh_admin()):
            return jsonify({"erro": "Pasta não encontrada."}), 404
        fonte = doc["_source"]
        if dados.get("guardar", True):
            pastas.adicionar(pasta_id, indice, doc_id, fonte.get("nome"), fonte.get("titulo"), usuario)
            mensagem = f"Guardado em \"{pasta['nome']}\"."
        else:
            pastas.remover(pasta_id, indice, doc_id)
            mensagem = f"Tirado de \"{pasta['nome']}\"."
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
    except Exception as e:
        return jsonify({"erro": f"Pastas indisponíveis: {e}"}), 503
    return jsonify({"ok": True, "mensagem": mensagem, "pasta_id": pasta_id})


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
    crawler.iniciar()
    notificacoes.iniciar()


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=MODO_DEBUG, port=5000)
