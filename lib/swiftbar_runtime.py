"""Locate dependencies prepared for the plugin's own Python interpreter."""
import os
from pathlib import Path
import platform
import ssl
import sys
import sysconfig


def vendor_dir():
    tag=sys.implementation.cache_tag+'-'+sysconfig.get_platform()+'-'+platform.machine()
    root=Path(__file__).resolve().parent.parent
    for vendor in (root/'python'/tag,root/'build/python'/tag):
        if vendor.is_dir():return vendor
    return None


def activate_vendor():
    vendor=vendor_dir()
    if vendor and str(vendor) not in sys.path:sys.path.insert(0,str(vendor))


def trust_bundled_certificates():
    """Python from python.org has no root certificates until its Install Certificates step
    runs, and then every HTTPS request fails. Only in that case, point OpenSSL at the pinned
    certifi set installed beside the plugin; a Python with its own store keeps using it."""
    if os.environ.get('SSL_CERT_FILE'):return
    paths=ssl.get_default_verify_paths()
    if paths.cafile or (paths.capath and any(Path(paths.capath).iterdir())):return
    vendor=vendor_dir()
    if vendor and (vendor/'certifi/cacert.pem').is_file():os.environ['SSL_CERT_FILE']=str(vendor/'certifi/cacert.pem')
