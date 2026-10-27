"""Deterministic proc/systemd observations, real Host validation and deadline.

No production UID/systemd evidence. Live TCP/Nginx is tested in split_update.
"""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from host_validation_test import host, process, p
from ingress_cutover_test import c


@pytest.fixture
def runtime(host, monkeypatch):
    config=dict(lan_ip='10.123.45.10',backend_port=18001,ha_port=18000,browser_port=18443,public_hostname='work.example.invalid')
    argv=['python','uvicorn','app.main:app','--host','127.0.0.1','--port','18001','--workers','1','--no-proxy-headers']
    env=dict(INGRESS_PROFILE='split',SESSION_COOKIE_SECURE='true',BROWSER_ALLOWED_ORIGINS='https://work.example.invalid')
    process(host,argv=argv,env=env)
    process(host,pid='22',children='33')
    process(host,pid='33',uid='33')
    sockets=[('127.0.0.1',18001,'pid=1234,'),('10.123.45.10',18000,'pid=22,'),('10.123.45.10',18443,'pid=22,')]
    state=dict(pid='1234',sockets=sockets)
    monkeypatch.setattr(p,'execute',lambda *args,**kwargs:state['pid'].encode())
    monkeypatch.setattr(host,'listeners',lambda:state['sockets'])
    monkeypatch.setattr(host,'ingress_pid',lambda:'22')
    monkeypatch.setattr(host,'active',lambda:True)
    monkeypatch.setattr(host,'backend_health',lambda _:None)
    monkeypatch.setattr(p.socket,'create_connection',lambda *args,**kwargs:nullcontext())
    monkeypatch.setattr(__import__('pwd'),'getpwnam',lambda _:SimpleNamespace(pw_uid=33))
    instant=[0.0]
    monkeypatch.setattr(c.time,'monotonic',lambda:instant[0])
    monkeypatch.setattr(c.time,'sleep',lambda duration:instant.__setitem__(0,instant[0]+duration))
    final=[]
    def verify():
        host.backend_ready(config)
        host.ingress_permissions('22')
        final.append(True)
    tool=SimpleNamespace(host=host,verify_configuration=lambda:dict(config=config),verify=verify)
    return SimpleNamespace(host=host,config=config,argv=argv,env=env,state=state,clock=instant,tool=tool,final=final)


def test_pre_exec_argv_env_and_worker_stabilize_before_final_validation(runtime,monkeypatch):
    r=runtime; original=r.host.update_readiness; seen=[]
    def observe(config,**kwargs):
        step=len(seen);seen.append(step)
        r.state['pid']='0' if step==0 else '1234'
        process(r.host,argv=['(sd-executor)'] if step==1 else (r.argv[:4] if step==2 else r.argv),env={} if step<4 else r.env)
        r.state['sockets']=[] if step<5 else [('127.0.0.1',18001,'pid=1234,'),('10.123.45.10',18000,'pid=22,'),('10.123.45.10',18443,'pid=22,')]
        process(r.host,pid='22',children='' if step<6 else '33')
        process(r.host,pid='33',uid='0' if step==6 else '33')
        return original(config,**kwargs)
    monkeypatch.setattr(r.host,'update_readiness',observe)
    c.Cutover.update_verify(r.tool,5)
    assert seen==list(range(9)) and r.final==[True]


@pytest.mark.parametrize('invalid',['argv','extra-argv','encoding','environment','worker-root','worker-count'])
def test_persistent_dynamic_mismatch_expires_as_invalid_runtime(runtime,invalid):
    r=runtime
    if invalid=='argv': process(r.host,argv=r.argv+['--proxy-headers'],env=r.env)
    if invalid=='extra-argv': process(r.host,argv=r.argv+['--timeout-keep-alive','90'],env=r.env)
    if invalid=='encoding': (r.host.proc/'1234/environ').write_bytes(b'INCOMPLETE=\xc3')
    if invalid=='environment': process(r.host,argv=r.argv,env=r.env|{'SESSION_COOKIE_SECURE':'false'})
    if invalid=='worker-root': process(r.host,pid='33',uid='0')
    if invalid=='worker-count': process(r.host,pid='22',children='33 44')
    with pytest.raises(p.Refusal,match='runtime argv/env/workera'): c.Cutover.update_verify(r.tool,5)
    assert 5<=r.clock[0]<=5.1 and not r.final


@pytest.mark.parametrize('invalid',['bind','foreign-backend','foreign-ingress','master-uid','worker-foreign-uid'])
def test_confirmed_socket_or_foreign_uid_violation_is_immediate(runtime,invalid):
    r=runtime
    if invalid=='bind': r.state['sockets'][0]=('0.0.0.0',18001,'pid=1234,')
    if invalid=='foreign-backend': r.state['sockets'][0]=('127.0.0.1',18001,'pid=99,')
    if invalid=='foreign-ingress': r.state['sockets'][1]=('10.123.45.10',18000,'pid=99,')
    if invalid=='master-uid': process(r.host,pid='22',uid='99',children='33')
    if invalid=='worker-foreign-uid': process(r.host,pid='33',uid='99')
    with pytest.raises(p.Refusal) as error: c.Cutover.update_verify(r.tool,5)
    assert not isinstance(error.value,p.Pending) and r.clock[0]==0 and not r.final


def test_missing_process_expires_as_unavailable_without_final_verification(runtime):
    r=runtime;r.state['pid']='0'
    with pytest.raises(p.Refusal,match='usługi nie osiągnęły gotowości'): c.Cutover.update_verify(r.tool,5)
    assert not r.final


@pytest.mark.parametrize('failure', [ConnectionResetError, BrokenPipeError, ConnectionError])
def test_transient_http_termination_then_stable_ready_and_one_final_check(runtime, monkeypatch, failure):
    r = runtime
    calls = []
    def health(_):
        calls.append(True)
        if len(calls) == 1: raise failure('startup transport termination')
    monkeypatch.setattr(r.host, 'backend_health', health)
    c.Cutover.update_verify(r.tool, 5)
    assert len(calls) == 3 and r.clock[0] == .2 and r.final == [True]


def test_permanent_connection_reset_expires_without_final_check(runtime, monkeypatch):
    r = runtime
    def health(_): raise ConnectionResetError('still restarting')
    monkeypatch.setattr(r.host, 'backend_health', health)
    with pytest.raises(p.Refusal, match='usługi nie osiągnęły gotowości'):
        c.Cutover.update_verify(r.tool, 5)
    assert 5 <= r.clock[0] <= 5.1 and not r.final


def test_complete_malformed_http_response_is_not_a_retryable_transport_failure(runtime, monkeypatch):
    r = runtime
    def health(_): raise p.Refusal('Nieprawidłowa linia statusu HTTP.')
    monkeypatch.setattr(r.host, 'backend_health', health)
    with pytest.raises(p.Refusal, match='linia statusu'):
        c.Cutover.update_verify(r.tool, 5)
    assert r.clock[0] == 0 and not r.final
