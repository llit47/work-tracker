"""Small, explicit migration of two owned files; no generic deployment framework.

Ownership means trusted permissions AND exact bytes of a version in the caller's
catalog. A filename/comment alone is never evidence. Backups are immutable;
write-ahead records allow retry before/after atomic replacement and fsync.
"""
import base64
import re
from pathlib import Path

import nginx_provision as p


def migrate(tool, name, path, versions, current, mode):
    layout = tool.layout
    p.require(re.fullmatch('[a-z-]+', name) and current in versions, 'Nieznany artefakt/wersja.')
    journal = layout.state / f'{name}-migration.json'
    target = versions[current]
    pending = journal.exists() or journal.is_symlink()
    if pending:
        layout.check(journal, private=True)
        record = p.read_json(journal)
        p.require(set(record) == {'backup', 'backup_sha', 'target_version'} and record['target_version'] == current,
                  'Niedokończona migracja innej wersji artefaktu; użyj zgodnej wersji narzędzia.')
        backup = Path(record['backup'])
        p.require(backup.parent == layout.backups, 'Nieprawidłowy backup artefaktu.')
        layout.check(backup, private=True)
        p.require(p.digest(backup.read_bytes()) == record['backup_sha'], 'Backup artefaktu zmieniony.')
        saved = p.read_json(backup)
        p.require(set(saved) == {'path', 'before', 'version'} and saved['path'] == str(path), 'Nieprawidłowy zapis migracji.')
        before = None if saved['before'] is None else base64.b64decode(saved['before'], validate=True)
        p.require((before is None and saved['version'] is None) or
                  (saved['version'] in versions and before == versions[saved['version']]), 'Nieznana poprzednia wersja.')
    else:
        before = None
        version = None
        if path.exists() or path.is_symlink():
            layout.check(path)
            before = path.read_bytes()
            version = next((v for v, content in versions.items() if before == content), None)
            p.require(version is not None, 'Obcy lub zmieniony artefakt: odmowa przejęcia.')
            p.require(path.stat().st_mode & 0o777 == mode, 'Nieprawidłowy tryb artefaktu.')
            if version == current:
                return
        backup = tool.backup(name, {'path': str(path), 'before': None if before is None else base64.b64encode(before).decode(),
                                    'version': version})
        record = dict(backup=str(backup), backup_sha=p.digest(backup.read_bytes()), target_version=current)
        p.atomic(journal, p.encoded(record))
        tool.host.checkpoint('artifact-journal')
    if path.exists() or path.is_symlink():
        layout.check(path)
        p.require(path.stat().st_mode & 0o777 == mode, 'Nieprawidłowy tryb artefaktu podczas migracji.')
        p.require(path.read_bytes() in (before, target), 'Artefakt zmieniony podczas migracji.')
    else:
        p.require(before is None, 'Artefakt usunięty podczas migracji.')
    layout.check(path.parent, directory=True)
    if not path.exists() or path.read_bytes() != target:
        p.atomic(path, target, mode)
    tool.host.checkpoint('artifact-written')
    journal.unlink()
    p.fsync_dir(journal.parent)
