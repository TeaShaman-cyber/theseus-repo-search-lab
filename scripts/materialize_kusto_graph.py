#!/usr/bin/env python3
"""Materialize a verified local accepted projection. Run only after ownership receipt."""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.kusto_query import KustoClient, tables
from theseus_repo_search.projection import projection_fingerprint


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--fingerprint", required=True)
    args = p.parse_args()
    item = next(c for c in load_accepted_catalog(args.catalog).corpora if c.source_id == args.source)
    if projection_fingerprint(args.db) != args.fingerprint:
        raise ValueError("local projection fingerprint mismatch")
    with sqlite3.connect(args.db.as_uri()+"?mode=ro", uri=True) as conn:
        meta = dict(conn.execute("SELECT key,value FROM meta"))
        if meta["artifact_identity"] != item.artifact_identity:
            raise ValueError("artifact identity mismatch")
        nodes = conn.execute("SELECT id FROM nodes ORDER BY id").fetchall()
        edges = conn.execute("SELECT source_id,target_id,relation,evidence_grade,producer FROM edges ORDER BY source_id,target_id,relation,producer").fetchall()
    ids = {r[0] for r in nodes}
    if len(ids) != len(nodes) or any(r[0] not in ids or r[1] not in ids for r in edges):
        raise ValueError("invalid graph endpoints")
    client = KustoClient(os.environ["REPO_SEARCH_KUSTO_CLUSTER"], os.environ["REPO_SEARCH_KUSTO_DATABASE"])
    n, e, r = tables(item)

    def mg(kql):
        req = urllib.request.Request(client.cluster+"/v1/rest/mgmt",
            data=json.dumps({"db":client.database,"csl":kql}).encode(),
            headers={"Authorization":"Bearer "+client._token,"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=60) as resp:
            data=json.load(resp)
        if "Tables" not in data or data.get("error"):
            raise RuntimeError("management failed")
        return data["Tables"][0]["Rows"]

    def ingest(table, rows):
        buf=io.StringIO(newline="")
        csv.writer(buf).writerows(rows)
        payload=gzip.compress(buf.getvalue().encode(),compresslevel=3,mtime=0)
        if len(payload)>4*1024*1024:
            raise ValueError("ingest batch too large")
        req=urllib.request.Request(client.cluster+"/v1/rest/ingest/"+client.database+"/"+table+"?streamFormat=Csv",
            data=payload,headers={"Authorization":"Bearer "+client._token,"Content-Encoding":"gzip","Content-Type":"application/octet-stream"})
        with urllib.request.urlopen(req,timeout=90) as resp:
            data=json.load(resp)
        if data.get("error"):
            raise RuntimeError("ingest failure")
        # The readback below, not the HTTP response, is the acceptance gate.

    if any(row[0] in (n,e,r) for row in mg(".show tables")):
        raise RuntimeError("existing projection: reconcile, do not overwrite")
    mg(f".create table {n} (id:string,batch:long)")
    mg(f".create table {e} (source_id:string,target_id:string,relation:string,evidence_grade:string,producer:string,batch:long)")
    mg(f".create table {r} (artifact_identity:string,source_revision:string,dependency_boundary:string,node_count:long,edge_count:long)")
    for table in (n,e,r):
        mg(f".alter table {table} policy streamingingestion enable")
    for table, rows, cols in ((n,nodes,"id"),(e,edges,"source_id,target_id,relation,evidence_grade,producer")):
        for i in range(0,len(rows),5000):
            batch=i//5000
            expected=rows[i:i+5000]
            ingest(table,[(*row,batch) for row in expected])
            actual=client.query(f"{table} | where batch == {batch} | project {cols}")
            if sorted(map(tuple,actual))!=sorted(expected):
                raise RuntimeError(f"exact row parity failed for {table} batch {batch}")
        if client.query(f"{table} | count") != [[len(rows)]]:
            raise RuntimeError("count mismatch")
        print("EXACT_PARITY",table,len(rows),flush=True)
    ready=[item.artifact_identity,meta["source_commit"],meta["dependency_boundary"],len(nodes),len(edges)]
    ingest(r,[ready])
    if client.query(f"{r} | project artifact_identity,source_revision,dependency_boundary,node_count,edge_count") != [ready]:
        raise RuntimeError("ready readback failed")
    print("READY",item.source_id,item.artifact_identity,flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
