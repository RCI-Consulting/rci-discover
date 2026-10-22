import mimetypes
import os
import re
import time
from datetime import datetime, timezone

import requests
import config
import extracao

from opensearch_client import OpenSearchClient
from utils import extensao, extrair_titulo

BASE_DOCLING_URL = config.DOCLING_BASE_URL.rstrip("/")

# O Docling embute imagens como `![Image](data:image/png;base64,...)` direto
# no markdown exportado. Isso não é pesquisável e infla o índice em dezenas
# de KB por imagem, então removemos antes de indexar/exibir.
_PADRAO_IMAGEM_BASE64 = re.compile(
    r"!\[[^\]]*\]\(data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+\)",
    re.DOTALL,
)


def _remover_imagens_base64(texto: str) -> str:
    return _PADRAO_IMAGEM_BASE64.sub("[imagem]", texto)


def extrair_texto_docling(caminho_arquivo: str) -> str:
    """Envia o arquivo para o Docling e retorna o texto/markdown extraído."""
    nome_arquivo = os.path.basename(caminho_arquivo)

    content_type, _ = mimetypes.guess_type(caminho_arquivo)
    if not content_type:
        content_type = "application/octet-stream"

    url = f"{BASE_DOCLING_URL}/v1alpha/convert/file"
    # OCR: lê o texto de imagens e de PDFs escaneados. Só entra nas partes da
    # página sem texto digital (force_ocr=false), então PDFs normais quase não
    # ficam mais lentos. Idiomas do EasyOCR em DOCLING_OCR_IDIOMAS (ex.: "pt,en").
    params = {
        "do_ocr": "true" if config.DOCLING_OCR else "false",
        "force_ocr": "true" if config.DOCLING_OCR_FORCAR else "false",
        "ocr_engine": "easyocr",
        "ocr_lang": config.DOCLING_OCR_IDIOMAS,
    }

    print(f"[Docling] Enviando '{nome_arquivo}' para {url} (timeout {config.DOCLING_TIMEOUT}s)...")
    inicio = time.monotonic()

    with open(caminho_arquivo, "rb") as f:
        files = {"files": (nome_arquivo, f, content_type)}
        try:
            res = requests.post(url, files=files, params=params, timeout=(10, config.DOCLING_TIMEOUT))
        except Exception as e:
            raise RuntimeError(f"Erro de conexão com o Docling: {str(e)}")

    if res.status_code != 200:
        raise RuntimeError(f"Docling retornou Status {res.status_code}: {res.text}")

    dados = res.json()
    item = dados[0] if isinstance(dados, list) and len(dados) > 0 else dados

    texto = ""
    doc = {}

    if isinstance(item, dict):
        doc = item.get("document", item)

        if isinstance(doc, dict):
            # Nome correto do campo na resposta do docling-serve: md_content
            # (confirmado pelo SDK oficial: result.document.md_content)
            texto = (
                doc.get("md_content")
                or doc.get("text_content")
                or doc.get("markdown")
                or doc.get("markdown_content")
                or doc.get("export_to_markdown")
                or doc.get("text")
                or item.get("md_content")
                or item.get("text_content")
                or ""
            )

            # Fallback: blocos de texto (formato "texts")
            if not texto and "texts" in doc:
                linhas = []
                for t in doc.get("texts", []):
                    if isinstance(t, dict) and t.get("text"):
                        linhas.append(t["text"])
                    elif isinstance(t, str):
                        linhas.append(t)
                texto = "\n".join(linhas)

            # Fallback: páginas e elementos
            if not texto and "pages" in doc:
                paginas = []
                for p in doc.get("pages", []):
                    if isinstance(p, dict):
                        for el in p.get("elements", []):
                            if isinstance(el, dict) and el.get("text"):
                                paginas.append(str(el["text"]))
                texto = "\n".join(paginas)

    texto = _remover_imagens_base64(str(texto)).strip()

    print(f"=== TEXTO EXTRAÍDO ({nome_arquivo}) ===")
    print(f"Tamanho: {len(texto)} caracteres | conversão em {time.monotonic() - inicio:.1f}s")
    if not texto:
        # Ajuda a diagnosticar rápido se a API mudar de formato de novo:
        print(f"[AVISO: TEXTO VAZIO] Chaves recebidas em 'document': {list(doc.keys()) if isinstance(doc, dict) else 'N/A'}")
    else:
        print(texto[:300])
    print("========================================")

    return texto


def processar_e_indexar_pdf(caminho_arquivo, doc_id=None, nome_indice=None):
    if not caminho_arquivo:
        raise ValueError("Caminho do arquivo não foi fornecido.")

    nome_indice = nome_indice or config.INDEX_NAME
    # TXT, DOCX e ODT saem direto (milissegundos); o resto, ou se falhar, vai para o Docling
    texto_extraido = extracao.extrair(caminho_arquivo) if config.EXTRACAO_RAPIDA else None
    if texto_extraido is None:
        texto_extraido = extrair_texto_docling(caminho_arquivo)
    else:
        print(f"[Extração rápida] {os.path.basename(caminho_arquivo)}: {len(texto_extraido)} caracteres, sem Docling")
    cliente = OpenSearchClient()

    # Garante que o conteúdo não vá como Base64 nem ultrapasse o limite
    nome = os.path.basename(caminho_arquivo)
    documento = {
        "nome": nome,
        "titulo": extrair_titulo(texto_extraido, nome),
        "caminho": str(caminho_arquivo),
        "conteudo": texto_extraido,
        "tipo": extensao(nome) or "outro",
        "indexado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    cliente.garantir_mapeamento(nome_indice)

    return cliente.index(
        index=nome_indice,
        body=documento,
        doc_id=doc_id,
    )


# Alias para compatibilidade
processar_e_indexar_arquivo = processar_e_indexar_pdf
