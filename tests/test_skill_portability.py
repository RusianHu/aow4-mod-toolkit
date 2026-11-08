"""Integration checks for a copied Skill runtime without its original ancestors."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import __version__,native


class SkillPortabilityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.runtime=self.root/'portable skill'/ 'scripts';self.runtime.mkdir(parents=True)
        original=Path(__file__).resolve().parents[1]/'scripts'
        shutil.copytree(original/'aow4mod',self.runtime/'aow4mod',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copyfile(original/'aow4_mod_audit.py',self.runtime/'aow4_mod_audit.py')
        self.environment={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}

    def run_isolated(self,*arguments):
        result=subprocess.run([sys.executable,'-I',*arguments],cwd=self.root,env=self.environment,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def test_direct_cli_runs_outside_the_original_project(self):
        entry=str(self.runtime/'aow4_mod_audit.py')
        self.assertEqual(self.run_isolated(entry,'--version').strip(),__version__)
        capabilities=json.loads(self.run_isolated(entry,'capabilities','authoring'))
        self.assertIn('project-new',capabilities['areas']['authoring']['commands'])

    def test_copied_package_import_and_native_fingerprint_use_only_bundled_sources(self):
        code='import sys,json;sys.path.insert(0,'+repr(str(self.runtime))+');from aow4mod import native;print(json.dumps({"file":native.__file__,"fingerprint":native.backend_fingerprint()}))'
        result=json.loads(self.run_isolated('-c',code))
        self.assertTrue(Path(result['file']).resolve().is_relative_to(self.runtime.resolve()))
        self.assertEqual(result['fingerprint'],native.backend_fingerprint())


if __name__=='__main__':unittest.main()
