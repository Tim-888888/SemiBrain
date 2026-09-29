"""Verify and then stop an isolated cold_recovery.py rehearsal, never live storage."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from cold_recovery import authority_code, inspect, private_json, run

BUSINESS_CHECK = r'''
import json
from fastapi import HTTPException
from semibrain_business import knowledge, retrieval, graph
from semibrain_business.security import db, authorized_document
from semibrain_common.runtime import redis, uid
b=db();redis().ping()
document=None
for row in b.documents.find({'active_version':{'$ne':None},'revoked':False,'visibility':'demo','kind':{'$ne':'wiki'}}):
 chunks=list(b.chunks.find({'document_id':row['_id'],'version':row['active_version']}))
 version=b.document_versions.find_one({'_id':row['active_version']})
 if 1 <= len(chunks) <= 8 and version.get('snapshot_asset_id') and not version.get('image_refs'):document=row;break
assert document,'No small published document available'
claim={'subject_id':document['owner_id'],'role':'admin','resource_ids':['demo'],'document_ids':[document['_id']]}
authorized_document(document['_id'],claim,active=True,version=document['active_version'])
assets=[document['raw_asset_id']]
assets += [r['asset_id'] for c in chunks for r in c.get('image_refs',[]) if r.get('asset_id')]
assets += [r['_id'] for r in b.assets.find({'report_export_id':{'$exists':True}}).limit(3)]
checked=[]
for key in dict.fromkeys(assets):
 row=b.assets.find_one({'_id':key})
 if row:
  raw=knowledge.read_asset(row);checked.append({'asset_id':key,'bytes':len(raw),'hash_verified':True})
# Private documents must not become public merely because storage was restored.
private=b.documents.find_one({'visibility':'private','revoked':False})
assert private
try:authorized_document(private['_id'],{'subject_id':'other-user','role':'user','resource_ids':['demo']})
except HTTPException as exc:assert exc.status_code==403
else:raise AssertionError('Private restored document exposed')
# Delete only a representative projection from this isolated network. Rebuild
# from restored Mongo text with the existing authorized embedding provider.
ids=[c['_id'] for c in chunks]
v=retrieval.vectors()
assert len(v.get(retrieval.COLLECTION,ids=ids))==len(ids)
v.delete(retrieval.COLLECTION,ids=ids)
v.flush(retrieval.COLLECTION)
assert not v.get(retrieval.COLLECTION,ids=ids,consistency_level='Strong')
retrieval.index_chunks(document,document['active_version'],chunks)
assert len(v.get(retrieval.COLLECTION,ids=ids))==len(ids)
evidence,trace=retrieval.search(chunks[0]['text'][:150],claim,top_k=3)
assert evidence and all(r['document_id']==document['_id'] for r in evidence)
# Inject one vector-write failure only in this isolated process and a new job.
# The same worker persistence path must preserve the old active version.
from pymongo import ReturnDocument
job_id,new_version=uid(),uid()
b.ingestion_jobs.insert_one({'_id':job_id,'document_id':document['_id'],'asset_id':document['raw_asset_id'],
 'edited_snapshot_id':version['snapshot_asset_id'],'version':new_version,'status':'queued','attempt':0,
 'generation':document['revision'],'allow_external':False})
def own_admission(database,collection,query,update):
 return database[collection].find_one_and_update({'$and':[query,{'_id':job_id}]},update,return_document=ReturnDocument.AFTER)
index_failure_reached=[]
def fail_index(*args):
 index_failure_reached.append(True)
 raise RuntimeError('RECOVERY_PROBE_VECTOR_OUTAGE')
original_admission,original_index=knowledge.admission,knowledge.index_chunks
try:
 knowledge.admission,knowledge.index_chunks=own_admission,fail_index
 knowledge.process_one()
finally:knowledge.admission,knowledge.index_chunks=original_admission,original_index
assert index_failure_reached
assert b.ingestion_jobs.find_one({'_id':job_id})['status']=='failed'
assert b.documents.find_one({'_id':document['_id']})['active_version']==document['active_version']
assert not b.document_versions.find_one({'_id':new_version}).get('projection_verified')
old_evidence,_=retrieval.search(chunks[0]['text'][:150],claim,top_k=3)
assert old_evidence and all(r['version']==document['active_version'] for r in old_evidence)
rebuilt=graph.rebuild_projection()
print(json.dumps({'assets':checked,'private_acl_denied':True,'redis_authenticated':True,
 'vector_rebuilt_document':document['_id'],'vector_rebuilt_chunks':len(chunks),'retrieval_count':len(evidence),
 'graph_rebuild':rebuilt,'vector_failure_preserves_old_version':True,
 'scope':'full cold snapshot restore, one document re-embedded, full graph topology rebuilt'}))
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backup',type=Path,required=True)
    args=parser.parse_args()
    root=args.backup.resolve()
    if not root.is_relative_to('/data/semibrain/backups'):
        raise SystemExit('Unexpected backup directory')
    restored=json.loads((root/'restore.json').read_text())
    deployment=json.loads((root/'deployment.private.json').read_text())
    expected=json.loads((root/'authority.json').read_text())
    network=restored['network']
    if not network.startswith('semibrain-recovery-'):
        raise SystemExit('Refusing a non-rehearsal network')
    for identifier in restored['containers'].values():
        row=inspect(identifier)
        assert set(row['NetworkSettings']['Networks'])=={network}
        assert not row['HostConfig']['PortBindings']
        assert all(Path(m['Source']).resolve().is_relative_to(root/'restore') for m in row['Mounts'])
    started=time.monotonic()
    verified={}
    def execute(service,source,timeout=120):
        row=deployment['applications'][service]
        return run(['docker','run','--rm','--network',network,'--env-file',str(root/(service+'.env')),
            '--read-only','--tmpfs','/tmp:size=256m,mode=1777','--memory','1g',row['Image'],'python','-c',source],timeout=timeout)
    try:
        for service in ('conversation','agent','business'):
            deadline=time.monotonic()+90
            while True:
                try:
                    actual=json.loads(execute(service,authority_code(service)))
                    break
                except RuntimeError:
                    if time.monotonic()>=deadline:
                        raise
                    time.sleep(3)
            assert actual==expected[service], 'Authoritative snapshot mismatch: '+service
            verified[service]={key:row['count'] for key,row in actual.items()}
            print(json.dumps({'event':'authority_verified','service':service,'collections':len(actual)}),flush=True)
        checks=json.loads(execute('business',BUSINESS_CHECK,timeout=180))
        finished=datetime.now(timezone.utc)
        result={'verified':True,'snapshot_at':restored['snapshot_at'],'verified_at':finished.isoformat(),
            'post_start_verification_elapsed_seconds':(finished-datetime.fromisoformat(restored['started_at'])).total_seconds(),
            'snapshot_to_verification_seconds':(finished-datetime.fromisoformat(restored['snapshot_at'])).total_seconds(),
            'verification_seconds':round(time.monotonic()-started,2),'authority_counts':verified,'checks':checks,
            'rpo_scope':'cold snapshot: no accepted write between authority hashes and cold copy; not a daily backup SLA',
            'failure_domain':'separate Docker containers, paths and network on the same ECS; not host-loss recovery',
            'off_host_copy':False}
        private_json(root/'verified.json',result)
        print(json.dumps(result),flush=True)
    finally:
        # Keep the backup and recovered files for inspection, release all test RAM.
        run(['docker','stop','--time','20',*restored['containers'].values()],timeout=100)
        print(json.dumps({'event':'isolated_recovery_containers_stopped','live_services_untouched':True}),flush=True)


if __name__=='__main__':
    main()
