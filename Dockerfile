# Versão fixa (a mesma do .venv de desenvolvimento): "python:3" mudava de
# versão sozinho a cada build.
FROM python:3.12-slim

WORKDIR /usr/src/app

# Logs na hora: sem isso o Python segura os print() num buffer e as mensagens
# do app ([Login], [Fila], [Docling]...) demoram ou nem aparecem no Dokploy.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python", "./web_app.py" ]
