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

30/09/2026 (2)

# Páginas de erro amigáveis
- Motivo: um documento indexado antes de um redeploy apontava pra um arquivo que não existia mais (`PASTA_ORIGEM` não tem volume persistente no Dokploy ainda — ver seção de infraestrutura a resolver), e o erro aparecia como a página padrão crua do Flask/Werkzeug, sem o visual do site.
- `/download/<id>` e `/preview/<id>` : erros 404 (documento não encontrado no índice, ou arquivo ausente no servidor) agora renderizam uma página de erro com o visual do site, com um link "Voltar à busca".
- `/arquivo/<id>` : como essa rota só é usada como origem de um `<iframe>` dentro do preview, o erro é uma mensagem mínima e centralizada (sem o topbar do site), pra não duplicar a navegação dentro do iframe.

## Arquivos novos
- `templates/erro.html` : página de erro completa (com topbar), usada pelo `@app.errorhandler(404)`.
- `templates/preview_erro.html` : mensagem de erro mínima, usada só dentro do `<iframe>` da rota `/arquivo/<id>`.

## Arquivos alterados
- `web_app.py` : adiciona `@app.errorhandler(404)` global; `/arquivo/<id>` passou a renderizar `preview_erro.html` em vez de `abort(404, ...)`.
- `static/style.css` : estilos das duas páginas de erro.

01/10/2026

# Remove imagens em base64 do texto extraído pelo Docling
- Motivo: o Docling embute imagens (ex: logos, headers dos documentos) direto no markdown exportado como `![Image](data:image/png;base64,...)`. Cada imagem gera dezenas de KB de texto em base64, que não é pesquisável e aparecia como lixo visual no "Texto extraído" do preview.
- `docling_service.py` : `extrair_texto_docling` agora remove esses blocos de imagem em base64 antes de indexar, substituindo por `[imagem]`.
- **Pendência**: documentos já indexados antes dessa mudança continuam com o base64 salvo no OpenSearch — é preciso reindexar (reenviar) esses documentos específicos pra limpar o conteúdo já salvo. A mudança só afeta uploads novos a partir de agora.

# Renderiza o Markdown extraído como HTML no preview
- Motivo: o texto extraído pelo Docling já é Markdown (títulos, tabelas, negrito), mas aparecia como texto puro (com `#`, `##`, `|` literais) num bloco `<pre>` — isso afeta sobretudo documentos sem preview visual (`.docx`, `.doc`, `.odt`, `.rtf`, `.txt`), onde o texto extraído é a única coisa exibida.
- `utils.py` : nova função `renderizar_markdown(texto)` — escapa o HTML do texto original antes de converter pra Markdown (`html.escape` + biblioteca `markdown`), evitando que uma tag HTML literal dentro do documento original seja executada pelo navegador (XSS armazenado). Testado com um trecho contendo `<script>` — o texto aparece escapado, não executa.
- `web_app.py` : rota `/preview/<id>` passa a renderizar o Markdown (`conteudo_html`) em vez do texto cru.
- `templates/preview.html`, `static/style.css` : bloco de texto extraído agora é HTML formatado (`.markdown-body`) em vez de `<pre>` — títulos, tabelas, listas e negrito com o visual do site.
- `requirements.txt` : adicionada a dependência `markdown`.

08/10/2026

# Indexação em segundo plano (fila) e gestão de índices
- Motivo: converter um PDF no Docling sem GPU leva minutos (um PDF de 17 páginas com fotos passou dos 300 s de timeout), e o upload rodava dentro da requisição HTTP, travando a tela e perdendo o trabalho quando o tempo estourava. Além disso, só existia um índice (`documentos`).
- **Upload não espera mais o Docling.** `/ingest` só salva o arquivo e enfileira; workers em segundo plano chamam o Docling. A tela `/fila` mostra o status de cada documento (na fila, processando, concluído, erro), atualiza sozinha enquanto houver trabalho e tem "Tentar novamente" nos erros.
- **A fila fica no MySQL** (tabela `jobs_indexacao`, criada automaticamente na primeira subida). Se o container cair ou reiniciar no meio de uma conversão, o job volta para a fila sozinho (em ~2 min, via "batimento" gravado a cada 30 s). Um job que já foi interrompido `JOBS_MAX_TENTATIVAS` vezes (padrão 2) vira erro — protege contra um documento que derruba o servidor em loop.
- **Concorrência**: por padrão 1 documento por vez (`JOBS_CONCURRENCIA`). O Docling já satura a CPU sozinho, então vários índices em paralelo **não aceleram** a conversão: a fila é única, o paralelismo é só de navegação/organização. Só mais CPU no `docling-serve` muda isso.
- **Vários índices**: nova tela `/indices` para criar (nome validado; o prefixo `rci-` é adicionado automaticamente), listar (documentos, tamanho, saúde) e apagar (pede para digitar o nome; os arquivos originais **não** são apagados do servidor, e os jobs daquele índice somem da fila). O upload ganhou um seletor de índice de destino e a **busca varre todos os índices**, mostrando a tag do índice em cada resultado.
- **Segurança**: a aplicação só enxerga o índice padrão (`documentos`) e índices com o prefixo `rci-`. O índice vem da URL em `/preview/<indice>/<id>`, `/download/...` e `/arquivo/...`, então todo nome passa por `indices.eh_gerenciado()` antes de tocar no OpenSearch (testado: `interno-secreto`, `.opendistro_security` e `_all` retornam 404). O índice padrão não pode ser apagado pela interface. As rotas antigas `/preview/<id>` continuam funcionando (índice padrão).
- **Arquivos por índice**: o índice padrão continua gravando em `pdf/` (os arquivos antigos já estão lá e o `doc_id` é um hash do caminho); índices novos gravam em `pdf/<nome>/`. Sem autenticação ainda — criar/apagar índice está aberto a qualquer um até a PR de login.
- `DOCLING_TIMEOUT` (padrão 1800 s) substitui o timeout fixo de 300 s, e o log passa a registrar o tempo de cada conversão.

