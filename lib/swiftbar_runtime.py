"""Locate dependencies prepared for the plugin's own Python interpreter."""
from pathlib import Path
import platform
import sys
import sysconfig


def activate_vendor():
    tag=sys.implementation.cache_tag+'-'+sysconfig.get_platform()+'-'+platform.machine()
    root=Path(__file__).resolve().parent.parent
    for vendor in (root/'python'/tag,root/'build/python'/tag):
        if vendor.is_dir():
            if str(vendor) not in sys.path:sys.path.insert(0,str(vendor))
            return
