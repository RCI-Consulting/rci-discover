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

18/09/2026

# Reorganização do frontend (web_app.py)
- Repositório migrado para o GitHub: [liperrf/rci-search](https://github.com/liperrf/rci-search).
- Objetivo: remover acoplamentos com código desativado, centralizar configuração e separar o HTML que estava embutido em `web_app.py`.

## Arquivos novos
- `utils.py` : módulo compartilhado com funções utilitárias.
  - `gerar_doc_id(caminho)` : gera um ID determinístico (SHA-256) a partir do caminho do arquivo, usado para indexar de forma idempotente. Antes vivia dentro de `indexardocs.py` (desativado) e era importado de lá pelo `web_app.py`; agora os dois módulos importam de `utils.py`.
- `templates/base.html` : layout base (Jinja) com a fonte e a folha de estilos compartilhadas por todas as páginas.
- `templates/index.html` : página de busca — estende `base.html`, extraída de `TEMPLATE` (antes uma string Python dentro de `web_app.py`).
- `templates/ingest.html` : página de upload/ingestão — estende `base.html`, extraída de `INGEST_TEMPLATE`.
- `static/style.css` : estilos da aplicação. Visual novo inspirado na página de busca do Google (logo e barra de pesquisa centralizadas na home, migrando para o topo quando há resultados), usando a paleta de cores da RCI (azul `#004FFE`, navy `#03143B`) e a fonte de títulos do site institucional (Atkinson Hyperlegible).

## Arquivos alterados
- `web_app.py` : não guarda mais HTML — as rotas (`/`, `/ingest`, `/download/<id>`) agora chamam `render_template(...)` apontando para `templates/`. Passa a importar `gerar_doc_id` de `utils.py` em vez de `indexardocs.py`.
- `config.py` : nova variável `DOCLING_BASE_URL` (lida do `.env`, com o mesmo valor padrão que antes ficava hardcoded em `docling_service.py`).
- `docling_service.py` : `processar_e_indexar_pdf` trocou uma assinatura genérica (`*args, **kwargs`) por parâmetros explícitos (`caminho_arquivo, doc_id=None, nome_indice=None`); passou a usar `config.DOCLING_BASE_URL` diretamente.
- `indexardocs.py` (DESATIVADO) : `gerar_doc_id` foi removida de dentro do arquivo — agora importa de `utils.py`.
- `requirements.txt` : removidas `fastapi` e `uvicorn` (não usadas, resquício de uma versão anterior do projeto); adicionada `requests` (usada por `docling_service.py`, mas não estava listada).

- `opensearch_client.py`, `Dockerfile` : sem alterações.

30/09/2026

# Preview inline do documento
- Nos resultados de busca, cada documento agora tem um link **"Visualizar"** além do "Baixar original", que abre uma página de preview em vez de forçar o download.
- PDFs e imagens (`.jpg`, `.jpeg`, `.png`) são exibidos direto no navegador (embutidos num `<iframe>`); os demais formatos (`.docx`, `.doc`, `.odt`, `.rtf`, `.txt`) mostram o texto já extraído pelo Docling durante a indexação, já que o navegador não consegue renderizá-los nativamente.
- Corrigido também um bug existente: o campo "tipo" dos resultados estava fixo em "PDF Document" para qualquer arquivo — agora é derivado da extensão real (`PDF`, `DOCX`, `TXT`, etc.).

## Arquivos novos
- `templates/preview.html` : página de preview — título do documento, botão de download, `<iframe>` (quando aplicável) e o texto extraído num bloco colapsável (`<details>`).

## Arquivos alterados
- `web_app.py` : novas rotas `/preview/<id>` (renderiza `preview.html`) e `/arquivo/<id>` (serve o arquivo original com `Content-Disposition: inline`, usado como origem do `<iframe>`). O "tipo" do resultado da busca passou a usar `utils.extensao(...)` em vez do valor fixo.
- `utils.py` : novas funções `extensao(nome)` e `pode_embutir(nome)` (extensões que o navegador exibe nativamente: pdf/jpg/jpeg/png).
- `templates/index.html` : card de resultado ganhou o link "Visualizar"; o título do card também passou a apontar para o preview em vez do download direto.
- `static/style.css` : estilos da página de preview e do novo link "Visualizar".
