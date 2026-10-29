import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import URLError

spec=importlib.util.spec_from_file_location('upstream_launcher',Path(__file__).resolve().parents[1]/'tools/upstream_local.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)

class LauncherStatusTests(unittest.TestCase):
    def test_permission_failure_is_not_reported_as_stopped(self):
        with patch.object(launcher,'get',side_effect=URLError(PermissionError(1,'not permitted'))), \
             patch.object(launcher.socket,'create_connection',side_effect=PermissionError(1,'not permitted')):
            with self.assertRaisesRegex(RuntimeError,'无法检查'): launcher.status()
    def test_refused_connection_means_stopped(self):
        with patch.object(launcher,'get',side_effect=URLError(ConnectionRefusedError())), \
             patch.object(launcher.socket,'create_connection',side_effect=ConnectionRefusedError()):
            self.assertIsNone(launcher.status())
    def test_unidentified_listener_is_not_our_app(self):
        with patch.object(launcher,'get',return_value={'app_id':'other'}):
            self.assertEqual(launcher.status(),{'other':True})

if __name__=='__main__': unittest.main()
