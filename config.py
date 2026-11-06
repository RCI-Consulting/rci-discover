import os
 
from dotenv import load_dotenv
 
load_dotenv()

# Modo debug do Flask (recarrega ao salvar e mostra o debugger no navegador).
# O debugger permite executar código no servidor, então fica DESLIGADO por
# padrão e só deve ser ligado na máquina de desenvolvimento: FLASK_DEBUG=1.
MODO_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

# Marca do produto: nome exibido nas telas, títulos e e-mails. Trocar o nome
# é só mudar estas variáveis no .env / Dokploy. APP_NOME_DESTAQUE é o final do
# nome pintado em azul (ex.: "Explor" + "IA"); vazio = nome todo numa cor só.
APP_NOME = os.getenv("APP_NOME", "Discover")
APP_NOME_DESTAQUE = os.getenv("APP_NOME_DESTAQUE", "")
# Logo do botão "home" no topo: caminho de uma imagem dentro de static/ e texto
# alternativo. Vazio (padrão) = logo do Discover desenhado em HTML (lupa no "O").
APP_LOGO = os.getenv("APP_LOGO", "")
APP_LOGO_ALT = os.getenv("APP_LOGO_ALT", "RCI")

# Login local (usuarios.py). SECRET_KEY assina o cookie da sessão: defina um
# valor longo e aleatório no Dokploy (ex.: python -c "import secrets;
# print(secrets.token_hex(32))"). Sem ela, uma chave temporária é gerada a cada
# reinício e todo mundo precisa entrar de novo.
SECRET_KEY = os.getenv("SECRET_KEY", "")
# Horas que a sessão dura sem precisar entrar de novo.
SESSAO_HORAS = int(os.getenv("SESSAO_HORAS", "8"))
# Cookie só por HTTPS. Use COOKIE_SEGURO=0 apenas acessando por http:// sem
# domínio (ex.: IP da VM de testes); em localhost o navegador já aceita.
COOKIE_SEGURO = os.getenv("COOKIE_SEGURO", "1") == "1"
# Primeiro administrador: criado no primeiro acesso à tela de login, só se
# ainda não existir nenhum usuário. Ele troca a senha ao entrar.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_SENHA = os.getenv("ADMIN_SENHA", "")
ADMIN_NOME = os.getenv("ADMIN_NOME", "Administrador")
 
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

# Índices gerenciados pela aplicação: o índice padrão (legado) mais qualquer
# índice criado pela tela "Índices", que sempre recebe esse prefixo. Isso evita
# que a interface enxergue ou apague índices internos do OpenSearch.
INDEX_PREFIX = os.getenv("INDEX_PREFIX", "rci-")

# Réplicas de cada índice. Com um nó só do OpenSearch, a réplica não tem onde
# ficar e o índice aparece com saúde amarela: por isso o padrão é 0. Se o
# cluster ganhar mais nós, use 1 (cada documento passa a existir em dois nós).
# O app aplica esse valor aos índices novos e ajusta os existentes ao subir.
INDEX_REPLICAS = int(os.getenv("INDEX_REPLICAS", "0"))

# Tempo máximo (segundos) esperando o Docling converter UM documento. Em CPU
# sem GPU um PDF de ~17 páginas com fotos passa de 5 min.
DOCLING_TIMEOUT = int(os.getenv("DOCLING_TIMEOUT", "1800"))

# Banco MySQL que guarda a fila de indexação (sobrevive a restart/crash).
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "rcisearch")

# Fila de indexação: quantos documentos o Docling converte ao mesmo tempo
# (ele já satura a CPU sozinho, então o padrão é 1), e quantas vezes um job
# interrompido por crash/restart é tentado antes de virar erro.
JOBS_CONCURRENCIA = int(os.getenv("JOBS_CONCURRENCIA", "1"))
JOBS_MAX_TENTATIVAS = int(os.getenv("JOBS_MAX_TENTATIVAS", "2"))

# Fuso para exibir horários na tela (o banco guarda em UTC). Brasília = -3.
FUSO_HORAS = int(os.getenv("FUSO_HORAS", "-3"))

# Quantos proxies reverso ficam na frente do app (o Traefik do Dokploy = 1).
# O IP do cliente é lido do X-Forwarded-For nesse nível; 0 desliga a leitura
# (use 0 se o app for exposto direto, senão o cabeçalho poderia ser forjado).
PROXIES_CONFIAVEIS = int(os.getenv("PROXIES_CONFIAVEIS", "1"))

# Autocomplete: uma busca anterior só vira sugestão para os outros usuários se
# foi feita pelo menos essa quantidade de vezes (evita expor o que uma única
# pessoa digitou). 1 = sugere qualquer busca que já achou algo.
SUGESTOES_MIN_BUSCAS = int(os.getenv("SUGESTOES_MIN_BUSCAS", "2"))

# Fontes do crawler: pastas do servidor (separadas por vírgula) onde uma fonte
# pode apontar, com as subpastas. Monte os compartilhamentos de rede dentro
# delas no Dokploy (ex.: /mnt/fontes/juridico).
FONTES_PASTAS_PERMITIDAS = os.getenv("FONTES_PASTAS_PERMITIDAS", "/mnt/fontes")

# OCR no Docling: lê imagens e PDFs escaneados (sem ele entram sem texto).
# DOCLING_OCR_FORCAR=1 refaz o OCR até em páginas que já têm texto digital
# (bem mais lento; só para PDFs com camada de texto ruim).
DOCLING_OCR = os.getenv("DOCLING_OCR", "1") == "1"
DOCLING_OCR_FORCAR = os.getenv("DOCLING_OCR_FORCAR", "0") == "1"
DOCLING_OCR_IDIOMAS = [i.strip() for i in os.getenv("DOCLING_OCR_IDIOMAS", "pt,en").split(",") if i.strip()]

# TXT, DOCX e ODT são lidos direto pelo app (extracao.py), sem o Docling.
# EXTRACAO_RAPIDA=0 manda tudo para o Docling, como antes.
EXTRACAO_RAPIDA = os.getenv("EXTRACAO_RAPIDA", "1") == "1"

# Bônus de recência na relevância: um documento indexado agora vale até
# (1 + PESO_RECENCIA) vezes o mesmo documento antigo. 0 desliga o bônus.
PESO_RECENCIA = float(os.getenv("PESO_RECENCIA", "0.5"))
