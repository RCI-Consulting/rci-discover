from opensearchpy import OpenSearch, helpers
from opensearchpy.exceptions import NotFoundError

import config
import indices

from utils import extrair_titulo

# Documentos indexados antes das facetas não têm `indexado_em`.
_SEM_DATA = {"bool": {"must_not": {"exists": {"field": "indexado_em"}}}}
# ...e os indexados antes do ajuste de relevância não têm `titulo`.
_SEM_TITULO = {"bool": {"must_not": {"exists": {"field": "titulo"}}}}
_DESATUALIZADO = {"bool": {"should": [_SEM_DATA, _SEM_TITULO], "minimum_should_match": 1}}


class OpenSearchClient:

    _mapeados = set()  # índices que já receberam os campos das facetas neste processo

    def __init__(self):
        self.client = OpenSearch(
            hosts=[{"host": config.OPENSEARCH_HOST, "port": config.OPENSEARCH_PORT}],
            http_auth=(config.OPENSEARCH_USER, config.OPENSEARCH_PASSWORD),
            use_ssl=True,
            verify_certs=False,
            ssl_assert_hostname=False,
            ssl_show_warn=False,
        )

    def search(self, index, query, **kwargs):
        return self.client.search(index=index, body=query, **kwargs)

    def index(self, index, body, doc_id=None, pipeline=None):
        """Indexa um único documento. Se doc_id for informado, o
        documento é criado/atualizado com esse ID (idempotente)."""
        kwargs = {
            "index": index,
            "body": body,
        }
        if doc_id:
            kwargs["id"] = doc_id
        if pipeline:
            kwargs["pipeline"] = pipeline

        return self.client.index(**kwargs)

    def get(self, index, doc_id):
        """Busca um documento pelo ID. Retorna None se não existir."""
        try:
            return self.client.get(index=index, id=doc_id)
        except Exception:
            return None

    def apagar_documento(self, index, doc_id):
        """Apaga um documento; se ele já não existir, não é erro."""
        try:
            self.client.delete(index=index, id=doc_id)
        except NotFoundError:
            pass

    def apagar_documentos(self, index, doc_ids):
        """Apaga vários documentos de uma vez. Devolve quantos foram apagados
        (os que já não existiam não contam e não são erro)."""
        acoes = ({"_op_type": "delete", "_index": index, "_id": d} for d in doc_ids)
        sucesso, _erros = helpers.bulk(self.client, acoes, raise_on_error=False, stats_only=True)
        return sucesso

    def existentes(self, pares):
        """Dos pares (índice, id), quais ainda existem no OpenSearch (um pedido só)."""
        if not pares:
            return set()
        resp = self.client.mget(body={"docs": [{"_index": i, "_id": d, "_source": False} for i, d in pares]})
        return {(doc["_index"], doc["_id"]) for doc in resp.get("docs", []) if doc.get("found")}

    def exists(self, index, doc_id):
        return self.client.exists(index=index, id=doc_id)

    def index_exists(self, index):
        return self.client.indices.exists(index=index)

    def bulk(self, actions, raise_on_error=False):
        return helpers.bulk(
            self.client,
            actions,
            raise_on_error=raise_on_error,
            stats_only=False,
        )

    def garantir_mapeamento(self, index):
        """Adiciona `tipo`, `indexado_em` e `titulo` ao mapeamento de um índice
        criado antes deles (sem isso o OpenSearch mapearia `tipo` e a data como
        texto, que não serve para filtrar nem agrupar). Idempotente e feito uma vez."""
        if index in self._mapeados:
            return
        try:
            self.client.indices.put_mapping(index=index, body={"properties": indices.CAMPOS_NOVOS})
        except NotFoundError:
            return  # índice apagado no meio do caminho: deixa o index() reportar
        self._mapeados.add(index)

    def portugues_ativo(self, index):
        """Se o índice já tem os subcampos `.pt` (analisador português)."""
        mapa = self.client.indices.get_mapping(index=index)[index]["mappings"].get("properties", {})
        return "pt" in mapa.get("conteudo", {}).get("fields", {})

    def ativar_portugues(self, index):
        """Liga o analisador português num índice criado antes dele:
        1. registra o analisador nas configurações (o OpenSearch exige o índice
           fechado para isso: ele fica indisponível por um ou dois segundos);
        2. acrescenta os subcampos `.pt` em nome, título e conteúdo, sem mexer
           no que já existe;
        3. reprocessa os documentos a partir do texto já salvo (sem Docling),
           em segundo plano. Devolve o id da tarefa no OpenSearch."""
        self.garantir_mapeamento(index)
        config = self.client.indices.get_settings(index=index)[index]["settings"]["index"]
        if indices.ANALISADOR_PT not in config.get("analysis", {}).get("analyzer", {}):
            self.client.indices.close(index=index)
            try:
                self.client.indices.put_settings(index=index, body=indices.ANALISE_PT)
            finally:
                self.client.indices.open(index=index, wait_for_active_shards=1)
        mapa = self.client.indices.get_mapping(index=index)[index]["mappings"].get("properties", {})
        novos = {}
        for campo in indices.CAMPOS_COM_PT:
            atual = mapa.get(campo, {"type": "text"})
            if atual.get("type", "text") != "text":
                continue
            novos[campo] = {**atual, "fields": {**atual.get("fields", {}), "pt": indices.SUBCAMPO_PT}}
        self.client.indices.put_mapping(index=index, body={"properties": novos})
        tarefa = self.client.update_by_query(
            index=index, body={"query": {"match_all": {}}},
            conflicts="proceed", refresh=True, wait_for_completion=False,
        )
        return tarefa.get("task")

    def indices_em_atualizacao(self):
        """Índices com reprocessamento (update_by_query) rodando agora."""
        try:
            resp = self.client.tasks.list(actions="*byquery", detailed=True)
        except Exception:
            return set()
        nomes = set()
        for no in resp.get("nodes", {}).values():
            for tarefa in no.get("tasks", {}).values():
                descricao = tarefa.get("description", "")  # ex.: "update-by-query [rci-rh]"
                if "[" in descricao:
                    nomes.update(n.strip() for n in descricao.split("[", 1)[1].split("]", 1)[0].split(","))
        return nomes

    def listar_sem_texto(self, index, minimo=50):
        """Gera (id, nome, caminho) dos documentos com menos de `minimo`
        caracteres de texto — tipicamente PDFs escaneados e imagens indexados
        antes do OCR. Lê o texto salvo de cada um, então pode demorar em índices grandes."""
        for hit in helpers.scan(self.client, index=index, query={"query": {"match_all": {}}}, _source=["nome", "caminho", "conteudo"]):
            fonte = hit.get("_source", {})
            if len((fonte.get("conteudo") or "").strip()) < minimo:
                yield hit["_id"], fonte.get("nome"), fonte.get("caminho")

    def contar_desatualizados(self, padrao):
        """{índice: quantos documentos ainda não têm os campos das facetas ou o título}."""
        resp = self.client.search(
            index=padrao,
            body={"size": 0, "query": _DESATUALIZADO, "aggs": {"i": {"terms": {"field": "_index", "size": 100}}}},
            ignore_unavailable=True,
        )
        return {b["key"]: b["doc_count"] for b in resp["aggregations"]["i"]["buckets"]}

    def preencher_titulos(self, index):
        """Grava `titulo` nos documentos que não têm, a partir do texto já
        salvo (não passa pelo Docling, então é rápido). Retorna quantos."""
        self.garantir_mapeamento(index)
        acoes = (
            {
                "_op_type": "update",
                "_index": index,
                "_id": hit["_id"],
                "doc": {"titulo": extrair_titulo(hit["_source"].get("conteudo", ""), hit["_source"].get("nome", ""))},
            }
            for hit in helpers.scan(self.client, index=index, query={"query": _SEM_TITULO}, _source=["nome", "conteudo"])
        )
        sucesso, _erros = helpers.bulk(self.client, acoes, raise_on_error=False, stats_only=True)
        return sucesso

    def listar_desatualizados(self, index):
        """Gera (id, nome, caminho) dos documentos sem os campos das facetas."""
        for hit in helpers.scan(self.client, index=index, query={"query": _SEM_DATA}, _source=["nome", "caminho"]):
            fonte = hit.get("_source", {})
            yield hit["_id"], fonte.get("nome"), fonte.get("caminho")

    def criar_indice(self, index, body):
        return self.client.indices.create(index=index, body=body)

    def apagar_indice(self, index):
        return self.client.indices.delete(index=index)

    def listar_indices(self, padrao):
        """Resumo (nome, docs, tamanho, saúde) dos índices que casam com
        `padrao` (aceita lista separada por vírgula e curingas)."""
        return self.client.cat.indices(
            index=padrao,
            format="json",
            h="index,docs.count,store.size,health,status",
        )

