import sqlite3
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from extract_session_trajectories import ACTION_FIELDS, DM_MAGNIFICATION_KEY, magnification_number


class MagnificationTests(unittest.TestCase):
    def test_example_database_propagates_magnification(self):
        root = Path(__file__).resolve().parents[1]
        inventory = root / 'examples/cupd50_core_she_2022_02_14/_archive_inventory'
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'trajectories.sqlite'
            subprocess.run([
                sys.executable, str(root / 'scripts' / 'extract_session_trajectories.py'),
                '--metadata-db', str(inventory / 'stem_bank_26_metadata.sqlite'),
                '--sessions-db', str(inventory / 'stem_bank_26_sessions_by_acq_date_sample.sqlite'),
                '-o', str(output),
            ], check=True, capture_output=True)
            with sqlite3.connect(output) as conn:
                for table, column, expected in [
                    ('image_sequence', 'magnification_number', 21),
                    ('si_events', 'magnification', 3),
                    ('action_sequence', 'magnification', 24),
                    ('image_transitions', 'delta_magnification', 20),
                    ('action_transitions', 'delta_magnification', 23),
                ]:
                    self.assertEqual(conn.execute(
                        f'SELECT count(*), count({column}) FROM {table}'
                    ).fetchone(), (expected, expected))
                self.assertEqual(conn.execute(
                    'SELECT sum(magnification_change_count) FROM trajectory_summary'
                ).fetchone()[0], 18)
                self.assertEqual(conn.execute(
                    'SELECT count(*) FROM image_transitions WHERE delta_magnification '
                    '!= curr_magnification_number - prev_magnification_number'
                ).fetchone()[0], 0)

    def test_source_precedence_and_missing_values(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute("ATTACH DATABASE ':memory:' AS meta")
        conn.execute('CREATE TABLE meta.metadata_kv (signal_id, source, key_path, value_number)')
        conn.execute('CREATE TABLE signals (signal_id)')
        conn.executemany('INSERT INTO signals VALUES (?)', [(i,) for i in range(1, 8)])
        normalized = 'hyperspy_metadata', ACTION_FIELDS['magnification']
        original = 'hyperspy_original_metadata', DM_MAGNIFICATION_KEY
        records = [
            (1, *original, 1200000),
            (2, *normalized, 1000000), (2, *original, 1200000),
            (3, *normalized, None), (3, *original, 1500000),
            (4, *normalized, 0), (4, *original, 2000000),
            # Signal 5 has no magnification; 6 has conflicting original values.
            (6, *original, 1000000), (6, *original, 2000000),
            # Repeated identical values do not multiply or invalidate a signal.
            (7, *original, 3000000), (7, *original, 3000000),
        ]
        conn.executemany('INSERT INTO meta.metadata_kv VALUES (?, ?, ?, ?)', records)
        actual = conn.execute(
            f'SELECT {magnification_number("s")} FROM signals s ORDER BY signal_id'
        ).fetchall()
        self.assertEqual(actual, [(1200000,), (1000000,), (1500000,), (2000000,),
                                  (None,), (None,), (3000000,)])


if __name__ == '__main__':
    unittest.main()
