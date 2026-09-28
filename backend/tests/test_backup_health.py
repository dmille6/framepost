"""Backup age must degrade health, otherwise StatusBanner exits before showing it."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from models import AppConfig
from services import health, storage


@pytest.mark.parametrize('stamp,exists,warn', [
    (None, False, True), ('invalid', True, True), ('old', True, True),
    ('fresh', False, True), ('fresh', True, False), ('naive', True, False),
])
def test_backup_health_reaches_banner(db, tmp_path, monkeypatch, stamp, exists, warn):
    now = datetime.now(timezone.utc)
    value = {'old': (now-timedelta(days=3)).isoformat(), 'fresh': now.isoformat(),
             'naive': now.replace(tzinfo=None).isoformat()}.get(stamp, stamp)
    db.add(AppConfig(key='worker_last_heartbeat', value=now.isoformat()))
    if value: db.add(AppConfig(key='last_backup', value=value))
    db.commit()
    if exists: (tmp_path / 'framepost-test.sqlite').write_bytes(b'backup')
    monkeypatch.setattr(storage, 'BACKUP', tmp_path)
    monkeypatch.setattr(health, 'SessionLocal', lambda: db)
    monkeypatch.setattr(db, 'close', lambda: None)
    monkeypatch.setattr(health.os, 'access', lambda *a: True)
    monkeypatch.setattr(health.shutil, 'disk_usage', lambda *a: SimpleNamespace(free=20 * 1024**3))
    result = health.collect_health()
    assert result['status'] == ('degraded' if warn else 'ok')
    assert bool(result['backup_warnings']) is warn
