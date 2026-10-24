"""Create the explicit vector/hybrid schema for a configured Azure Search service."""

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)


def setup_index(endpoint: str, dimensions: int, *, index_name: str = "rag-chunks") -> None:
    if dimensions < 1:
        raise ValueError("Embedding dimensions must be positive")
    credential = DefaultAzureCredential()
    try:
        with SearchIndexClient(endpoint, credential) as client:
            client.create_or_update_index(SearchIndex(
                name=index_name,
                fields=[
                    SimpleField(name="id", type="Edm.String", key=True, filterable=True),
                    SearchableField(name="content", type="Edm.String"),
                    SimpleField(name="source_key", type="Edm.String", filterable=True),
                    SimpleField(name="revision", type="Edm.String", filterable=True),
                    SimpleField(name="use_case", type="Edm.String", filterable=True),
                    SearchField(name="vector", type="Collection(Edm.Single)",
                                searchable=True, vector_search_dimensions=dimensions,
                                vector_search_profile_name="hybrid"),
                ],
                vector_search=VectorSearch(
                    algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
                    profiles=[VectorSearchProfile(name="hybrid", algorithm_configuration_name="hnsw")],
                ),
            ))
    finally:
        credential.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--dimensions", type=int, default=1536)
    parser.add_argument("--index-name", default="rag-chunks")
    args = parser.parse_args()
    setup_index(args.endpoint, args.dimensions, index_name=args.index_name)


if __name__ == "__main__":
    main()
