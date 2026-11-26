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
import urllib.error
import urllib.request
from pathlib import Path

from theseus_repo_search.accepted_catalog import load_accepted_catalog
from theseus_repo_search.kusto_query import READY_COLUMNS, KustoClient, tables
from theseus_repo_search.projection import (
    projection_fingerprint,
    read_projection_provenance,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--fingerprint", required=True)
    p.add_argument("--verify-existing", action="store_true", help="verify existing owned graph before adding v2 ready metadata")
    p.add_argument("--resume-empty", action="store_true", help="resume explicitly reconciled empty owned tables")
    args = p.parse_args()
    if args.resume_empty and args.verify_existing:
        p.error("choose one reconciliation mode")
    item = next(c for c in load_accepted_catalog(args.catalog).corpora if c.source_id == args.source)
    if projection_fingerprint(args.db) != args.fingerprint:
        raise ValueError("local projection fingerprint mismatch")
    with sqlite3.connect(args.db.resolve().as_uri()+"?mode=ro", uri=True) as conn:
        provenance = read_projection_provenance(conn)
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

    # Larger graphs use the same streaming route already measured in #131.
    # Small graphs and metadata use inline ingestion.
    stream_tables: set[str] = set()

    def ingest(table, rows):
        if table in stream_tables:
            buf=io.StringIO(newline="")
            csv.writer(buf).writerows(rows)
            raw=buf.getvalue().encode()
            if len(raw) > 3*1024*1024:
                # Same batch key, smaller transport chunks; verify whole batch.
                if len(rows) == 1:
                    raise ValueError("oversized streaming row")
                mid=len(rows)//2
                ingest(table,rows[:mid])
                ingest(table,rows[mid:])
                return
            req=urllib.request.Request(client.cluster+"/v1/rest/ingest/"+client.database+"/"+table+"?streamFormat=Csv",
                data=gzip.compress(raw,compresslevel=3,mtime=0),
                headers={"Authorization":"Bearer "+client._token,"Content-Encoding":"gzip","Content-Type":"application/octet-stream"})
            try:
                with urllib.request.urlopen(req,timeout=90) as resp:
                    data=json.load(resp)
                if data.get("error"):
                    raise RuntimeError("ingest failure")
                return
            except urllib.error.HTTPError as exc:
                detail=exc.read(2048).decode("utf-8",errors="replace")
                # Fall back only on a definite gateway rejection, with no rows
                # for the affected batch; never replay an ambiguous write.
                if (exc.code != 400 or "BadRequest_EntityNotFound" not in detail
                        or client.query(f"{table} | where batch == {rows[0][-1]} | count") != [[0]]):
                    raise
                print("INLINE_FALLBACK",table,flush=True)
                stream_tables.remove(table)
        buf=io.StringIO(newline="")
        size=0
        for row in rows:
            line=io.StringIO(newline="")
            csv.writer(line).writerow(row)
            value=line.getvalue()
            length=len(value.encode())
            if length > 512*1024:
                raise ValueError("inline row exceeds bounded ingestion size")
            if size+length > 512*1024:
                mg(f".ingest inline into table {table} <|\n"+buf.getvalue())
                buf=io.StringIO(newline="")
                size=0
            buf.write(value)
            size+=length
        if size:
            mg(f".ingest inline into table {table} <|\n"+buf.getvalue())

    existing = {row[0] for row in mg(".show tables")} & {n,e,r}
    if args.verify_existing:
        if not {n,e} <= existing or (r in existing and client.query(f"{r} | count") != [[0]]):
            raise RuntimeError("expected exact owned node/edge tables without nonempty v2 ready")
    elif args.resume_empty:
        if existing != {n,e} or any(client.query(f"{table} | count") != [[0]] for table in (n,e)):
            raise RuntimeError("expected two owned empty tables")
    else:
        if existing:
            raise RuntimeError("existing projection: reconcile, do not overwrite")
        mg(f".create table {n} (id:string,batch:long)")
        mg(f".create table {e} (source_id:string,target_id:string,relation:string,evidence_grade:string,producer:string,batch:long)")
    large = len(edges)>100000
    if large and not args.verify_existing:
        for table in (n,e):
            mg(f".alter table {table} policy streamingingestion enable")
            try:
                cleared=mg(f".clear table {table} cache streamingingestion schema")
            except urllib.error.HTTPError as exc:
                if exc.code not in (400,403):
                    raise
                cleared=[]
            if cleared and all(row[-1]=="Succeeded" for row in cleared):
                stream_tables.add(table)
                print("STREAM_READY",table,flush=True)
            else:
                print("INLINE_FALLBACK_SCHEMA_CACHE",table,flush=True)
    batch_size=20000 if large else 5000
    for table, rows, cols in ((n,nodes,"id"),(e,edges,"source_id,target_id,relation,evidence_grade,producer")):
        for i in range(0,len(rows),batch_size):
            batch=i//batch_size
            expected=rows[i:i+batch_size]
            if not args.verify_existing:
                ingest(table,[(*row,batch) for row in expected])
            actual=client.query(f"{table} | where batch == {batch} | project {cols}")
            if sorted(map(tuple,actual))!=sorted(expected):
                raise RuntimeError(f"exact row parity failed for {table} batch {batch}")
            if large:
                print("BATCH_PARITY",table,batch,len(expected),flush=True)
        if client.query(f"{table} | count") != [[len(rows)]]:
            raise RuntimeError("count mismatch")
        print("EXACT_PARITY",table,len(rows),flush=True)
    if r not in existing:
        mg(f".create table {r} (artifact_identity:string,source_revision:string,dependency_boundary:string,node_count:long,edge_count:long,root_modules:string,source_kind:string,source_authority:string,created_from_authoritative_source:bool,producer_kind:string)")
    ready=[item.artifact_identity,provenance.source_revision,meta["dependency_boundary"],len(nodes),len(edges),
           meta["root_modules"],provenance.source_kind,json.dumps(provenance.source_authority,ensure_ascii=False,separators=(",",":")),
           provenance.created_from_authoritative_source,meta["producer.kind"]]
    ingest(r,[ready])
    if client.query(f"{r} | project {READY_COLUMNS}") != [ready]:
        raise RuntimeError("ready readback failed")
    print("READY",item.source_id,item.artifact_identity,flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
