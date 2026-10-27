"""Actual 5d974b1 checkout/updater, real Git/SQLite/Alembic/Uvicorn.

Fixed paths, OS credentials, package/build commands and systemctl observations
are redirected in private candidate blobs. No product CLI bypass. This is NOT
proof of systemd/root/sandbox/reboot on Debian (9C). The production baseline
needs real Git history; the later v1 runtime uses hash-pinned public fixtures.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from nginx_provision_test import p
from split_update_test import database_rows
from historical_fixtures import BASE, SHA256, historical_file, require_baseline_history

ROOT = p.ROOT


def command(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, **kwargs).stdout


@pytest.fixture
def first(tmp_path):
    base = tmp_path
    app, release, remote = (base / n for n in ('app', 'release', 'remote.git'))
    require_baseline_history(ROOT)
    command(['git', 'clone', '-q', '--no-hardlinks', ROOT, app])
    # A shallow source can hold BASE under a test-only ref, outside clone's refs.
    command(['git', '-C', app, 'fetch', '-q', '--no-tags', '--depth=2', '--update-shallow', ROOT, BASE])
    command(['git', '-C', app, 'checkout', '-q', '--detach', BASE])
    assert command(['git', '-C', app, 'rev-parse', 'HEAD']).decode().strip() == BASE
    app.chmod(0o755); (app / '.git').chmod(0o755)
    for name in ('config','HEAD','index'): (app / '.git' / name).chmod(0o644)
    assert not (app / 'deploy/update_safety.py').exists()
    # Seed the test origin from BASE, not the invoking checkout's branch refs.
    command(['git', 'init', '-q', '--bare', '--initial-branch=main', remote])
    command(['git', '-C', remote, 'fetch', '-q', '--no-tags', '--depth=2', app, BASE+':refs/heads/main'])
    command(['git', '-C', app, 'remote', 'set-url', 'origin', remote])
    command(['git', 'clone', '-q', remote, release])
    command(['git', '-C', release, 'checkout', '-qb', 'candidate', BASE])
    command(['git', '-C', release, 'config', 'user.name', 'Upgrade Test'])
    command(['git', '-C', release, 'config', 'user.email', 'upgrade@example.invalid'])
    data, config, systemd, backups, binary = (base / n for n in ('data', 'config', 'systemd', 'backups', 'bin'))
    for path in (data, config, systemd, backups, binary): path.mkdir(mode=0o700)
    database, env, unit, guard, lock = data / 'work_tracker.db', config / 'work-tracker.env', systemd / 'work-tracker.service', systemd / 'work-tracker.service.d/90-work-tracker-update-safety.conf', base / 'deploy.lock'
    unit.write_bytes((app / 'deploy/work-tracker.service').read_bytes())
    unit.chmod(0o644)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = 'sqlite:///' + str(database)
    env.write_text(f'APP_HOST=0.0.0.0\nAPP_PORT={port}\nDATABASE_URL={url}\nWEBHOOK_TOKEN=public-test-token-at-least-32-characters\nBROWSER_ALLOWED_ORIGINS=http://127.0.0.1:{port}\nCORS_ORIGINS=\nSESSION_COOKIE_SECURE=false\nINGRESS_PROFILE=lan\n')
    env.chmod(0o640)
    original_env = env.read_bytes()
    command([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=app / 'backend', env=os.environ | {'DATABASE_URL': url})
    keeper = sqlite3.connect(database)
    keeper.execute('PRAGMA journal_mode=WAL')
    keeper.execute('PRAGMA wal_autocheckpoint=0')
    keeper.execute("INSERT INTO location_timezones VALUES ('office','UTC','2030-01-01T00:00:00+00:00')")
    keeper.execute("INSERT INTO users VALUES (1,'fixture_owner','public-test-hash','2030-01-01T00:00:00+00:00')")
    keeper.execute("INSERT INTO user_sessions VALUES (1,1,?,'2030-01-01T00:00:00+00:00','2030-01-01T00:00:00+00:00','2031-01-01T00:00:00+00:00',NULL)", ('b' * 64,))
    keeper.execute("INSERT INTO login_attempts (login_key,ip_key,attempted_at,failed_at) VALUES (?,'192.0.2.1/32','2030-01-01T00:00:00+00:00',NULL)", ('c' * 64,))
    keeper.commit()
    state = {'process': None, 'calls': [], 'failure': None, 'loaded': False, 'threads': [], 'starts': []}
    mapping = {'/opt/work-tracker': str(app), '/etc/work-tracker/work-tracker.env': str(env),
               '/var/lib/work-tracker/work_tracker.db': str(database), '/var/backups/work-tracker': str(backups),
               '/etc/systemd/system/work-tracker.service': str(unit), '/run/work-tracker-deploy.lock': str(lock),
               '/etc/work-tracker/update-safety.py': str(config / 'update-safety.py'),
               '/etc/work-tracker/update-state.json': str(config / 'update-state.json'),
               '/etc/work-tracker/ingress/cutover.json': str(config / 'ingress/cutover.json')}
    def redirected(text):
        for old, new in mapping.items(): text = text.replace(old, new)
        return text
    for path in (ROOT / 'deploy').glob('*'):
        if path.is_file():
            target = release / 'deploy' / path.name
            if path.suffix in ('.py', '.sh', '.manifest'):
                text = redirected(path.read_text())
                if path.name == 'first_upgrade.py':
                    text = text.replace("ORIGIN = 'https://github.com/llit47/work-tracker.git'", f'ORIGIN = {str(remote)!r}')
                    text = text.replace("SAFE_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'", f'SAFE_PATH = {str(binary) + ":" + os.environ["PATH"]!r}')
                    text = text.replace('info.st_uid == 0', 'info.st_uid in (0, os.getuid())').replace('os.fstat(fd).st_uid == 0', 'os.fstat(fd).st_uid == os.getuid()')
                    text = text.replace('\n        info = item.lstat()', f'\n        if item == Path({str(base.parent)!r}): break\n        info = item.lstat()')
                    text = text.replace('os.geteuid() == 0', 'os.geteuid() == os.getuid()')
                    text = text.replace("pwd.getpwnam('work-tracker')", 'pwd.getpwuid(os.getuid())').replace("grp.getgrnam('work-tracker')", 'grp.getgrgid(os.getgid())')
                    text = text.replace("        print('FIRST UPGRADE REFUSED", "        import traceback; traceback.print_exc()\n        print('FIRST UPGRADE REFUSED")
                if path.name in ('update_safety.py', 'nginx_provision.py'):
                    text = text.replace('uid = 0', 'uid = os.getuid()').replace("boundary = Path('/')", f'boundary = Path({str(base)!r})')
                    text = text.replace("grp.getgrnam('work-tracker').gr_gid", 'os.getgid()')
                if path.name == 'update_safety.py':
                    text = text.replace('os.geteuid() == 0', 'os.geteuid() == os.getuid()')
                    text = text.replace("pwd.getpwnam('work-tracker')", 'pwd.getpwuid(os.getuid())').replace("grp.getgrnam('work-tracker')", 'grp.getgrgid(os.getgid())')
                    text = text.replace("        print('UPDATE REQUIRES", "        import traceback; traceback.print_exc()\n        print('UPDATE REQUIRES")
                if path.suffix == '.py': compile(text, str(target), 'exec')
                target.write_text(text)
            else: shutil.copyfile(path, target)
    text = redirected((ROOT / 'update.sh').read_text()).replace('[[ "${EUID}" -eq 0 ]]', 'true')
    text = text.replace('s.st_uid==0', 's.st_uid in (0,os.getuid())')
    text = text.replace('  s=q.lstat();', f'  if q == pathlib.Path("{str(base.parent)}"): break\n  s=q.lstat();')
    text = text.replace('readonly SERVICE_USER="work-tracker"', f'readonly SERVICE_USER="{__import__("pwd").getpwuid(os.getuid()).pw_name}"')
    (release / 'update.sh').write_text(text)
    # Preserve the real historical unit blob/checkout; observed systemd paths
    # below are translated to the private layout, like the other OS adapters.
    entry = base / 'first-upgrade.py'
    entry.write_bytes((release / 'deploy/first_upgrade.py').read_bytes())
    spec = importlib.util.spec_from_file_location('private_provision', release / 'deploy/nginx_provision.py')
    observation = importlib.util.module_from_spec(spec); spec.loader.exec_module(observation)
    venv = app / 'backend/.venv/bin'
    venv.mkdir(parents=True)
    (venv / 'python').symlink_to(sys.executable)
    log = (base / 'backend.log').open('w')
    def parsed_env():
        return dict(line.split('=', 1) for line in env.read_text().splitlines())
    def stop():
        if state['process']:
            state['process'].terminate(); state['process'].wait(timeout=5); state['process'] = None
    def start():
        if guard.exists():
            checker = config / 'update-safety.py'
            result = subprocess.run([sys.executable, '-I', checker, 'allow-start'], capture_output=True)
            if result.returncode: return False
        state['starts'].append(command(['git', '-C', app, 'rev-parse', 'HEAD']).decode().strip())
        state['process'] = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host','0.0.0.0','--port',str(port),'--workers','1','--no-proxy-headers'],
            cwd=app / 'backend', env=os.environ | parsed_env(), stdout=log, stderr=log)
        return True
    def health():
        import http.client
        c = http.client.HTTPConnection('127.0.0.1',port,timeout=1)
        try: c.request('GET','/api/health'); return c.getresponse().status == 200
        except OSError: return False
        finally: c.close()
    def ready():
        deadline=time.monotonic()+5
        while not health():
            assert time.monotonic()<deadline
            time.sleep(.02)
    assert start(); ready()
    import http.client
    def ha(path, payload):
        c = http.client.HTTPConnection('127.0.0.1',port,timeout=3)
        c.request('POST','/api/webhook/home-assistant'+path,json.dumps(payload),{'Content-Type':'application/json','X-Webhook-Token':parsed_env()['WEBHOOK_TOKEN']})
        response=c.getresponse(); response.read(); c.close(); return response.status
    assert ha('',dict(event='entry',source='home_assistant',location='office',timestamp='2030-01-01T08:00:00Z'))==201
    assert ha('/correction',dict(raw_event_id=1,time='07:45'))==200
    original_rows = database_rows(database)
    for path in (database,Path(str(database)+'-wal'),Path(str(database)+'-shm')): path.chmod(0o640)
    sock_path = base / 'control.sock'
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request=json.loads(self.rfile.readline()); kind,args=request['kind'],request['args']
            state['calls'].append((kind,args)); rc=0; output=''
            try:
                if kind=='systemctl':
                    if args[0]=='show':
                        key=args[args.index('-p')+1]
                        if key=='FragmentPath': output=str(unit)
                        elif key=='MainPID': output=str(state['process'].pid if state['process'] else 0)
                        elif key=='ActiveState': output='active' if state['process'] else 'inactive'
                        elif key=='DropInPaths': output=str(guard) if state['loaded'] and guard.exists() else ''
                        elif key=='NeedDaemonReload': output='no' if state['loaded'] else 'yes'
                        elif key=='ExecStartPre' and state['loaded'] and guard.exists():
                            argv=guard.read_text().split('ExecStartPre=!',1)[1].strip()
                            output='{ path=/usr/bin/python3 ; argv[]='+argv+' ; ignore_errors=no ; pid=0 ; status=0/0 }'
                        elif key=='ExecStart': output='{ path='+observation.APP_EXEC_START[0]+' ; argv[]='+' '.join(observation.APP_EXEC_START)+' ; ignore_errors=no ; pid=0 ; status=0/0 }'
                        else: output=observation.APP_PROPERTIES[key]
                    elif args[0]=='is-active':
                        active=bool(state['process'] or state.get('type_simple_started'))
                        rc=0 if active else 3; output='active' if active else 'inactive'
                    elif args[0]=='is-enabled': output='enabled'
                    elif args[0]=='daemon-reload':
                        state['loaded']=state['failure']!='reload'
                        if state.get('late_write') and not state.get('later_rows'):
                            assert ha('',dict(event='exit',source='home_assistant',location='office',timestamp='2030-01-01T16:00:00Z'))==201
                            with sqlite3.connect(database) as db:
                                db.execute("UPDATE user_sessions SET revoked_at='2030-01-01T17:00:00+00:00'")
                                db.execute("UPDATE login_attempts SET failed_at='2030-01-01T17:00:00+00:00'")
                            state['later_rows']=database_rows(database)
                        if state['failure']=='kill-bootstrap': os.kill(state['updater'],signal.SIGKILL)
                    elif args[0]=='stop': state['type_simple_started']=False; stop()
                    elif args[0]=='restart':
                        state['type_simple_started']=True
                        if state['failure']=='kill-after': os.kill(state['updater'],signal.SIGKILL)
                        else:
                            def later():
                                time.sleep(.2)
                                if state['failure']!='never-ready': start()
                            worker=threading.Thread(target=later); state['threads'].append(worker); worker.start()
                elif kind=='npm' and args==['run','build'] and state['failure']=='build': state['failure']=None; rc=1
                elif kind=='sqlite3':
                    import shlex
                    with sqlite3.connect(args[0]) as db, sqlite3.connect(shlex.split(args[1])[1]) as dest: db.backup(dest)
                elif kind=='git-barrier':
                    if state['failure']=='kill-handoff': os.kill(state['updater'],signal.SIGKILL)
                elif kind=='alembic':
                    if not state.get('legacy'):
                        assert guard.exists() and state['loaded'], 'migration BEFORE effective guard'
                        assert (config/'update-state.json').exists()
                        assert json.loads((config/'update-state.json').read_bytes())['phase']=='migrating'
                    # The contender has no access to the inherited lock.
                    import fcntl
                    if not state.get('legacy'):
                        with lock.open('rb') as contender:
                            with pytest.raises(BlockingIOError): fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    result=subprocess.run([sys.executable,'-m','alembic',*args],cwd=app/'backend',env=os.environ|{'DATABASE_URL':url,'WT_TEST_UPDATER_PID':str(state['updater'])},capture_output=True)
                    rc,output=result.returncode,result.stdout.decode()
            except Exception:
                rc,output=1,'Test adapter refusal (details withheld)'
            self.wfile.write(json.dumps(dict(rc=rc,out=output)).encode()+b'\n')
    server=socketserver.UnixStreamServer(str(sock_path),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    driver=f'''#!{sys.executable}
import os,pathlib,socket,json,sys
kind=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
if kind=='git':
 r=__import__('subprocess').run(['/usr/bin/git',*args],capture_output=True)
 sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr)
 if r.returncode or 'merge' not in args:sys.exit(r.returncode)
 kind='git-barrier'
if kind=='python3':os.execv({sys.executable!r},[{sys.executable!r},*args])
if kind=='runuser':os.execvp(args[3],args[3:])
if kind=='chown':sys.exit(0)
if kind=='install':
 clean=[]
 while args:
  v=args.pop(0)
  if v in ('-o','-g'):args.pop(0)
  else:clean.append(v)
 os.execv('/usr/bin/install',['/usr/bin/install',*clean])
with socket.socket(socket.AF_UNIX) as s:
 s.connect({str(sock_path)!r});s.sendall(json.dumps(dict(kind=kind,args=args)).encode()+b'\\n');r=json.loads(s.makefile('rb').readline());sys.stdout.write(r['out']);sys.exit(r['rc'])
'''
    for name in ('systemctl','npm','sqlite3','install','chown','runuser','python3','git'):
        (binary/name).write_text(driver);(binary/name).chmod(0o755)
    for name in ('pip','alembic'):
        (venv/name).write_text(driver);(venv/name).chmod(0o755)
    # Real ss is used for real process socket ownership.
    def upgrade(failure=None, expected=None, ref='refs/heads/main', legacy=False, late_write=False, manifest_entry=None, legacy_updater=None):
        state['failure']=failure; state['legacy']=legacy; state['late_write']=late_write
        if manifest_entry:
            with (release/'deploy/config.manifest').open('a') as stream: stream.write('\n'+manifest_entry+'\n')
        if failure=='kill-migration':
            (release/'backend/alembic/versions/20990101_first.py').write_text("from alembic import op\nimport sqlalchemy as sa\nimport os,signal\nrevision='20990101_first'\ndown_revision='20261025_08'\ndef upgrade():\n op.create_table('partial_first_upgrade',sa.Column('id',sa.Integer,primary_key=True))\n os.kill(int(os.environ['WT_TEST_UPDATER_PID']),signal.SIGKILL)\n raise RuntimeError('partial test migration')\n")
        command(['git','-C',release,'add','.']); command(['git','-C',release,'commit','-qm','Private candidate'])
        target=command(['git','-C',release,'rev-parse','HEAD']).decode().strip()
        command(['git','-C',release,'push','-q','origin','HEAD:refs/heads/main'])
        if legacy:
            source = legacy_updater if legacy_updater is not None else command(['git','-C',app,'show',BASE+':update.sh'])
            old=redirected(source.decode()).replace('[[ "${EUID}" -eq 0 ]]','true')
            old=old.replace('readonly SERVICE_USER="work-tracker"', f'readonly SERVICE_USER="{__import__("pwd").getpwuid(os.getuid()).pw_name}"')
            file=base/'historical-updater.sh';file.write_text(old);file.chmod(0o700)
            args=['bash',str(file)]
        else: args=[sys.executable,'-I',str(entry),'start','--expected-sha',expected or target,'--ref',ref,'--readiness-timeout','5']
        proc=subprocess.Popen(args,env=os.environ|{'PATH':str(binary)+':'+os.environ['PATH']},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        state['updater']=proc.pid
        stdout,stderr=proc.communicate(timeout=45)
        return SimpleNamespace(returncode=proc.returncode,stdout=stdout,stderr=stderr,target=target)
    def old_release_update():
        # Exact historical runtime on the unchanged BASE backend/frontend tree.
        # Validate every blob before adapting OS paths/UIDs; no safety mocks.
        historical = {name: historical_file(name) for name in SHA256}
        target_files={str(path.relative_to(release)):path.read_bytes() for path in (release/'deploy').iterdir() if path.is_file()}
        target_files['update.sh']=(release/'update.sh').read_bytes()
        stop()
        command(['git','-C',app,'checkout','-q','--detach',BASE])
        for name, data_bytes in historical.items():
            (app/name).write_bytes(data_bytes)
        for name in ('common.sh','env_file.py','update_rollback.sh','update_safety.py'):
            content=redirected(historical['deploy/'+name].decode())
            if name=='update_safety.py':
                content=content.replace('uid = 0','uid = os.getuid()').replace("boundary = Path('/')",f'boundary = Path({str(base)!r})')
                content=content.replace('os.geteuid() == 0','os.geteuid() == os.getuid()')
            (app/'deploy'/name).write_text(content)
        command(['git','-C',app,'config','user.name','Upgrade Test'])
        command(['git','-C',app,'config','user.email','upgrade@example.invalid'])
        command(['git','-C',app,'add','.']);command(['git','-C',app,'commit','-qm','Private historical OS adapter'])
        baseline=command(['git','-C',app,'rev-parse','HEAD']).decode().strip()
        old_checker=(app/'deploy/update_safety.py').read_bytes()
        checker_path=config/'update-safety.py';checker_path.write_bytes(old_checker);checker_path.chmod(0o644)
        guard.parent.mkdir(mode=0o755)
        guard.write_text('[Service]\nExecStartPre=!/usr/bin/python3 -I '+str(checker_path)+' allow-start\n');guard.chmod(0o644)
        state['loaded']=True
        command(['git','-C',release,'fetch','-q',app,baseline])
        command(['git','-C',release,'reset','--hard',baseline])
        for name,data_bytes in target_files.items():
            if name=='deploy/update_safety.py':
                data_bytes=data_bytes.replace(b'fcfb8ee82d5c404e721b3e8188b07c514b60f69a4139669be390060353cb6476',__import__('hashlib').sha256(old_checker).hexdigest().encode())
            (release/name).write_bytes(data_bytes)
        assert start();ready()
        result=upgrade(legacy=True,legacy_updater=historical['update.sh'])
        if result.returncode: return result,old_checker
        ready()
        # The unchanged old staged updater finished; new checkout accepted v1.
        assert checker_path.read_bytes()==old_checker and not (config/'update-state.json').exists()
        migrated=subprocess.run([sys.executable,app/'deploy/update_safety.py','migrate-checker'],capture_output=True)
        assert migrated.returncode==0,migrated.stderr.decode()
        assert checker_path.read_bytes()==(app/'deploy/update_safety.py').read_bytes()!=old_checker
        return result,old_checker
    try:
        yield SimpleNamespace(app=app,base=base,env=env,unit=unit,guard=guard,state=state,backups=backups,database=database,
            upgrade=upgrade,old_release_update=old_release_update,original_env=original_env,rows=original_rows,start=start,stop=stop,ready=ready,lock=lock,entry=entry,remote=remote)
    finally:
        for worker in state['threads']:worker.join(5)
        stop(); server.shutdown();server.server_close();thread.join(5);log.close();keeper.close()


def test_first_lan_upgrade_uses_actual_old_checkout_and_preserves_all_rows(first):
    f=first; wal=Path(str(f.database)+'-wal');shm=Path(str(f.database)+'-shm')
    result=f.upgrade()
    assert result.returncode==0, result.stdout.decode()+result.stderr.decode()
    assert f.guard.exists() and (f.env.parent/'update-safety.py').exists()
    assert f.env.read_bytes()==f.original_env and database_rows(f.database)==f.rows
    assert not (f.env.parent/'update-state.json').exists()
    f.ready()
    assert f.state['starts']==[BASE,result.target]
    directory=next(f.backups.glob('update-first-*'))
    assert json.loads((directory/'transaction.json').read_bytes())['phase']=='commit'
    assert database_rows(directory/'online.db')==f.rows


@pytest.mark.parametrize('failure',['build','kill-migration','kill-after','reload','kill-bootstrap','kill-handoff','never-ready'])
def test_interruption_preserves_data_and_prevents_unsafe_boot(first,failure):
    f=first; result=f.upgrade(failure=failure)
    assert result.returncode!=0
    assert f.env.read_bytes()==f.original_env and database_rows(f.database)==f.rows
    assert Path(str(f.database)+'-wal').exists() and Path(str(f.database)+'-shm').exists()
    if failure=='build':
        assert any(kind=='npm' and args==['run','build'] for kind,args in f.state['calls'])
        assert not any(kind=='alembic' for kind,args in f.state['calls'])
        assert b'Starting automatic rollback' in result.stderr
        assert not (f.env.parent/'update-state.json').exists()
        f.ready();assert f.state['starts'][-1]==BASE
    else:
        record=json.loads((f.env.parent/'update-state.json').read_bytes())
        assert record['phase']=={'kill-migration':'migrating','kill-after':'verifying','reload':'preparing','kill-bootstrap':'preparing','kill-handoff':'preparing','never-ready':'manual'}[failure]
        f.stop()
        assert not f.start()  # Actual stable checker; systemd is simulated.
        assert database_rows(f.database)==f.rows
        assert f.state['starts']==[BASE]
        if failure=='kill-migration':
            with sqlite3.connect(f.database) as db: assert db.execute("SELECT name FROM sqlite_master WHERE name='partial_first_upgrade'").fetchone()
        again=subprocess.run([sys.executable,'-I',f.entry,'start','--expected-sha',result.target],capture_output=True)
        assert again.returncode==1  # Conservative safe refusal, no automatic resume.
        assert database_rows(f.database)==f.rows


@pytest.mark.parametrize('damage',['sha','ref','dirty','foreign','origin','lock','unknown','checker','legacy-running','database-mode'])
def test_first_upgrade_refuses_invalid_target_checkout_guard_or_concurrency(first,damage):
    f=first
    if damage=='dirty': (f.app/'README.md').write_text('administrator change\n')
    if damage=='foreign': f.guard.parent.mkdir();f.guard.write_text('[Service]\nExecStartPre=administrator\n')
    if damage=='origin': command(['git','-C',f.app,'remote','set-url','origin','https://example.invalid/foreign.git'])
    if damage=='unknown': command(['git','-C',f.app,'checkout','-q',BASE+'^'])
    if damage=='database-mode': f.database.chmod(0o660)
    if damage=='checker': (f.env.parent/'update-safety.py').write_text('foreign checker')
    legacy=None
    if damage=='legacy-running': legacy=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)',str(f.app/'update.sh')])
    held=None
    if damage=='lock':
        import fcntl
        held=f.lock.open('wb');os.chmod(f.lock,0o600);fcntl.flock(held,fcntl.LOCK_EX)
    try: result=f.upgrade(expected='a'*40 if damage=='sha' else None,ref='refs/heads/unknown' if damage=='ref' else 'refs/heads/main')
    finally:
        if held:held.close()
        if legacy:legacy.terminate();legacy.wait(timeout=5)
    assert result.returncode==1
    assert b'FIRST UPGRADE REFUSED' in result.stderr
    assert not any(kind=='alembic' for kind,args in f.state['calls'])
    assert database_rows(f.database)==f.rows and f.env.read_bytes()==f.original_env
    assert not (f.env.parent/'update-state.json').exists()
    if damage=='foreign': assert f.guard.read_text()=='[Service]\nExecStartPre=administrator\n'


def test_real_historical_staged_updater_demonstrates_missing_crash_guard(first):
    f=first
    result=f.upgrade(failure='kill-migration',legacy=True)
    assert result.returncode==-signal.SIGKILL, result.stdout.decode()+result.stderr.decode()
    assert not f.guard.exists() and not (f.env.parent/'update-state.json').exists()
    with sqlite3.connect(f.database) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='partial_first_upgrade'").fetchone()
    assert database_rows(f.database)==f.rows
    # No actual unsafe boot: absence of any pre-start guard is the regression.
    assert any(kind=='alembic' for kind,args in f.state['calls'])


def test_first_upgrade_keeps_accepted_ha_and_identity_writes_after_online_backup(first):
    f=first
    result=f.upgrade(failure='build',late_write=True)
    assert result.returncode==1
    assert f.state['later_rows']!=f.rows
    assert database_rows(f.database)==f.state['later_rows']
    assert database_rows(f.database)['work_events'][0]==f.rows['work_events'][0]
    directory=next(f.backups.glob('update-first-*'))
    assert database_rows(directory/'online.db')==f.rows  # Historical copy predates later accepted writes.
    assert f.env.read_bytes()==f.original_env
    f.ready()


@pytest.mark.parametrize('problem,key', [('missing','SESSION_COOKIE_SECURE'),('invalid','APP_PORT'),('future','FUTURE_REQUIRED')])
def test_target_manifest_refuses_before_guard_ff_stop_or_migration(first,problem,key):
    f=first
    entry=None
    if problem=='missing': f.env.write_text('\n'.join(line for line in f.env.read_text().splitlines() if not line.startswith(key+'='))+'\n')
    if problem=='invalid': f.env.write_text(f.env.read_text().replace('APP_PORT=', 'APP_PORT=invalid'))
    if problem=='future': entry='FUTURE_REQUIRED|required|secret||Private value supplied before upgrade|token'
    before=f.env.read_bytes()
    result=f.upgrade(manifest_entry=entry)
    assert result.returncode==1 and key.encode() in result.stderr
    assert f.env.read_bytes()==before and database_rows(f.database)==f.rows
    assert command(['git','-C',f.app,'rev-parse','HEAD']).decode().strip()==BASE
    assert not f.guard.exists() and not (f.env.parent/'update-state.json').exists()
    assert not any(kind=='alembic' or (kind=='systemctl' and args[0] in ('stop','restart')) for kind,args in f.state['calls'])


def test_first_upgrade_unsupported_bind_refuses_before_guard_and_preserves_running_lan(first):
    f = first
    f.env.write_bytes(f.env.read_bytes().replace(b'APP_HOST=0.0.0.0\n', b'APP_HOST=localhost\n'))
    before = f.env.read_bytes()
    process = f.state['process']
    result = f.upgrade()
    assert result.returncode == 1 and b'APP_HOST' in result.stderr and b'Operator' in result.stderr
    assert f.env.read_bytes() == before and database_rows(f.database) == f.rows
    assert command(['git', '-C', f.app, 'rev-parse', 'HEAD']).decode().strip() == BASE
    assert not f.guard.exists() and not (f.env.parent / 'update-state.json').exists()
    assert f.state['process'] is process and process.poll() is None
    f.ready()
    assert not any(kind in ('npm', 'alembic') or (kind == 'systemctl' and args[0] in ('daemon-reload', 'stop', 'restart'))
                   for kind, args in f.state['calls'])


def test_new_required_target_key_already_supplied_stays_byte_identical(first):
    f=first
    f.env.write_bytes(f.env.read_bytes()+b'FUTURE_REQUIRED=public-fixture-value\n')
    before=f.env.read_bytes()
    result=f.upgrade(manifest_entry='FUTURE_REQUIRED|required|plain||Supply before upgrade|nonempty')
    assert result.returncode==0, result.stderr.decode()
    assert f.env.read_bytes()==before and database_rows(f.database)==f.rows
    assert not (f.env.parent/'update-state.json').exists()


def test_production_bootstrap_rejects_feature_branch(first):
    f=first; result=f.upgrade(ref='refs/heads/feat/phase-9b2-safe-cutover')
    assert result.returncode==1 and not f.guard.exists()
    assert command(['git','-C',f.app,'rev-parse','HEAD']).decode().strip()==BASE


def test_real_old_release_update_then_explicit_checker_version_migration(first):
    f=first
    result,before=f.old_release_update()
    assert result.returncode==0,result.stdout.decode()+result.stderr.decode()
    assert database_rows(f.database)==f.rows and f.env.read_bytes()==f.original_env
    archives=list(f.backups.glob('update-checker-*'))
    assert len(archives)==1 and (archives[0]/'checker-before.py').read_bytes()==before
    assert not (f.env.parent/'checker-migration.json').exists()
