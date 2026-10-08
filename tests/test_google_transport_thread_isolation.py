"""Network-free reproduction of Google service reuse across worker threads."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
from types import SimpleNamespace

import pytest

from src.integrations import gmail, google_calendar, google_drive

CASES = [(gmail, '_get_gmail_service'), (google_calendar, '_get_calendar_service'),
         (google_drive, '_get_drive_service')]


@pytest.mark.parametrize('module,getter_name', CASES)
def test_cached_transport_belongs_to_calling_thread(monkeypatch, module, getter_name):
    # Prime the cache as a webhook would, then overlap three scheduler workers.
    credentials = SimpleNamespace(token='synthetic-token')
    monkeypatch.setattr(module, 'get_credentials', lambda user_id: credentials)
    monkeypatch.setattr(module, 'build', lambda *a, **kw: SimpleNamespace(owner=get_ident()))
    getter = getattr(module, getter_name)
    main_service = getter(987654)
    barrier = Barrier(3)

    def worker():
        first = getter(987654)
        barrier.wait(timeout=5)
        second = getter(987654)
        assert first.owner == get_ident(), 'Transport crossed a thread boundary'
        assert first is second, 'Same-thread reuse should remain fast'
        assert first is not main_service
        return first

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(worker) for _ in range(3)]
        services = [future.result(timeout=10) for future in futures]
    assert len({id(service) for service in services}) == 3


@pytest.mark.parametrize('module,getter_name', CASES)
def test_token_change_and_user_separation(monkeypatch, module, getter_name):
    credentials = SimpleNamespace(token='token-a')
    monkeypatch.setattr(module, 'get_credentials', lambda user_id: credentials)
    monkeypatch.setattr(module, 'build', lambda *a, **kw: object())
    getter = getattr(module, getter_name)
    first = getter(987655)
    assert getter(987655) is first
    assert getter(987656) is not first
    credentials.token = 'token-b'
    assert getter(987655) is not first


@pytest.mark.parametrize('module,getter_name', CASES)
def test_disconnected_user_does_not_receive_cached_service(monkeypatch, module, getter_name):
    monkeypatch.setattr(module, 'get_credentials', lambda user_id: SimpleNamespace(token='test'))
    monkeypatch.setattr(module, 'build', lambda *a, **kw: object())
    getter = getattr(module, getter_name)
    getter(987657)
    monkeypatch.setattr(module, 'get_credentials', lambda user_id: None)
    with pytest.raises(module.NotConnectedError):
        getter(987657)
