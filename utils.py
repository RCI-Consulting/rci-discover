import hashlib
import html
import re
from pathlib import Path

import markdown as _markdown
import nh3

# Extensões que o navegador consegue exibir diretamente (sem plugin externo)
EXTENSOES_EMBUTIVEIS = {".pdf", ".jpg", ".jpeg", ".png"}


def gerar_doc_id(caminho: str) -> str:
    """ID determinístico a partir do caminho do arquivo — reindexar o
    mesmo arquivo sobrescreve o doc existente em vez de duplicar."""
    return hashlib.sha256(caminho.encode("utf-8")).hexdigest()


def extensao(nome: str) -> str:
    """Extensão do arquivo em minúsculas, sem o ponto (ex: 'pdf')."""
    return Path(nome).suffix.lower().lstrip(".")


_TITULO_MARKDOWN = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def extrair_titulo(conteudo: str, nome: str) -> str:
    """Título do documento para a busca e o card: o primeiro título (#, ##
    ou ###) do Markdown do Docling, nos primeiros 3000 caracteres; sem ele,
    o nome do arquivo sem extensão e com _ e - trocados por espaço (o
    tokenizador do OpenSearch não separa "contrato_fornecedor" em duas palavras)."""
    for m in _TITULO_MARKDOWN.finditer((conteudo or "")[:3000]):
        titulo = re.sub(r"[*_`]+", "", m.group(1)).strip()
        if 3 <= len(titulo) <= 200 and titulo != "[imagem]":
            return titulo
    base = Path(nome or "").stem
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", base)).strip() or (nome or "")


def pode_embutir(nome: str) -> bool:
    """Se o navegador consegue exibir esse tipo de arquivo direto num iframe/img."""
    return Path(nome).suffix.lower() in EXTENSOES_EMBUTIVEIS


# O que o HTML do preview pode conter depois de convertido do Markdown. Sem
# <img>: uma imagem externa (![](https://...)) faria o navegador de quem abre o
# documento avisar um site de fora (IP, horário). Links só http(s) e mailto:
# `[clique](javascript:...)` vindo de um documento vira texto sem link.
_TAGS_PREVIEW = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i",
    "code", "pre", "blockquote", "ul", "ol", "li", "a",
    "table", "thead", "tbody", "tr", "th", "td",
}
_ATRIBUTOS_PREVIEW = {"a": {"href", "title"}, "ol": {"start"}, "th": {"align"}, "td": {"align"}}
_ESQUEMAS_PREVIEW = {"http", "https", "mailto"}


def renderizar_markdown(texto: str) -> str:
    """Converte o Markdown extraído pelo Docling em HTML para exibir no
    preview. Duas camadas contra XSS armazenado (o documento pode vir de
    qualquer pasta de rede):
    1. escapa o texto antes de converter — tag HTML literal no documento vira
       texto inerte;
    2. passa o HTML gerado por uma lista de permissões (nh3), porque o próprio
       Markdown cria links e imagens com qualquer endereço."""
    if not texto:
        return ""
    gerado = _markdown.markdown(
        html.escape(texto),
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return nh3.clean(
        gerado,
        tags=_TAGS_PREVIEW,
        attributes=_ATRIBUTOS_PREVIEW,
        url_schemes=_ESQUEMAS_PREVIEW,
        link_rel="noopener noreferrer nofollow",
    )


def escapar_destaque(texto: str) -> str:
    """Escapa todo o HTML de um fragmento do OpenSearch, mantendo só as tags
    <em>/</em> que ele usa para destacar os termos achados."""
    return html.escape(texto).replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")


# Sequência longa sem espaço (base64 de imagens antigas, URLs enormes...): não
# quebra linha sozinha, não é legível e estoura o card da busca.
_SEQUENCIA_LONGA = re.compile(r"\S{40,}")


def montar_trecho(destaque, conteudo: str, limite: int = 220) -> str:
    """HTML seguro para o resumo de um resultado de busca.

    `destaque` é o fragmento do OpenSearch (com <em> nos termos achados) e
    `conteudo` o texto completo, usado quando não há destaque. Tudo é escapado
    — só as tags <em>/</em> do destaque sobrevivem — e sequências enormes sem
    espaço são encurtadas, então o resultado pode ir direto num `|safe`.
    """
    if destaque:
        texto = escapar_destaque(destaque)
    else:
        conteudo = conteudo or ""
        texto = html.escape(conteudo[:limite]) + ("..." if len(conteudo) > limite else "")
    return _SEQUENCIA_LONGA.sub(lambda m: m.group(0)[:20] + "…", texto)

