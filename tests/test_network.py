"""Regression for macOS Python installations with no default OpenSSL CA bundle."""
import ssl
import unittest
from unittest.mock import patch

from server import outbound_ssl_context


class NetworkTrustTests(unittest.TestCase):
    def test_missing_default_roots_are_restored_without_disabling_tls_checks(self):
        empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(empty.cert_store_stats()["x509_ca"], 0)
        with patch("server.ssl.create_default_context", return_value=empty):
            context = outbound_ssl_context()
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


if __name__ == "__main__":
    unittest.main()
