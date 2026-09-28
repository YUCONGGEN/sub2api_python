import sqlite3
from scripts.seed_mapping_presets import apply_presets, missing_presets
from backend.service.user_group_service import UserGroupService


def test_presets_are_idempotent_preserve_groups_and_match_any_effort():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('''
            CREATE TABLE model_mappings(id INTEGER PRIMARY KEY, name TEXT, source_model TEXT,
                source_effort TEXT, target_model TEXT, target_effort TEXT, enabled INTEGER,
                created_at TEXT, updated_at TEXT);
            CREATE TABLE user_group_model_mappings(group_id INTEGER, mapping_id INTEGER);
            INSERT INTO model_mappings VALUES(1,'existing','gpt-5.5','*','gpt-6-luna','high',0,'old','old');
            INSERT INTO user_group_model_mappings VALUES(42,1);
        ''')
        assert len(missing_presets(conn)) == 23
        assert apply_presets(conn) == 23
        assert apply_presets(conn) == 0
        assert conn.execute('SELECT name,enabled FROM model_mappings WHERE id=1').fetchone() == ('existing', 0)
        assert conn.execute('SELECT * FROM user_group_model_mappings').fetchall() == [(42, 1)]
        conn.row_factory = sqlite3.Row
        for row in conn.execute('SELECT * FROM model_mappings WHERE id>1'):
            rule = dict(row)
            for effort in ('', 'none', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
                result, matched = UserGroupService.apply_model_mapping(
                    {'group_model_mappings': [rule]},
                    {'model': rule['source_model'], 'reasoning': {'effort': effort}})
                assert matched['id'] == rule['id']
                assert result['model'] == rule['target_model']
                assert result['reasoning']['effort'] == 'high'
