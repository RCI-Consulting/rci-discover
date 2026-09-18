from opensearchpy import OpenSearch, helpers

import config


class OpenSearchClient:

    def __init__(self):
        self.client = OpenSearch(
            hosts=[{"host": config.OPENSEARCH_HOST, "port": config.OPENSEARCH_PORT}],
            http_auth=(config.OPENSEARCH_USER, config.OPENSEARCH_PASSWORD),
            use_ssl=True,
            verify_certs=False,
            ssl_assert_hostname=False,
            ssl_show_warn=False,
        )

    def search(self, index, query):
        return self.client.search(index=index, body=query)

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
