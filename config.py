import os
 
from dotenv import load_dotenv
 
load_dotenv()
 
# Conexão OpenSearch
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "opensearch")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_INITIAL_ADMIN_PASSWORD")
 
# Índice e pipeline
INDEX_NAME = "documentos"
PIPELINE_NAME = "documentos_pipeline"
 
# Pasta de origem dos PDFs (caminho UNC)
PASTA_ORIGEM = "./pdf"

# URL base do serviço Docling (extração de conteúdo dos documentos)
DOCLING_BASE_URL = os.getenv("DOCLING_BASE_URL", "http://docling-serve:5001")
 
# Extensões de arquivo aceitas na varredura
EXTENSOES_VALIDAS = {".pdf", ".docx", ".doc", ".txt", ".odt", ".rtf", ".jpg", ".jpeg", ".png"}
 
