import hashlib
from pathlib import Path

# Extensões que o navegador consegue exibir diretamente (sem plugin externo)
EXTENSOES_EMBUTIVEIS = {".pdf", ".jpg", ".jpeg", ".png"}


def gerar_doc_id(caminho: str) -> str:
    """ID determinístico a partir do caminho do arquivo — reindexar o
    mesmo arquivo sobrescreve o doc existente em vez de duplicar."""
    return hashlib.sha256(caminho.encode("utf-8")).hexdigest()


def extensao(nome: str) -> str:
    """Extensão do arquivo em minúsculas, sem o ponto (ex: 'pdf')."""
    return Path(nome).suffix.lower().lstrip(".")


def pode_embutir(nome: str) -> bool:
    """Se o navegador consegue exibir esse tipo de arquivo direto num iframe/img."""
    return Path(nome).suffix.lower() in EXTENSOES_EMBUTIVEIS
