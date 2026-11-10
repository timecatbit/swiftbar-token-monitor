"""Interpreter-specific dependencies and the certificate fallback, without network."""
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import swiftbar_runtime as runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.vendor=Path(self.tmp.name)/'vendor';(self.vendor/'certifi').mkdir(parents=True)
        (self.vendor/'certifi/cacert.pem').write_text('roots')
        self.bundle=str(self.vendor/'certifi/cacert.pem')
        env=patch.dict(os.environ);env.start();self.addCleanup(env.stop);os.environ.pop('SSL_CERT_FILE',None)

    def paths(self,cafile=None,capath=None):
        return ssl.DefaultVerifyPaths(cafile,capath,'SSL_CERT_FILE','/x/cert.pem','SSL_CERT_DIR','/x/certs')

    def test_python_without_root_certificates_uses_the_bundled_set(self):
        # python.org's Python before Install Certificates: no CA file, an empty certificate folder.
        empty=Path(self.tmp.name)/'certs';empty.mkdir()
        with patch.object(runtime,'vendor_dir',return_value=self.vendor),patch.object(runtime.ssl,'get_default_verify_paths',return_value=self.paths(capath=str(empty))):
            runtime.trust_bundled_certificates()
        self.assertEqual(os.environ['SSL_CERT_FILE'],self.bundle)

    def test_python_with_its_own_store_or_a_chosen_file_is_left_alone(self):
        with patch.object(runtime,'vendor_dir',return_value=self.vendor),patch.object(runtime.ssl,'get_default_verify_paths',return_value=self.paths(cafile='/etc/ssl/cert.pem')):
            runtime.trust_bundled_certificates()
        self.assertNotIn('SSL_CERT_FILE',os.environ)
        os.environ['SSL_CERT_FILE']='/chosen.pem'
        with patch.object(runtime,'vendor_dir',return_value=self.vendor),patch.object(runtime.ssl,'get_default_verify_paths',return_value=self.paths()):
            runtime.trust_bundled_certificates()
        self.assertEqual(os.environ['SSL_CERT_FILE'],'/chosen.pem')

    def test_no_bundle_changes_nothing(self):
        with patch.object(runtime,'vendor_dir',return_value=None),patch.object(runtime.ssl,'get_default_verify_paths',return_value=self.paths()):
            runtime.trust_bundled_certificates()
        self.assertNotIn('SSL_CERT_FILE',os.environ)


if __name__=='__main__':
    unittest.main()
