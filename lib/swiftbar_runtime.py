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


def quiet_subprocess():
    """A tray app has no console; avoid flashing one for background CLI reads."""
    import subprocess
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}


def cli_args(binary,*args):
    """Invoke trusted PATH-resolved npm shims on Windows without expanding paths or arguments."""
    import subprocess
    if os.name!='nt' or Path(binary).suffix.lower() not in ('.bat','.cmd'):
        return [str(binary),*args]
    binary=str(Path(binary).resolve())
    if any(c in binary for c in '%!\r\n"') or any(not arg or not arg.replace('-','').isalnum() for arg in args):
        raise ValueError('unsupported CLI path or arguments')
    cmd=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'cmd.exe'
    return subprocess.list2cmdline([str(cmd)])+' /d /v:off /s /c ""'+binary+'" '+' '.join(args)+'"'
