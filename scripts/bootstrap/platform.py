"""Explicit, private bootstrap job; never run inside an online API container."""

import json
import os
import time
from datetime import UTC, datetime

from minio import Minio, MinioAdmin
from minio.credentials import StaticProvider
from pymilvus import MilvusClient
from pymongo import MongoClient
from sqlalchemy import create_engine, text


def initialize_mongo():
    uri = (
        "mongodb://bootstrap:"
        + os.environ["MONGO_OWNER"]
        + "@mongo:27017/admin?directConnection=true"
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("replSetGetStatus")
    except Exception as exc:
        if getattr(exc, "code", None) != 94:
            raise
        client.admin.command(
            "replSetInitiate", {"_id": "rs0", "members": [{"_id": 0, "host": "mongo:27017"}]}
        )
    for _ in range(60):
        if client.admin.command("hello").get("isWritablePrimary"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("REPLICA_SET_NOT_READY")
    for service in ("conversation", "agent", "business"):
        db = client[service + "_db"]
        if not db.command("usersInfo", service)["users"]:
            db.command(
                "createUser",
                service,
                pwd=os.environ[service.upper() + "_MONGO"],
                roles=[{"role": "readWrite", "db": db.name}],
            )
    client.close()
    print("Mongo replica set and three database-scoped users ready", flush=True)


def initialize_postgres():
    from semibrain_business.warehouse import generate, load_dataset

    engine = create_engine(
        "postgresql+psycopg://bootstrap:" + os.environ["PG_OWNER"] + "@postgres:5432/semibrain_demo"
    )
    data = generate(
        seed=921001, prefix="DEMO-B", start=datetime(2026, 9, 1, tzinfo=UTC), lot_count=12
    )
    load_dataset(engine, data)
    with engine.begin() as connection:
        # Password is generated hex and passed as a bound value to quote_literal, never logged.
        if not connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname='business_reader'")
        ).scalar():
            encoded = connection.execute(
                text("SELECT quote_literal(:password)"), {"password": os.environ["PG_READER"]}
            ).scalar_one()
            connection.exec_driver_sql("CREATE ROLE business_reader LOGIN PASSWORD " + encoded)
        connection.exec_driver_sql("GRANT CONNECT ON DATABASE semibrain_demo TO business_reader")
        connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO business_reader")
        connection.exec_driver_sql("GRANT SELECT ON ALL TABLES IN SCHEMA public TO business_reader")
        connection.exec_driver_sql(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO business_reader"
        )
        connection.exec_driver_sql(
            "ALTER ROLE business_reader SET default_transaction_read_only=on"
        )
        for table, column in [
            ("test_results", "event_time"),
            ("process_events", "started_at"),
            ("defect_records", "event_time"),
        ]:
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS b_scope_{table} ON {table}(lot_id,{column})"
            )
    print("Synthetic PostgreSQL tables and SELECT-only role ready", flush=True)


def initialize_objects():
    password = os.environ["MINIO_OWNER"]
    client = Minio("minio:9000", access_key="bootstrap", secret_key=password, secure=False)
    admin = MinioAdmin(
        endpoint="minio:9000", credentials=StaticProvider("bootstrap", password), secure=False
    )
    for name, user, secret_name in [
        ("knowledge-assets", "business_assets", "MINIO_BUSINESS"),
        ("milvus-projection", "milvus_storage", "MINIO_MILVUS"),
    ]:
        if not client.bucket_exists(name):
            client.make_bucket(name)
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetBucketLocation",
                        "s3:ListBucket",
                        "s3:ListBucketMultipartUploads",
                    ],
                    "Resource": ["arn:aws:s3:::" + name],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:AbortMultipartUpload",
                        "s3:ListMultipartUploadParts",
                    ],
                    "Resource": ["arn:aws:s3:::" + name + "/*"],
                },
            ],
        }
        users = json.loads(admin.user_list())
        if user not in users:
            admin.user_add(user, os.environ[secret_name])
        admin.policy_add(user, policy=policy)
        admin.policy_set(user, user=user)
    print("Private MinIO buckets and separate scoped users ready", flush=True)


def initialize_vectors():
    password = os.environ["MILVUS_OWNER"]
    try:
        client = MilvusClient(uri="http://milvus:19530", token="root:" + password, timeout=8)
        client.list_users()
    except Exception:
        client = MilvusClient(uri="http://milvus:19530", token="root:Milvus", timeout=10)
        client.update_password("root", "Milvus", password)
        client.close()
        client = MilvusClient(uri="http://milvus:19530", token="root:" + password, timeout=10)
    from semibrain_business.retrieval import COLLECTION, initialize

    os.environ["SEMIBRAIN_MILVUS_URI"] = "http://milvus:19530"
    os.environ["SEMIBRAIN_MILVUS_TOKEN"] = "root:" + password
    initialize()
    if "business_projection" not in client.list_users():
        client.create_user("business_projection", os.environ["MILVUS_BUSINESS"])
    if "knowledge_projection" not in client.list_roles():
        client.create_role("knowledge_projection")
    for privilege in (
        "Load",
        "GetLoadingProgress",
        "GetLoadState",
        "CreateIndex",
        "IndexDetail",
        "Search",
        "Query",
        "Insert",
        "Upsert",
        "Delete",
        "Flush",
        "GetStatistics",
    ):
        client.grant_privilege("knowledge_projection", "Collection", privilege, COLLECTION)
    # Milvus classifies metadata inspection as Global; content access stays collection-scoped.
    for privilege in ("DescribeCollection", "ShowCollections"):
        client.grant_privilege("knowledge_projection", "Global", privilege, "*")
    client.grant_role("business_projection", "knowledge_projection")
    print("Milvus projection collection and scoped user ready", flush=True)


if __name__ == "__main__":
    initialize_mongo()
    initialize_postgres()
    initialize_objects()
    initialize_vectors()
