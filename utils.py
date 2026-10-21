import hashlib
import html
import re
from pathlib import Path

import markdown as _markdown

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


def renderizar_markdown(texto: str) -> str:
    """Converte o Markdown extraído pelo Docling em HTML para exibir no
    preview. Escapa o texto antes de converter — qualquer tag HTML que
    viesse literalmente dentro do documento original vira texto inerte em
    vez de ser interpretada pelo navegador (evita XSS armazenado)."""
    if not texto:
        return ""
    return _markdown.markdown(
        html.escape(texto),
        extensions=["tables", "fenced_code", "sane_lists"],
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

