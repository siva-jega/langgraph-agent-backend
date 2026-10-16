import os
import uuid

from pymilvus import (Collection, CollectionSchema, DataType, FieldSchema,
                      connections, utility)

MILVUS_HOST = "localhost"
MILVUS_PORT = 19530


def create_collection(collection_name: str):
    try:
        if collection_name not in utility.list_collections():
            print(f"Creating new collection: {collection_name}")
            fields = [
                FieldSchema(
                    name="id",
                    is_primary=True,
                    dtype=DataType.INT64,
                    auto_id=True,
                ),
                FieldSchema(
                    name="text",
                    dtype=DataType.VARCHAR,
                    max_length=65535,
                ),
                FieldSchema(
                    name="embedding",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=3072,
                ),
                FieldSchema(
                    name="kid",
                    dtype=DataType.VARCHAR,
                    max_length=65535,
                ),
            ]

            schema = CollectionSchema(
                fields=fields,
                description="text embeddings collection",
                enable_dynamic_field=True,
            )

            collection = Collection(
                name=collection_name, schema=schema, using="default"
            )

            print("Creating indexes...")
            index_params = {
                "metric_type": "IP",
                "index_type": "FLAT",
                "params": {},
            }
            collection.create_index(
                field_name="embedding",
                index_params=index_params,
            )
            print("Successfully created collection and indexes")
            return collection
        else:
            print(f"Using existing collection: {collection_name}")
            return Collection(collection_name)
    except Exception as e:
        print(f"Error creating/accessing collection: {str(e)}")
        raise


if __name__ == "__main__":
    print(f"Connecting to Milvus at {MILVUS_HOST}:{MILVUS_PORT}...")
    connections.connect(
        "default",
        uri=os.getenv("MILVUS_URI", f"http://{MILVUS_HOST}:{MILVUS_PORT}"),
        token=os.getenv("MILVUS_TOKEN"),
    )
    print("Connected.")
    collection = create_collection("agent_backend")
    print("Collection is ready.")
