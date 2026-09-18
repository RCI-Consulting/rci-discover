import hashlib


def gerar_doc_id(caminho: str) -> str:
    """ID determinístico a partir do caminho do arquivo — reindexar o
    mesmo arquivo sobrescreve o doc existente em vez de duplicar."""
    return hashlib.sha256(caminho.encode("utf-8")).hexdigest()
