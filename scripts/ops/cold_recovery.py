"""Cold backup and isolated storage recovery rehearsal on a Docker demo host.

Run explicitly during a maintenance window. No scheduler and no automatic restore
over live paths. Private manifests contain deployment configuration and MUST remain
outside Git and outside web roots. An isolated restore never starts any worker.
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

STORAGE = ("mongo", "redis", "postgres", "minio", "etcd", "milvus", "neo4j")
APPLICATION = ("web", "conversation", "conversation-worker", "agent", "agent-worker", "business", "business-worker")
AUTHORITY = {
    'conversation': ['users','conversations','messages','gateway_runs'],
    'agent': ['runs','reports','model_calls','tool_calls','memories','agent_configuration','agent_configuration_versions','evaluations'],
    'business': ['documents','document_versions','chunks','assets','graph_edges','graph_entities','skills','skill_versions','mcp_settings'],
}


def authority_code(service):
    code = ("import hashlib,json;from bson import BSON;from semibrain_common.runtime import database;"
        f"d=database({service!r});out={{}}\n"
        f"for name in {AUTHORITY[service]!r}:\n"
        " h=hashlib.sha256();count=0\n"
        " for row in d[name].find({}).sort('_id',1):h.update(BSON.encode(row));count+=1\n"
        " out[name]={'count':count,'sha256':h.hexdigest()}\n")
    if service == 'business':
        code += ("import os;from sqlalchemy import select;from semibrain_business.warehouse import metadata,engine_from_url\n"
            "with engine_from_url(os.environ['SEMIBRAIN_WAREHOUSE_READ_URL']).connect() as connection:\n"
            " for table in metadata.sorted_tables:\n"
            "  h=hashlib.sha256();count=0\n"
            "  for row in connection.execute(select(table).order_by(*table.primary_key.columns)).mappings():\n"
            "   h.update(json.dumps(dict(row),sort_keys=True,default=str).encode());count+=1\n"
            "  out['postgres:'+table.name]={'count':count,'sha256':h.hexdigest()}\n")
    return code + "print(json.dumps(out))"


def run(args, *, text=True, data=None, timeout=180):
    result = subprocess.run(args, input=data, capture_output=True, text=text, timeout=timeout)
    if result.returncode:
        # Docker errors can echo environment values. Keep them in the operator's
        # private terminal only when explicitly inspecting, not public evidence.
        raise RuntimeError(f"Command failed: {args[0]} (exit {result.returncode})")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def source_paths(containers):
    paths = set()
    for container in containers:
        for mount in container["Mounts"]:
            source = Path(mount["Source"]).resolve()
            if mount["Type"] != "bind" or not any(source.is_relative_to(root) for root in (Path('/data/semibrain'), Path('/srv/semibrain'))):
                raise RuntimeError("Unsupported backup mount; inspect it before proceeding")
            paths.add(source)
    # Avoid archiving a file twice when a parent directory is already included.
    return sorted(p for p in paths if not any(p != q and p.is_relative_to(q) for q in paths))


def live_exec(prefix, service, source):
    return run(["docker", "exec", "-i", f"{prefix}-{service}-1", "python", "-"], data=source, timeout=120)


def application_check(row, env, source, timeout=90):
    networks=list(row['NetworkSettings']['Networks'])
    identifier=run(['docker','create','--network',networks[0],'--env-file',str(env),
        '--read-only','--tmpfs','/tmp:size=128m,mode=1777',row['Image'],'python','-c',source]).strip()
    try:
        for network in networks[1:]:
            run(['docker','network','connect',network,identifier])
        output=run(['docker','start','--attach',identifier],timeout=timeout)
        if inspect(identifier)['State']['ExitCode']:
            raise RuntimeError('Authoritative snapshot check failed')
        return output
    finally:
        run(['docker','rm','--force',identifier])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="semibrain-platform")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maintenance-confirmed", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.output.resolve()
    if not output.is_relative_to('/data/semibrain/backups') or output.exists():
        raise SystemExit("Use a new private directory under /data/semibrain/backups")
    output.mkdir(parents=True, mode=0o700)
    prefix = args.prefix
    storage = {name: inspect(f'{prefix}-{name}-1') for name in STORAGE}
    apps = {name: inspect(f'{prefix}-{name}-1') for name in APPLICATION}
    if not all(row['State']['Running'] for row in [*storage.values(), *apps.values()]):
        raise RuntimeError('All current services must be running before rehearsal')
    live_exec(prefix, 'agent', "from semibrain_common.runtime import database;assert not database('agent').runs.count_documents({'status':{'$in':['dispatching','queued','running','retrying','waiting_input','cancelling']}})")
    live_exec(prefix, 'business', "from semibrain_common.runtime import database;b=database('business');assert not sum(b[c].count_documents({'status':{'$in':['queued','running','receiving','cancelling']}}) for c in ['ingestion_jobs','tool_jobs','report_exports'])")
    sources = source_paths(storage.values())
    private_json(output/'deployment.private.json', {'storage': storage, 'applications': apps})
    snapshot_at = None
    started = time.monotonic()
    try:
        # Stop ingress first; no newly accepted work can cross the snapshot fence.
        for group in (['web','conversation'], ['conversation-worker','agent-worker','business-worker'], ['agent','business']):
            names = [apps[name]['Id'] for name in group]
            run(['docker','stop','--time','25',*names], timeout=100)
        # Recheck accepted work after closing ingress. If busy, restore services
        # without taking a backup; no inflight external job is silently copied.
        agent = apps['agent']
        env = output/'check.env'
        env.write_text('\n'.join(agent['Config']['Env'])+'\n')
        network = next(iter(agent['NetworkSettings']['Networks']))
        run(['docker','run','--rm','--network',network,'--env-file',str(env),agent['Image'],'python','-c',
             "from semibrain_common.runtime import database;assert not database('agent').runs.count_documents({'status':{'$in':['queued','running','retrying','cancelling']}})"], timeout=30)
        env.unlink()
        reference = {}
        for service in ('conversation','agent','business'):
            row = apps[service]
            env = output/(service+'.env')
            env.write_text('\n'.join(row['Config']['Env'])+'\n')
            if service == 'business':
                application_check(row, env, "from semibrain_common.runtime import database;b=database('business');assert not sum(b[c].count_documents({'status':{'$in':['queued','running','receiving','cancelling']}}) for c in ['ingestion_jobs','tool_jobs','report_exports'])", timeout=30)
            reference[service] = json.loads(application_check(row, env, authority_code(service)))
        private_json(output/'authority.json', reference)
        names = [storage[name]['Id'] for name in ('milvus','neo4j','postgres','mongo','redis','etcd','minio')]
        run(['docker','stop','--time','30',*names], timeout=100)
        snapshot_at = datetime.now(timezone.utc).isoformat()
        archive = output/'storage.tar.gz'
        run(['tar','--numeric-owner','-czf',str(archive),'-C','/',*[str(p).lstrip('/') for p in sources]], timeout=300)
        archive.chmod(0o600)
        checksum = sha(archive)
        private_json(output/'backup.json', {'snapshot_at':snapshot_at,'archive_sha256':checksum,'archive_bytes':archive.stat().st_size,
            'images':{k:v['Image'] for k,v in storage.items()},'paths':[str(p) for p in sources],
            'consistency':'cold_storage_after_ingress_and_workers_stopped','off_host_copy':False})
    finally:
        # Never leave the live demo stopped if archive creation fails.
        restart_errors = []
        for group in (STORAGE, ('business','agent','conversation'), ('business-worker','agent-worker','conversation-worker','web')):
            names = [(storage if name in STORAGE else apps)[name]['Id'] for name in group]
            try:
                run(['docker','start',*names], timeout=120)
            except RuntimeError:
                restart_errors.append(group)
        if restart_errors:
            raise RuntimeError('Live services require operator restart: '+repr(restart_errors))
    print(json.dumps({'event':'live_services_restarted','maintenance_seconds':round(time.monotonic()-started,2),'snapshot_at':snapshot_at}),flush=True)
    if not snapshot_at or not (output/'backup.json').exists():
        raise RuntimeError('Backup did not finish')
    recovered = output/'restore'
    recovered.mkdir(mode=0o700)
    assert sha(archive) == checksum
    run(['tar','-xzf',str(archive),'-C',str(recovered)],timeout=300)
    label = 'semibrain-recovery-'+str(int(time.time()))
    run(['docker','network','create',label])
    restored = {}
    for name,row in storage.items():
        env = output/(name+'.env')
        env.write_text('\n'.join(row['Config']['Env'])+'\n')
        env.chmod(0o600)
        command=['docker','create','--name',label+'-'+name,'--network',label,'--network-alias',name,
            '--restart','no','--memory',str(row['HostConfig']['Memory'] or 1024**3),'--env-file',str(env)]
        for mount in row['Mounts']:
            cloned = recovered / mount['Source'].lstrip('/')
            assert cloned.resolve().is_relative_to(recovered) and cloned.exists()
            command += ['--mount','type=bind,src='+str(cloned)+',dst='+mount['Destination']+('' if mount['RW'] else ',readonly')]
        if row['Config'].get('User'):
            command += ['--user',row['Config']['User']]
        # All pinned storage images retain their vendor entrypoints.
        image = json.loads(run(['docker','image','inspect',row['Image']]))[0]
        assert row['Config'].get('Entrypoint') == image['Config'].get('Entrypoint')
        command += [row['Image'],*(row['Config'].get('Cmd') or [])]
        restored[name] = run(command).strip()
    run(['docker','start',*restored.values()],timeout=120)
    private_json(output/'restore.json',{'network':label,'containers':restored,'restore_root':str(recovered),
        'snapshot_at':snapshot_at,'started_at':datetime.now(timezone.utc).isoformat(),'verified':False})
    print(json.dumps({'event':'isolated_storage_started','network':label,'backup_directory':str(output),
        'ports_published':0,'workers_started':0,'verified':False}),flush=True)


if __name__ == '__main__':
    main()
