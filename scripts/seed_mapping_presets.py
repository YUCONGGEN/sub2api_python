"""Add opt-in GPT routing presets. Never change existing rules or group bindings.

Dry-run: python -m scripts.seed_mapping_presets --database data/rose.db
Apply: add --apply --backup /private/path/unique-backup.db
Model positioning: https://developers.openai.com/api/docs/models
The high effort choices are presets, not guarantees of quality or subscription cost.
"""
import argparse
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from backend.common.model_capabilities import known_reasoning_efforts

SOURCES = ('gpt-5.5', 'gpt-5.6', 'gpt-5.6-sol', 'gpt-5.6-terra',
           'gpt-5.6-luna', 'gpt-6-sol', 'gpt-6-luna', 'gpt-6-astra')
TARGETS = (('节省优先', 'gpt-6-luna', 'high'),
           ('均衡', 'gpt-6-sol', 'high'),
           ('能力优先', 'gpt-6-astra', 'high'))


def missing_presets(connection):
    existing = {tuple(row) for row in connection.execute(
        'SELECT source_model,source_effort,target_model,target_effort FROM model_mappings')}
    rows = []
    for source in SOURCES:
        for label, target, effort in TARGETS:
            assert effort in known_reasoning_efforts(target)
            if (source, '*', target, effort) not in existing:
                rows.append((f'{label}：{source} / 任意 → {target} / {effort}', source, '*', target, effort))
    return rows


def apply_presets(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        rows = missing_presets(connection)
        now = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            'INSERT INTO model_mappings(name,source_model,source_effort,target_model,target_effort,enabled,created_at,updated_at) '
            'VALUES(?,?,?,?,?,1,?,?)', [(*row, now, now) for row in rows])
        connection.commit()
        return len(rows)
    except Exception:
        connection.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    database = Path(args.database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + ('?mode=rw' if args.apply else '?mode=ro'), uri=True, timeout=15) as connection:
        if not args.apply:
            for row in missing_presets(connection):
                print(row[0])
            return
        if not args.backup:
            parser.error('--apply requires --backup')
        backup = Path(args.backup).resolve()
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)
        print('Created presets:', apply_presets(connection))
        print('Existing mappings and group selections unchanged. Backup:', backup)


if __name__ == '__main__':
    main()
