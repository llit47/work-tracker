"""Only Pending is retryable; static/runtime security failures are not."""
import pytest
from nginx_provision_test import setup, tls, p
from ingress_cutover_test import cutover, activate, c


def test_delayed_readiness_then_one_final_security_verification(cutover, monkeypatch):
    tool = activate(cutover)
    observed = []
    def readiness(config, **kwargs):
        observed.append('readiness')
        if len(observed) < 5: raise p.Pending('starting')
    monkeypatch.setattr(tool.host, 'update_readiness', readiness)
    original = tool.verify
    final = []
    def verify():
        final.append(True)
        original()
    monkeypatch.setattr(tool, 'verify', verify)
    tool.update_verify(5)
    assert len(observed) == 5 and final == [True]
    tool.recover(rollback=True)


def test_permanent_runtime_security_failure_is_not_retried(cutover, monkeypatch):
    tool = activate(cutover)
    calls = []
    def readiness(_, **kwargs):
        calls.append(True)
        raise p.Refusal('wrong bind/argv/config')
    monkeypatch.setattr(tool.host, 'update_readiness', readiness)
    original_sleep = c.time.sleep
    monkeypatch.setattr(c.time, 'sleep', lambda _: pytest.fail('security error must not sleep/retry'))
    with pytest.raises(p.Refusal): tool.update_verify(5)
    assert calls == [True]
    monkeypatch.setattr(c.time, 'sleep', original_sleep)
    tool.recover(rollback=True)


def test_final_acl_failure_is_checked_once_after_ready(cutover, monkeypatch):
    tool = activate(cutover)
    calls = []
    monkeypatch.setattr(tool.host, 'update_readiness', lambda _, **kwargs: calls.append('ready'))
    def bad_acl(_):
        calls.append('acl')
        raise p.Refusal('unexpected ACL response')
    monkeypatch.setattr(tool.host, 'private_probes', bad_acl)
    with pytest.raises(p.Refusal): tool.update_verify(5)
    assert calls == ['ready', 'acl']
    tool.recover(rollback=True)


def test_timeout_retries_pending_only_and_does_not_run_final_verify(cutover, monkeypatch):
    tool = activate(cutover)
    instant = [0.0]
    original_clock, original_sleep = c.time.monotonic, c.time.sleep
    monkeypatch.setattr(c.time, 'monotonic', lambda: instant[0])
    monkeypatch.setattr(c.time, 'sleep', lambda seconds: instant.__setitem__(0, instant[0] + seconds))
    def not_ready(_, **kwargs): raise p.Pending('still starting')
    monkeypatch.setattr(tool.host, 'update_readiness', not_ready)
    monkeypatch.setattr(tool, 'verify', lambda: pytest.fail('not ready'))
    with pytest.raises(p.Refusal, match='Timeout'): tool.update_verify(5)
    assert 5 <= instant[0] <= 5.1
    monkeypatch.setattr(c.time, 'monotonic', original_clock)
    monkeypatch.setattr(c.time, 'sleep', original_sleep)
    tool.recover(rollback=True)


@pytest.mark.parametrize('timeout', [0, 4, 301, True, '60'])
def test_readiness_timeout_has_safe_bounds(cutover, timeout):
    tool, _ = cutover
    with pytest.raises(p.Refusal, match='5..300'): tool.update_verify(timeout)


def test_readiness_caps_subprocess_budget_and_clears_deadline(monkeypatch):
    host = p.Host()
    observed = []
    def absent_backend(args, *, timeout, **kwargs):
        observed.append(timeout)
        return b'0'
    monkeypatch.setattr(p, 'execute', absent_backend)
    with pytest.raises(p.Pending):
        host.update_readiness({}, deadline=c.time.monotonic() + .2)
    assert len(observed) == 1 and 0 < observed[0] <= .2
    assert host._probe_deadline is None
    assert host.io_timeout(30) == 30
