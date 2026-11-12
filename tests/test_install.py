"""Installer staging and rollback, without SwiftBar or a real plugin folder."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('install_script',ROOT/'scripts/install.py')
install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.dest=Path(self.temp.name)/'plugins';self.panel=self.dest/'.opencodex-panel'
        (self.panel/'lib').mkdir(parents=True);(self.panel/'lib/old.py').write_text('old')
        (self.panel/'OpenCodexPanel.app').mkdir();(self.panel/'OpenCodexPanel.app/old-file').write_text('old')
        self.transport=Path(self.temp.name)/'cp39-macosx';self.transport.mkdir();(self.transport/'pkg.py').write_text('new')
        # A package tree of its own: the real helper isn't built yet when the unit tests run.
        package=Path(self.temp.name)/'package'
        for name in ('build/OpenCodexPanel.app/Contents/Info.plist','assets/logo.svg','lib/swiftbar_plugin.py'):
            (package/name).parent.mkdir(parents=True,exist_ok=True);(package/name).write_text('new')
        root=patch.object(install,'ROOT',package);root.start();self.addCleanup(root.stop)
        self.signature_ok=True;real=subprocess.run
        def run(command,*args,**kwargs):
            if command[0]=='codesign':
                if not self.signature_ok:raise subprocess.CalledProcessError(1,command)
                return subprocess.CompletedProcess(command,0)
            return real(command,*args,**kwargs)
        signed=patch.object(install.subprocess,'run',side_effect=run);signed.start();self.addCleanup(signed.stop)
    def test_a_helper_that_fails_its_signature_check_changes_nothing(self):
        self.signature_ok=False
        with self.assertRaises(subprocess.CalledProcessError):install.stage(self.dest,self.transport)
        self.assertEqual(sorted(p.name for p in self.panel.iterdir()),['OpenCodexPanel.app','lib'])
        self.assertEqual((self.panel/'OpenCodexPanel.app/old-file').read_text(),'old')
    def test_parts_are_replaced_whole_and_put_back_when_a_later_step_fails(self):
        staging=install.stage(self.dest,self.transport)
        self.assertTrue((staging/'lib/swiftbar_plugin.py').is_file());self.assertTrue((staging/'python/cp39-macosx/pkg.py').is_file())
        pairs=[(staging/'OpenCodexPanel.app',self.panel/'OpenCodexPanel.app'),(staging/'lib',self.panel/'lib'),
               (staging/'python/cp39-macosx',self.panel/'python/cp39-macosx')]
        moved=[];install.swap(pairs,staging,moved)
        # Whole parts: a file the new version dropped doesn't linger beside it.
        self.assertFalse((self.panel/'lib/old.py').exists());self.assertFalse((self.panel/'OpenCodexPanel.app/old-file').exists())
        install.restore(moved)
        self.assertEqual((self.panel/'lib/old.py').read_text(),'old');self.assertEqual((self.panel/'OpenCodexPanel.app/old-file').read_text(),'old')
        self.assertFalse((self.panel/'python/cp39-macosx').exists())  # It wasn't there before.
        self.assertFalse((self.panel/'lib/swiftbar_plugin.py').exists())
    def test_a_rename_that_fails_midway_undoes_the_parts_already_moved(self):
        staging=install.stage(self.dest,self.transport)
        pairs=[(staging/'lib',self.panel/'lib'),(staging/'missing',self.panel/'OpenCodexPanel.app')]
        moved=[]
        with self.assertRaises(FileNotFoundError):install.swap(pairs,staging,moved)
        install.restore(moved)
        self.assertEqual((self.panel/'lib/old.py').read_text(),'old');self.assertEqual((self.panel/'OpenCodexPanel.app/old-file').read_text(),'old')

    def backups(self,names):
        root=Path(self.temp.name)/'backups'
        for name in names:(root/name).mkdir(parents=True)
        return root
    def test_only_the_newest_install_backups_are_kept(self):
        old=['20261101-10000%d-abcde%d'%(i,i) for i in range(7)]+['20991231-235959-ffffff']
        others=['removed-plugins-20261105-143627','configuration-cleanup-20261105-144939','notes']
        root=self.backups(old+others)
        (root/'20261101-090000-aaaaaa.zip').write_text('a file, not a backup')
        outside=Path(self.temp.name)/'elsewhere';outside.mkdir();(outside/'keep').write_text('kept')
        (root/'20261101-080000-bbbbbb').symlink_to(outside,target_is_directory=True)
        (self.dest/'token-monitor.30s.sh').write_text('old entry')
        with redirect_stdout(io.StringIO()) as said:
            backup=install.back_up(self.dest,[Path('token-monitor.30s.sh'),Path('.opencodex-panel/lib')],root)
        self.assertEqual(said.getvalue(),'Removed 4 older backups\n')
        self.assertEqual((backup/'token-monitor.30s.sh').read_text(),'old entry');self.assertEqual((backup/'.opencodex-panel/lib/old.py').read_text(),'old')
        # The new backup stays even when the clock once ran ahead; four others stay with it.
        kept=sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and install.BACKUP_NAME.fullmatch(p.name))
        self.assertEqual(kept,sorted(old[4:]+[backup.name]))
        self.assertTrue(all((root/name).is_dir() for name in others));self.assertTrue((root/'20261101-090000-aaaaaa.zip').is_file())
        self.assertTrue((root/'20261101-080000-bbbbbb').is_symlink());self.assertEqual((outside/'keep').read_text(),'kept')
    def test_a_failed_backup_prunes_nothing_and_a_failed_prune_installs_anyway(self):
        old=['20261101-10000%d-abcde%d'%(i,i) for i in range(7)];root=self.backups(old)
        with patch.object(install,'copy',side_effect=OSError('disk full')),self.assertRaises(OSError):
            install.back_up(self.dest,[Path('.opencodex-panel/lib')],root)
        self.assertTrue(all((root/name).is_dir() for name in old))
        with patch.object(install.shutil,'rmtree',side_effect=OSError('busy')):
            backup=install.back_up(self.dest,[Path('.opencodex-panel/lib')],root)
        self.assertTrue((backup/'.opencodex-panel/lib/old.py').is_file());self.assertTrue(all((root/name).is_dir() for name in old))


if __name__=='__main__':
    unittest.main()
