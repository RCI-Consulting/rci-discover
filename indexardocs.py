"""
Varre a pasta configurada (PASTA_ORIGEM em config.py) atrás de PDFs,
extrai o conteúdo via pipeline de ingest do OpenSearch (attachment/Tika)
e indexa no índice `documentos`, usando o OpenSearchClient existente.

Uso:
    python indexar_documentos.py
"""

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path

import config
from opensearch_client import OpenSearchClient
from utils import gerar_doc_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def arquivo_ja_indexado(cliente: OpenSearchClient, doc_id: str, mtime: float) -> bool:
    """Verifica se o arquivo já foi indexado e não foi modificado
    desde a última indexação (compara file_mtime salvo vs atual)."""
    resultado = cliente.get(config.INDEX_NAME, doc_id)
    if resultado is None:
        return False
    mtime_salvo = resultado["_source"].get("file_mtime")
    return mtime_salvo is not None and abs(mtime_salvo - mtime) < 1


def listar_pdfs(pasta: str):
    """Lista recursivamente todos os PDFs na pasta de origem."""
    caminho_base = Path(pasta)
    if not caminho_base.exists():
        raise FileNotFoundError(
            f"Não foi possível acessar a pasta: {pasta}. "
            f"Verifique permissões e conectividade de rede."
        )
    for arquivo in caminho_base.rglob("*"):
        if arquivo.is_file() and arquivo.suffix.lower() in config.EXTENSOES_VALIDAS:
            yield arquivo


def montar_documento(arquivo: Path) -> dict:
    """Lê o PDF e monta o corpo do documento para indexação, usando os
    nomes de campo do mapping (nome, caminho, data)."""
    stat = arquivo.stat()
    conteudo_binario = arquivo.read_bytes()
    conteudo_base64 = base64.b64encode(conteudo_binario).decode("utf-8")

    return {
        "nome": arquivo.name,
        "caminho": str(arquivo.resolve()),
        "file_size": stat.st_size,
        "file_mtime": stat.st_mtime,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "data": conteudo_base64,  # consumido e removido pela pipeline
    }


def gerar_acoes_bulk(cliente: OpenSearchClient, pasta: str):
    """Generator que produz as ações de bulk indexing, pulando
    arquivos já indexados e sem alteração."""
    total = 0
    pulados = 0
    erros = 0

    for arquivo in listar_pdfs(pasta):
        total += 1
        try:
            stat = arquivo.stat()
            doc_id = gerar_doc_id(str(arquivo))

            if arquivo_ja_indexado(cliente, doc_id, stat.st_mtime):
                logger.info(f"Já indexado, pulando: {arquivo.name}")
                pulados += 1
                continue

            documento = montar_documento(arquivo)

            yield {
                "_index": config.INDEX_NAME,
                "_id": doc_id,
                "_source": documento,
                "pipeline": config.PIPELINE_NAME,
            }
            logger.info(f"Preparado para indexação: {arquivo.name}")

        except Exception as e:
            erros += 1
            logger.error(f"Erro ao processar {arquivo}: {e}")

    logger.info(
        f"Resumo da varredura: {total} encontrados, "
        f"{pulados} pulados (já indexados), {erros} erros de leitura"
    )


def main():
    logger.info(f"Conectando ao OpenSearch em {config.OPENSEARCH_HOST}:{config.OPENSEARCH_PORT}...")
    cliente = OpenSearchClient()

    if not cliente.index_exists(config.INDEX_NAME):
        raise RuntimeError(
            f"Índice '{config.INDEX_NAME}' não existe. Crie o índice com o "
            f"mapping antes de rodar este script."
        )

    logger.info(f"Varrendo pasta: {config.PASTA_ORIGEM}")

    sucesso, falhas = cliente.bulk(gerar_acoes_bulk(cliente, config.PASTA_ORIGEM))

    logger.info(f"Indexação concluída. Sucesso: {sucesso}")
    if falhas:
        logger.warning(f"Falhas na indexação: {len(falhas)}")
        for falha in falhas[:10]:
            logger.warning(falha)


if __name__ == "__main__":
    main()