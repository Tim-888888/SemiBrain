"""Neo4j contains opaque topology only. Mongo authorizes every returned edge."""

import os
from functools import lru_cache

from neo4j import GraphDatabase, Query, unit_of_work


def configured():
    return bool(os.getenv("SEMIBRAIN_NEO4J_URI") and os.getenv("SEMIBRAIN_NEO4J_PASSWORD"))


@lru_cache(maxsize=1)
def driver():
    return GraphDatabase.driver(
        os.environ["SEMIBRAIN_NEO4J_URI"],
        auth=(os.getenv("SEMIBRAIN_NEO4J_USER", "neo4j"), os.environ["SEMIBRAIN_NEO4J_PASSWORD"]),
        connection_timeout=4,
        connection_acquisition_timeout=4,
        max_transaction_retry_time=3,
        max_connection_pool_size=4,
    )


def rebuild(rows, revision):
    @unit_of_work(timeout=30)
    def write(tx):
        tx.run("MATCH ()-[r:SB_REL]->() DELETE r").consume()
        tx.run("MATCH (n:SBEntity) WHERE NOT (n)--() DELETE n").consume()
        tx.run(
            """UNWIND $rows AS row
            MERGE (a:SBEntity {id:row.subject_id}) MERGE (b:SBEntity {id:row.object_id})
            CREATE (a)-[:SB_REL {id:row.id, revision:$revision}]->(b)""",
            rows=rows,
            revision=revision,
        ).consume()

    with driver().session(database="neo4j") as session:
        session.run(Query("CREATE CONSTRAINT sb_entity_id IF NOT EXISTS FOR (n:SBEntity) REQUIRE n.id IS UNIQUE", timeout=10)).consume()
        session.execute_write(write)


def traverse(seeds, allowed, revision, depth):
    # depth is a validated integer, never user Cypher. No arbitrary query endpoint.
    if depth not in (1, 2):
        raise ValueError("GRAPH_DEPTH_INVALID")
    statement = (
        """MATCH p=(n:SBEntity)-[:SB_REL*1.."""
        + str(depth)
        + """ ]-(m:SBEntity)
        WHERE n.id IN $seeds AND all(r IN relationships(p) WHERE r.id IN $allowed AND r.revision=$revision)
        RETURN [r IN relationships(p) | r.id] AS edge_ids LIMIT 30"""
    )
    with driver().session(database="neo4j", default_access_mode="READ") as session:
        return [
            list(row["edge_ids"])
            for row in session.run(
                Query(statement, timeout=5), seeds=seeds, allowed=allowed, revision=revision
            )
        ]
