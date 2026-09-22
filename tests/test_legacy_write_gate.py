import argparse
import ast
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class LegacyWriteGateTests(unittest.TestCase):
    def test_every_countywide_write_entrypoint_stops_before_connection(self):
        source = Path(__file__).resolve().parents[1] / 'importer_parts/part06.py'
        assembled = ''.join(p.read_text(encoding='utf-8') for p in sorted(source.parent.glob('part*.py')))
        main = next(n for n in ast.parse(assembled).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        connect = Mock(side_effect=AssertionError('Must not open a production connection'))
        namespace = {'argparse': argparse, 'SEQUENCE': ['owner','parcel','sales','legal','parcel-tax','permits'], 'DEFAULT_PARTITIONS': 32, 'get_db_connection': connect}
        exec(compile(ast.Module(body=[main], type_ignores=[]), str(source), 'exec'), namespace)
        for args in ([], ['--only','owner'], ['--merge-only','legal'], ['--mode','direct'], ['--mode','direct','--dry-run']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['polk_importer.py'] + args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error: namespace['main']()
                self.assertEqual(error.exception.code, 2)
        connect.assert_not_called()
