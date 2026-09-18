# python-opensearch

## RCI-SEARCH ##

# docker-desktop Windows
- uso de um ambiente de testes OpenSearch
- https://github.com/opensearch-project/opensearch-build/tree/main/docker/release/dockercomposefiles baixar o ultimo docker-compose-default.x.yml e renomear para docker-compose.yml
- rodar no cluster docker-compose up - vai iniciar dois nodes opensearch e o opensearch dashboards.
- docker ps -> verifica se os nodes estão no ar.
- localhost:5601 para abrir o OpenSearch Dashboards.
- o arquivo .env deve ter a variável OPENSEARCH_INITIAL_ADMIN_PASSWORD e ela tem os requerimentos: mínimo 8 caracteres, maiusculas, minusculas, numeros e caracter especial. 

# ingest attachment plugin :
- criação de um nova imagem do Docker: Dockerfile (com D maiúsculo e sem extensão)
    FROM opensearchproject/opensearch:3.8.0
    RUN /usr/share/opensearch/bin/opensearch-plugin install --batch ingest-attachment
- alterar o docker-compose.yml para usar a nova imagem (nos dois nodes do exemplo):
  opensearch-node1:
    image: opensearch-custom:3.8.0
  opensearch-node2:
    image: opensearch-custom:3.8.0
- para criar a imagem: 
    * docker compose down (desliga os containers)
    * docker build -t opensearch-custom:3.8.0 . <= atenção ao ponto no final
- subir novamente os containers: "docker compose up -d"
- para verificar os nodes, no Dev Tools do Dashboards: **GET _cat/plugins?v**
    * opensearch-node1 ingest-attachment                    3.8.0
    * opensearch-node2 ingest-attachment                    3.8.0

# criação index :
- Endpoints
    PUT /{index}
    [Index API](https://docs.opensearch.org/latest/api-reference/index-apis/create-index/)


# criação da pipeline :
PUT _ingest/pipeline/documentos_pipeline
{
  "description": "Extracao de documentos com ingest attachment",
  "processors": [
    {
      "attachment": {
        "field": "data"
      }
    },
{
  "remove": {
    "field": "data" => remove do registro o documento em base64 que é convertido pelo plugin. Assim deixa a resposta mais limpa, apenas com a transcrição do documento.
  }
}
  ]
}

# ambiente .venv : 
- venv\Scripts\activate
# uvicorn - Fast API
- uvicorn api:app --reload
# config.py :
- carrega as variáveis do `.env` (PASTA, HOST, USERNAME, PASSWORD).
- pasta de origem dos documentos (para prova de conceito -> precisa ser aprimorado).
- **11/08/2026** lê apenas .pdf.
# opensearch_client.py :
- cria a classe para conexão com o OpenSearch -> instalação do opensearch via docker desktop Windows.
- carrega as variáveis de ambiente definidas no arquivo .env.
# api.py : 
- teste que o GPT fez para testar a fastAPI com uvicorn para rodar no backend e retornar o arquivo origial em download.
# app.py : 
- inicia o cliente e envia os parâmetros (index e query).
# ingestpdf.py :
- lê um .pdf e converte em base64 para ingestão no node.
- teste bem inicial, só roda 1 arquivo e esta definido no código.
# indexardocs.py :
- varre a pasta configurada (no config.py), extrai o conteúdo do pdf via pipeline de `_ingest` do OpenSearch e indexa no índice `documentos`. Usa a classe existente `OpenSearchClient()` 

17/09/2026

# Ambiente RCI-Search
- Projeto foi movido para o Dokploy - cada 'serviço' possuí seu próprio container.
  * opensearch - 2 nodes + dashboards <importante para usar o Dev Tools>
  * docling-serve - docling faz a ingestão de documentos (OCR será importante na versão final)
  * frontend - repositório python. 
    - ![alt text](image.png)
    - `web_app.py` : páginas de busca e upload dos documentos. 
                     é possível fazer download do arquivo original
                     -> necessário ajustar paginação e o texto que está saíndo do card.
    - `docling_service.py` : funções de extração e processamento dos documentos.
    - `opensearch_client.py`, `config.py` : não sofreram alterações.
    - `indexardocs.py` : DESATIVADO <usava o plugin ingest-attachment + ApacheTika>
    - `requirements.txt`, `Dockerfile` : requerimentos do python e comando para rodar a página web_app.

- Ambiente está estável, embora Docling consuma muitos recursos.
