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

08/10/2026 (3)

# Auditoria das buscas (MySQL)
- Cada busca feita em `/` grava uma linha na tabela `auditoria_buscas` (criada sozinha no primeiro uso): **termo**, **total de resultados**, **data/hora**, **usuário** e **IP**. Busca vazia não é registrada.
- **Não atrapalha a busca:** a gravação roda em segundo plano (`auditoria.py`). Se o MySQL estiver fora ou lento, a busca responde normal (testado com o banco inacessível: 0,06 s) e a falha só aparece no log (`[Auditoria] Não consegui registrar a busca`).
- **IP:** atrás do Traefik, `request.remote_addr` seria sempre o IP do proxy. O `ProxyFix` passa a ler o IP real do `X-Forwarded-For`, considerando só o último salto (`PROXIES_CONFIAVEIS`, padrão 1 = o Traefik). Um cabeçalho forjado pelo cliente não engana (testado). Use `PROXIES_CONFIAVEIS=0` se o app for exposto direto, sem proxy.
- **Usuário:** a coluna já existe e é preenchida a partir de `session["usuario"]`, a chave que o login com Microsoft (PR #3) grava. Enquanto não houver login fica `NULL`; quando a PR #3 entrar, passa a ser preenchido sem mudar mais nada.
- Data/hora ficam em **UTC** (como a fila). Para ver no horário de Brasília: `SELECT CONVERT_TZ(criado_em, '+00:00', '-03:00') AS data_hora, termo, total_resultados, usuario, ip FROM auditoria_buscas ORDER BY id DESC;`
- Termo é truncado em 1000 caracteres. `total_resultados` vem do OpenSearch e para em 10000 quando há mais que isso.
- **Atenção (LGPD):** o termo pesquisado pode conter dado pessoal, e junto com usuário e IP isso é dado de uso. Ainda não há política de retenção/limpeza.
- **A conferir no primeiro deploy:** se o IP gravado aparecer como `10.0.0.x` (rede interna do Docker Swarm) em vez do IP real da máquina, o Swarm está mascarando a origem antes do Traefik e a porta do Traefik precisa publicar em `mode: host`.

## Arquivos
- Novo: `auditoria.py`. Alterados: `web_app.py` (ProxyFix e chamada após cada busca), `config.py` (`PROXIES_CONFIAVEIS`).

08/10/2026 (4)

# Menu do topo sempre à esquerda, junto da marca
- Sintoma: ao trocar de página o menu mudava de lado — na home ficava à esquerda (a marca some do topo porque já aparece grande no centro) e nas demais páginas ia para a direita, com a marca à esquerda.
- `static/style.css` : a barra do topo alinha tudo à esquerda (`justify-content: flex-start` com espaçamento único) e o botão **RCISearch** (volta para a home) fica colado no menu. Na home a marca do topo fica `visibility: hidden` em vez de `display: none`, então ocupa o mesmo lugar e o menu **não muda de posição** entre as páginas. Em tela estreita o menu desce para baixo da marca, também à esquerda.
- Medido em 7 páginas (home, busca, fila, índices, upload, preview, erro 404): em 1200 px a marca fica em x=32 e o menu em x=187 em todas; sem rolagem horizontal.

09/10/2026

# Área de administração (`/admin`) e tela de auditoria
- O menu do topo agora tem só **Buscar** e **Administração**. As telas de gestão foram para dentro de `/admin`, com abas: **Índices**, **Adicionar documento**, **Fila** e **Auditoria**.
- Endereços novos: `/admin/indices`, `/admin/documentos` (antigo `/ingest`), `/admin/fila`, `/admin/auditoria`. Os endereços antigos (`/ingest`, `/fila`, `/indices`) redirecionam (308, mantém o método), então links e favoritos continuam funcionando.
- **Auditoria** (`/admin/auditoria`): lê a tabela `auditoria_buscas`. Mostra totais (buscas, termos diferentes, buscas sem resultado), os **termos mais buscados**, os termos **sem resultado** (o que falta indexar) e o histórico paginado (50 por página) com data/hora no fuso de Brasília, termo, resultados, usuário e IP. Filtros: período (24 h, 7/30/90 dias, tudo) e termo contendo o texto (`%` e `_` valem como texto). Termos sempre escapados na tela.
- **Controle de acesso:** `acesso.py` tem `eh_admin()`, usado por todas as rotas `/admin` (responde 403 com página amigável) e pelo link do menu. **Hoje ela libera todo mundo**, porque o app roda só na rede interna e ainda não há login. Quando o login com Microsoft entrar (PR #3), só essa função muda, passando a conferir o papel/grupo do usuário (App Role do Entra ID).
- **Enquanto não houver login, `/admin` está aberto para quem alcançar o servidor**, inclusive apagar índices. Não exponha o app fora da rede interna antes de ligar o controle de acesso.
- Se o MySQL estiver fora, a tela de auditoria abre com um aviso em vez de quebrar.

## Arquivos
- Novos: `acesso.py`, `templates/auditoria.html`, `templates/_admin_abas.html`. Alterados: `web_app.py` (rotas em `/admin`, redirects, bloqueio 403), `auditoria.py` (consultas), `templates/_topbar.html`, `fila.html`, `indices.html`, `indice_apagar.html`, `ingest.html` (links e abas), `static/style.css`.

09/10/2026 (2)

# Facetas da busca (filtros laterais) e botão Reindexar
- A tela de resultados ganhou uma **coluna de filtros à esquerda**, com contagem em cada opção: **Índice**, **Tipo de arquivo** (PDF, DOCX…) e **Indexado em** (últimas 24 h, 7/30 dias, último ano). Marcar/desmarcar aplica na hora; "Limpar filtros" volta ao conjunto completo. Em tela estreita os filtros ficam acima dos resultados.
- **Como contam** (`facetas.py`): cada grupo mostra as contagens considerando os filtros dos *outros* grupos, mas não o dele. Escolher "PDF" não esconde "DOCX": você vê quantos resultados teria ao trocar ou somar. Índice e tipo aceitam várias opções (OU); a data é uma só. Valores vindos da URL são validados (índice só se for gerenciado, tipo só `a-z0-9`, data só as faixas conhecidas).
- **Campos novos nos documentos:** `tipo` (extensão em minúsculo; `outro` se não tiver) e `indexado_em` (data UTC). Índices novos já nascem com eles; nos índices existentes o app adiciona os campos ao mapeamento sozinho (`put_mapping`) na primeira indexação.
- **Documentos antigos não têm esses campos**, então não entram nas facetas de tipo e data até serem reindexados. O índice continua aparecendo normalmente.
- **Botão Reindexar** (`/admin/indices`): a nova coluna **Desatualizados** mostra quantos documentos de cada índice ainda não têm `tipo`/`indexado_em`. Reindexar recoloca esses documentos na fila usando o arquivo original guardado no servidor (não precisa enviar de novo) e sobrescreve o mesmo documento (mesmo `_id`). Arquivos que não existem mais no servidor são só contados e avisados — esses precisam ser enviados de novo. Reprocessa pelo Docling, então demora como um upload normal.
- **Auditoria:** a tabela `auditoria_buscas` ganhou a coluna `filtros` (ex.: `tipo=pdf; data=7d`), criada sozinha em tabelas antigas, e a tela de auditoria mostra uma coluna **Filtros**.
- **A conferir no primeiro deploy:** abrir uma busca e ver se as facetas aparecem; depois de reindexar, `tipo` e `indexado_em` devem aparecer no Dev Tools (`GET rci-*,documentos/_mapping`). Se alguma busca der erro de agregação, mande a mensagem.

## Arquivos
- Novo: `facetas.py`. Alterados: `web_app.py` (busca com `post_filter`/`aggs`, rota `/admin/indices/<nome>/reindexar`), `opensearch_client.py` (`garantir_mapeamento`, `contar_desatualizados`, `listar_desatualizados`), `docling_service.py` (grava `tipo` e `indexado_em`), `indices.py` (mapeamento), `auditoria.py` (coluna `filtros`), `templates/index.html`, `indices.html`, `auditoria.html`, `static/style.css`.

09/10/2026 (3)

# Resultados da busca em 3 colunas
- A tela de resultados passou a ter **3 colunas**: **filtros** (facetas) na margem esquerda, alinhados à marca RCISearch do topo; **resultados fixos no centro da página** (720 px); e uma **coluna livre à direita**, com a mesma largura da esquerda para o centro ficar exatamente centralizado. A coluna da direita está vazia de propósito (reservada para algo útil depois, como prévia rápida do documento).
- A **caixa de busca** agora fica alinhada com a coluna dos resultados (antes ficava colada no canto esquerdo da tela). A home não mudou.
- **Telas menores:** abaixo de 1200 px a coluna da direita some e filtros + resultados ficam juntos à esquerda; abaixo de 860 px os filtros passam para cima dos resultados.
- Medido em 1920, 1366, 1200, 1150 e 700 px: em 1200 px ou mais o centro dos resultados coincide com o centro da tela (diferença 0 px); sem rolagem horizontal; filtros em x=32, igual à marca.
- Só `templates/index.html` e `static/style.css` mudaram (sem mudança de rotas ou de lógica).

13/10/2026 (2)

# Logo da RCI como botão home e favicon
- **Botão home:** o texto "RCISearch" do topo foi trocado pela **logo azul da RCI**, fixa no canto esquerdo, que leva para a página inicial. Ela aparece em **todas as telas** (inclusive na home), sempre com o mesmo tamanho (72×30 px) e no mesmo lugar, então o menu ao lado também não muda de posição entre as páginas (medido em home, busca, administração e erro 404: logo em x=32 e menu em x=132 em todas). O "RCISearch" grande no centro da home continua.
- **Favicon:** ícone da aba com a logo clara (versão "branco" da marca, `#F5EEDB`) sobre um **quadrado azul arredondado**. Usei o fundo azul porque a logo clara sozinha, sobre transparente, some em abas claras. `static/favicon.ico` (16 a 256 px), `static/img/favicon-32.png` e `apple-touch-icon.png` (180 px, atalho no celular). O app responde também em `/favicon.ico`, onde os navegadores procuram por padrão.
- **Arquivos gerados** dos PNGs da marca (3939×3939 px, com muita margem transparente): `static/img/rci-logo-azul.png` (recortada e reduzida para 360 px de largura, 12 KB). Os PNGs originais continuam na pasta `images/`, fora do Git.
- Alterados: `templates/_topbar.html`, `base.html`, `preview_erro.html`, `static/style.css` (regra `.home-btn`), `web_app.py` (rota `/favicon.ico`).

13/10/2026 (3)

# Projetos (grupos de índices com busca própria)
- Um **projeto** agrupa vários índices e tem uma **página de busca própria**, como os projetos do Watson Explorer. Um índice pode estar em **vários projetos**. A busca geral (`/`, "Buscar") continua pesquisando em todos os índices, como antes.
- **Telas:** `/projetos` lista os projetos em cards; `/p/<endereço>` é a busca de um projeto (caixa de busca, filtros e resultados só dos índices dele, com o nome e a descrição do projeto). O menu do topo ganhou **Projetos**.
- **Administração → Projetos** (`/admin/projetos`): criar, editar (nome, descrição, índices por checkbox) e apagar. O **endereço** (`/p/...`) vem do nome (sem acento, minúsculo, com hífen; `-2`, `-3` se já existir) e **não muda ao renomear**, para links salvos continuarem valendo. Apagar um projeto remove só o projeto: índices e documentos não são tocados. Apagar um **índice** o tira de todos os projetos.
- **Dados:** tabelas `projetos` e `projeto_indices` no MySQL (criadas sozinhas no primeiro uso). O vínculo guarda o nome real do índice; índice que não existe mais no OpenSearch é ignorado na busca.
- **Busca no projeto:** o alvo é a lista de índices do projeto (nomes validados como índices gerenciados). Projeto sem índices não consulta o OpenSearch e avisa na tela. As facetas (Índice, Tipo, Data) valem só dentro do projeto.
- **Auditoria:** buscas feitas num projeto gravam `projeto=<endereço>` na coluna *Filtros*, junto dos filtros de facetas.
- **Se o MySQL estiver fora:** `/p/<projeto>` mostra uma página de erro amigável (503) avisando que a busca geral continua; `/projetos` e a administração abrem com aviso; a busca geral não é afetada.
- **Ainda não existe:** restrição de quem vê cada projeto. Quando o login entrar (PR #3), dá para ligar projetos a grupos/papéis do Entra ID.

## Arquivos
- Novos: `projetos.py`, `templates/projetos.html`, `projetos_admin.html`, `projeto_form.html`, `projeto_apagar.html`. Alterados: `web_app.py` (busca separada em `_pesquisar`/`_tela_busca`, rotas `/projetos`, `/p/<slug>` e `/admin/projetos...`), `templates/index.html`, `_topbar.html`, `_admin_abas.html`, `static/style.css`.

13/10/2026 (4)

# Autocomplete, sugestões e dicionário (como no Watson Explorer)
- **Pesquisa feita antes:** no Watson Explorer o autocomplete é um *dicionário* (frases extraídas das coleções ou de uma lista) e a expansão por sinônimos usa dicionários de termos/siglas aplicados na hora da busca, sem reindexar. No OpenSearch **não é preciso montar um dicionário para ter autocomplete**: dá para usar o que já existe (nomes de documentos e buscas anteriores). O dicionário manual só acrescenta o que o índice não sabe: **siglas, nomes internos e sinônimos**. Por isso a solução tem as duas coisas.
- **Autocomplete** (`static/autocomplete.js`, endpoint `/sugestoes`): a partir de 2 letras, lista até 8 sugestões, com ↑ ↓ Enter Esc e mouse. Três fontes: **dicionário** (termo preferido; digitar a sigla sugere o nome completo), **buscas populares** (o que outras pessoas já pesquisaram e acharam algo) e **nomes de documentos** que começam com o texto (vai direto para o preview). Dentro de um projeto, vale só o projeto. Se uma fonte falhar (MySQL ou OpenSearch fora), as outras continuam.
- **Você quis dizer:** quando a busca acha 3 resultados ou menos, o app pergunta ao OpenSearch (phrase suggester sobre `conteudo`) se há uma correção e mostra "Você quis dizer: …" com link. É um pedido separado e opcional: se falhar, a busca não é afetada.
- **Buscas populares:** quando uma busca não acha nada, a tela sugere as buscas mais repetidas que deram resultado.
- **Dicionário** (Administração → Dicionário, tabela `dicionario_termos`): termo preferido + sinônimos/siglas (ex.: `acordo de confidencialidade` = `NDA, termo de sigilo`). **Expansão na busca:** se a consulta tem uma das formas (palavra inteira, sem diferenciar maiúsculas), a busca também procura pelas outras, e a tela mostra "Também buscando por: …". Vale na hora, **sem reindexar**; cache de 30 s.
- **Privacidade (LGPD):** buscas viram sugestão para outras pessoas só se foram feitas **pelo menos `SUGESTOES_MIN_BUSCAS` vezes** (padrão 2) nos últimos 180 dias, e nunca se tiverem e-mail ou sequência longa de números (CPF, CNPJ, telefone). Para sugerir tudo que já achou resultado, use `SUGESTOES_MIN_BUSCAS=1`.
- **Ainda não validado contra o OpenSearch real:** a consulta de nomes (`match_phrase_prefix`) e o phrase suggester seguem a documentação, mas só foram testados com cliente simulado. Conferir no primeiro deploy: digitar 3 letras de um nome de documento, e buscar uma palavra com erro de digitação que não ache nada.
- **Limites conhecidos:** o dicionário não é separado por projeto (vale para todos); a expansão não ignora acentos ao procurar a forma na consulta (o autocomplete ignora); sinônimos não entram na busca dentro de `suggest`.

## Arquivos
- Novos: `dicionario.py`, `sugestoes.py`, `static/autocomplete.js`, `templates/dicionario_admin.html`, `dicionario_form.html`. Alterados: `web_app.py` (`/sugestoes`, rotas `/admin/dicionario...`, expansão e correção na busca), `auditoria.py` (`termos_populares`), `utils.py` (`escapar_destaque`), `config.py` (`SUGESTOES_MIN_BUSCAS`), `templates/index.html`, `_admin_abas.html`, `static/style.css`.

14/10/2026

# Nova página inicial (boas-vindas) e busca geral em `/busca`
- **Home (`/`)**: sem caixa de busca. Mostra "Bem-vindo ao RCISearch" e "Comece a explorar", com **cards**: **Busca geral** (vai para `/busca`), **Projetos** (vai para `/projetos`, com a contagem de projetos; se o MySQL estiver fora, o número some e o card continua funcionando) e **Administração** (vai para `/admin`). O card de Administração só aparece quando `acesso.eh_admin()` é verdadeiro, então some sozinho para quem não for admin quando o login entrar (PR #3).
- **Busca geral agora é `/busca`**, igual à antiga home: caixa de busca no centro, autocomplete, resultados e filtros. O botão **Buscar** do menu, o "Voltar à busca" das páginas de erro e do preview e os formulários da busca apontam para `/busca`.
- **Links antigos continuam valendo:** `/?q=...` redireciona (302) para `/busca?...` mantendo todos os parâmetros (termo e filtros). `/` sem `q` é a home.
- A logo azul do topo continua levando para a home. Layout dos cards: 3 em linha a partir de ~1000 px, 2 + 1 em tela média e empilhados no celular (conferido em 1440, 1000, 700 e 380 px, sem rolagem horizontal).
- Alterados: `web_app.py` (rotas `/` e `/busca`), `templates/home.html` (novo), `_topbar.html`, `erro.html`, `preview.html`, `projetos.html`, `static/style.css`.

21/10/2026

# Relevância, ordenação, paginação e destaques (como no Watson Explorer)
- **Pesos por campo:** novo campo `titulo` em cada documento. É o primeiro título (`#`, `##` ou `###`) do Markdown do Docling; se não houver, é o nome do arquivo sem extensão e com `_`/`-` trocados por espaço (o OpenSearch não separa `contrato_fornecedor` em duas palavras). Pesos: `titulo` 4×, `nome` 3×, `conteudo` 1×. A frase exata (palavras na ordem digitada) também ganha pontos. O card mostra o título e, embaixo, o nome do arquivo.
- **Recência:** documentos indexados há pouco ganham um bônus de até 50% (`PESO_RECENCIA`, padrão `0.5`; `0` desliga). O bônus é cheio nos primeiros 30 dias e cai para a metade em cerca de um ano. Não é filtro: um documento antigo muito relevante continua na frente. Documentos sem data (anteriores às facetas) ficam neutros.
- **Ordenação:** "Ordenar: Relevância · Mais recentes · Nome (A–Z)" ao lado do total de resultados. A escolha vai na URL (`ordem=recentes|nome`) e é mantida ao filtrar.
- **Paginação:** 10 resultados por página, barra numerada (`1 … 5 6 7 8 9 … 20`) e a URL guarda a página (`pagina=`). Mudar filtro ou ordem volta para a página 1. O OpenSearch pagina até 10.000 resultados (página 1000).
- **Auditoria:** só registra a busca na página 1 com a ordem padrão. Trocar de página ou de ordem não conta como busca nova (antes, cada clique inflaria os "mais buscados").
- **Destaques** (*best bets*/*spotlights* do Watson Explorer), em **Administração → Destaques** (tabela `destaques` no MySQL, criada sozinha):
  - O admin escolhe um documento e palavras-chave. Quando a busca **é** a palavra-chave ou a **contém** como palavras inteiras (sem diferenciar maiúsculas nem acentos), o documento aparece num bloco **Destaque** acima dos resultados. Vale só na página 1, aparece mesmo com filtros ou outra ordem e mesmo se a busca normal não achar nada. No máximo 3 por busca.
  - Para escolher o documento: procure pelo nome/título na tela "Novo destaque", ou use o link **Destacar na busca** no preview (só para admin).
  - Descrição opcional: aparece no lugar do trecho do documento.
  - Na busca de um projeto, só aparecem destaques de índices do projeto. Apagar um índice apaga os destaques dele. Documento que sumiu do índice é ignorado na busca e marcado na edição.
  - Se o MySQL estiver fora, a busca segue sem destaques. Cache de 30 s, como o dicionário.
- **Documentos antigos:** não têm `titulo` e entram na coluna **Desatualizados** em Administração → Índices. **Reindexar** agora preenche o título na hora, a partir do texto já salvo (sem Docling), e continua mandando para a fila só os que não têm tipo/data. Enquanto não reindexar, eles ainda são achados pelo nome e pelo texto.

## Variável de ambiente nova (opcional, no Dokploy)
- `PESO_RECENCIA` (padrão `0.5`).

## Como foi testado
Com um OpenSearch e um MySQL simulados (cliente Flask de teste): corpo da consulta (pesos, `function_score`, `from`/`size`, `sort`), páginas e links preservando os filtros, validação de `ordem` e `pagina` vindas da URL, auditoria só na página 1, destaques (casamento sem acento, palavra inteira, filtro por projeto, documento sumido), telas de admin com o MySQL fora e escape de HTML do documento.

**A conferir no primeiro deploy (não testado contra o OpenSearch real):**
- buscar algo e ver se a ordem dos resultados faz sentido;
- usar "Mais recentes" e "Nome (A–Z)";
- clicar em Reindexar num índice antigo e conferir `titulo` no Dev Tools (`GET rci-*,documentos/_search?_source=titulo,nome`);
- criar um destaque e buscar a palavra-chave.

Se a busca der erro de `function_score` ou de `sort`, mande a mensagem.

## Arquivos
- Novos: `relevancia.py`, `destaques.py`, `templates/destaques_admin.html`, `destaque_form.html`.
- Alterados: `web_app.py` (busca, `url_com`, rotas `/admin/destaques...`, reindexar preenche títulos, apagar índice apaga destaques), `opensearch_client.py` (`preencher_titulos`, desatualizados inclui sem título), `indices.py` (campo `titulo`), `docling_service.py` (grava `titulo`), `utils.py` (`extrair_titulo`), `config.py` (`PESO_RECENCIA`), `templates/index.html`, `preview.html`, `indices.html`, `_admin_abas.html`, `static/style.css`.

21/10/2026 (2)

# Temas dos resultados (agrupamento por assunto, como no Watson Explorer)
- A coluna da direita da busca, que estava reservada, agora mostra os **Temas**: as palavras que mais se destacam nos documentos achados em comparação com o índice inteiro. Exemplo: em "contrato" podem surgir "aditivo", "rescisão", "fornecedor". É o equivalente ao *clustering* do Watson Explorer (herdado do Vivisimo), na versão que o OpenSearch faz sem configurar nada: agregação `significant_text` sobre `conteudo`.
- **Refinar:** clicar num tema deixa só os documentos que contêm a palavra (`tema=` na URL). Os temas são recalculados dentro desse recorte, então dá para descer mais um nível (até 3 temas ao mesmo tempo, todos obrigatórios). Os temas escolhidos aparecem como etiquetas "Só documentos sobre: escala ×" acima dos resultados; o × tira o tema. "Limpar filtros" tira tudo.
- **Leve:** analisa só os 100 documentos mais relevantes (`sampler`), ignora texto repetido entre documentos (`filter_duplicate_text`) e roda num pedido separado, com limite de 5 s. Se falhar ou estourar o tempo, a busca aparece normal, só sem o painel. A contagem ao lado de cada tema só aparece quando os 100 cobrem todos os resultados (senão seria uma contagem da amostra).
- **Fica de fora:** as próprias palavras da busca, palavras de até 3 letras, termos que começam com número, `imagem` (marcador do Docling) e pedaços de URL. Com menos de 3 resultados o painel não aparece.
- **Telas menores:** abaixo de 1200 px o painel desce para baixo dos filtros, na coluna da esquerda; abaixo de 860 px fica entre os filtros e os resultados, com os temas em linha. Conferido em 1366, 1000 e 700 px, sem rolagem horizontal.
- Respeita os filtros (índice, tipo, data) e a busca do projeto. A auditoria registra os temas escolhidos na coluna *Filtros* (`tema=escala`).
- **Limite conhecido:** são palavras soltas. Expressões como "plano de saúde" precisariam de um subcampo com *shingles* no mapeamento e reindexar tudo.

## Como foi testado
Com OpenSearch simulado: corpo do pedido (amostra, filtros das facetas aplicados, temas escolhidos como filtro), exclusão das palavras da busca e de temas inválidos, falha do OpenSearch sem quebrar a busca, links de refinar e de tirar tema mantendo os outros parâmetros, auditoria. Telas conferidas no navegador com dados de exemplo.

**A conferir no deploy de dev:** buscar algo com bastante resultado e ver se os temas fazem sentido (palavras genéricas do português que escaparem podem entrar no `_EXCLUIR` de `temas.py`); medir se a busca ficou perceptivelmente mais lenta.

## Arquivos
- Novo: `temas.py`. Alterados: `web_app.py` (temas na busca e `url_com` aceita lista), `templates/index.html`, `static/style.css`.
