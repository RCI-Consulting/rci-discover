"""Extração rápida de texto, sem o Docling, para formatos que já são texto
por dentro: TXT, DOCX e ODT. O Docling em CPU leva minutos por documento;
estes saem em milissegundos, o que importa nas pastas grandes do crawler.

O resultado é Markdown simples (títulos com `#`, tabelas com `|`), no mesmo
formato que o Docling devolve, então preview, título e busca funcionam igual.
Se algo der errado (arquivo corrompido, formato inesperado), `extrair`
devolve None e o documento vai para o Docling como antes.
"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET

EXTENSOES = {".txt", ".docx", ".odt"}
LIMITE_XML = 100 * 1024 * 1024   # protege contra "zip bomb": XML descompactado acima disso vai para o Docling

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _ler_xml(caminho, interno):
    with zipfile.ZipFile(caminho) as z:
        if z.getinfo(interno).file_size > LIMITE_XML:
            raise ValueError("XML grande demais")
        dados = z.read(interno)
    if b"<!DOCTYPE" in dados[:2000] or b"<!ENTITY" in dados:
        raise ValueError("XML com DTD")  # documentos do Office nunca têm; recusa entidades maliciosas
    return ET.fromstring(dados)


def _linhas_tabela(linhas):
    if not linhas:
        return []
    largura = max(len(l) for l in linhas)
    linhas = [l + [""] * (largura - len(l)) for l in linhas]
    saida = ["| " + " | ".join(linhas[0]) + " |", "|" + " --- |" * largura]
    saida += ["| " + " | ".join(l) + " |" for l in linhas[1:]]
    return saida


# ---- TXT --------------------------------------------------------------------

def _txt(caminho):
    with open(caminho, "rb") as f:
        dados = f.read()
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            return dados.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return dados.decode("latin-1")


# ---- DOCX -------------------------------------------------------------------

def _texto_docx(elemento):
    partes = []
    for no in elemento.iter():
        if no.tag == _W + "t" and no.text:
            partes.append(no.text)
        elif no.tag == _W + "tab":
            partes.append("\t")
        elif no.tag in (_W + "br", _W + "cr"):
            partes.append("\n")
    return "".join(partes).strip()


def _nivel_titulo_docx(paragrafo):
    """1–6 se o estilo for de título ('Heading1', 'Ttulo1', 'Title'...), senão 0."""
    estilo = paragrafo.find(f"{_W}pPr/{_W}pStyle")
    nome = (estilo.get(_W + "val") if estilo is not None else "") or ""
    if nome.lower() in ("title", "titulo", "ttulo"):
        return 1
    m = re.match(r"(?i)(heading|t[ií]?tulo)\s*([1-6])$", nome)
    return int(m.group(2)) if m else 0


def _docx(caminho):
    corpo = _ler_xml(caminho, "word/document.xml").find(_W + "body")
    linhas = []
    for bloco in corpo:
        if bloco.tag == _W + "p":
            texto = _texto_docx(bloco)
            if texto:
                nivel = _nivel_titulo_docx(bloco)
                linhas.append(("#" * nivel + " " if nivel else "") + texto)
                linhas.append("")
        elif bloco.tag == _W + "tbl":
            tabela = [[_texto_docx(c).replace("\n", " ").replace("|", "/") for c in tr.findall(_W + "tc")]
                      for tr in bloco.findall(_W + "tr")]
            linhas += _linhas_tabela(tabela) + [""]
    return "\n".join(linhas)


# ---- ODT --------------------------------------------------------------------

def _texto_odt(elemento):
    partes = [elemento.text or ""]
    for filho in elemento:
        if filho.tag == _TEXT + "s":
            partes.append(" " * int(filho.get(_TEXT + "c", "1")))
        elif filho.tag == _TEXT + "tab":
            partes.append("\t")
        elif filho.tag == _TEXT + "line-break":
            partes.append("\n")
        else:
            partes.append(_texto_odt(filho))
        partes.append(filho.tail or "")
    return "".join(partes)


def _odt_blocos(pai, linhas):
    for bloco in pai:
        if bloco.tag == _TEXT + "h":
            nivel = min(max(int(bloco.get(_TEXT + "outline-level", "1")), 1), 6)
            texto = _texto_odt(bloco).strip()
            if texto:
                linhas += ["#" * nivel + " " + texto, ""]
        elif bloco.tag == _TEXT + "p":
            texto = _texto_odt(bloco).strip()
            if texto:
                linhas += [texto, ""]
        elif bloco.tag == _TABLE + "table":
            tabela = [[_texto_odt(c).strip().replace("\n", " ").replace("|", "/") for c in tr.findall(_TABLE + "table-cell")]
                      for tr in bloco.iter(_TABLE + "table-row")]
            linhas += _linhas_tabela(tabela) + [""]
        elif bloco.tag in (_TEXT + "list", _TEXT + "list-item", _TEXT + "section"):
            _odt_blocos(bloco, linhas)


def _odt(caminho):
    texto = _ler_xml(caminho, "content.xml").find(f"{_OFFICE}body/{_OFFICE}text")
    linhas = []
    _odt_blocos(texto, linhas)
    return "\n".join(linhas)


_EXTRATORES = {".txt": _txt, ".docx": _docx, ".odt": _odt}


def extrair(caminho):
    """Texto/Markdown do arquivo, ou None se o formato não tem extração
    rápida ou se ela falhou (aí quem chama usa o Docling)."""
    extrator = _EXTRATORES.get(os.path.splitext(caminho)[1].lower())
    if not extrator:
        return None
    try:
        texto = extrator(caminho)
    except Exception as e:
        print(f"[Extração rápida] Falhou em {os.path.basename(caminho)} ({e}); vai para o Docling.")
        return None
    return re.sub(r"\n{3,}", "\n\n", texto).strip()