## Variáveis de ambiente novas (cadastrar no Dokploy)
- `MYSQL_HOST` : host **interno** do serviço MySQL no Dokploy (não o IP público), `MYSQL_PORT` (3306), `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` (`rcisearch`). Sem elas o site abre, mas upload e `/fila` mostram erro de banco indisponível.
- Opcionais: `DOCLING_TIMEOUT`, `JOBS_CONCURRENCIA`, `JOBS_MAX_TENTATIVAS`, `INDEX_PREFIX` (`rci-`), `FUSO_HORAS` (`-3`, horário exibido na fila), `JOBS_DESATIVADOS=1` (não sobe os workers; útil para depuração).

## Arquivos novos
- `db.py` (conexão MySQL), `jobs.py` (fila: enfileirar, reivindicar de forma atômica, batimento, recuperar travados), `worker.py` (threads que consomem a fila), `indices.py` (regras de quais índices a aplicação gerencia e o mapeamento padrão).
- `templates/_topbar.html` (topbar único, antes copiado em 4 templates), `templates/fila.html`, `templates/indices.html`, `templates/indice_apagar.html`.

## Arquivos alterados
- `web_app.py` : rotas `/fila`, `/indices`, `/indices/<nome>/apagar`; `/ingest` enfileira em vez de processar; busca em todos os índices; rotas de preview/download/arquivo aceitam o índice na URL; workers sobem com o app (e só no processo que atende requisições quando o `debug` do Flask está ligado).
- `opensearch_client.py` : métodos para criar/apagar/listar índices e `search` aceitando parâmetros extras. A listagem usa curinga porque a API `_cat` do OpenSearch trava quando recebe um nome explícito que não existe.
- `docling_service.py`, `config.py`, `requirements.txt` (`pymysql[rsa]`; o extra `rsa` traz o `cryptography`, necessário para a autenticação padrão do MySQL 8), `static/style.css`, `templates/base.html`, `templates/index.html`, `templates/ingest.html`, `templates/preview.html`, `templates/erro.html`.

## Como foi testado
Contra um OpenSearch descartável (container local), um Docling falso com atraso e o MySQL real do Dokploy (a tabela de teste foi esvaziada depois): upload responde em < 1 s; processamento em ordem de chegada com concorrência 1; erro do Docling vira "erro" com a mensagem; reenvio do mesmo arquivo enquanto na fila é recusado; **servidor derrubado no meio da conversão → o job volta e conclui**; job que estourou as tentativas vira erro e não entra em loop; apagar índice limpa os jobs dele.

08/10/2026 (2)

# Resumo da busca: não quebra mais o card e não executa HTML do documento
- Sintoma: resultados de documentos antigos mostravam um texto enorme sem espaços (o base64 de imagens que o Docling embutia antes da PR #6) e esse texto estourava a largura do card.
- `utils.montar_trecho()` : monta o resumo do resultado. Escapa todo o HTML (só as tags `<em>` do destaque do OpenSearch sobrevivem) e encurta qualquer sequência de 40+ caracteres sem espaço (base64, URLs gigantes) para 20 caracteres + `…`.
- **Correção de segurança:** o resumo era renderizado com `|safe` sem escapar nada — um documento com `<script>` ou `<img onerror=...>` no texto executaria na tela de busca (o preview já escapava). Testado com `<script>` no destaque e no texto: aparece como texto, não executa.
- `static/style.css` : o card quebra palavras longas (`overflow-wrap: anywhere`); os links "Visualizar" e "Baixar original" não quebram, e só o nome do arquivo quebra quando é muito comprido.
- Os documentos antigos continuam com o base64 salvo no OpenSearch (só o resumo na tela foi corrigido). Reenviar o arquivo sobrescreve o registro e limpa o conteúdo.
