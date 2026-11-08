from pathlib import Path
import json
import subprocess
import sys
import unittest


class NativeOutputTests(unittest.TestCase):
    def test_native_fd_and_win32_output_go_to_stderr_and_exception_restores_stdout(self):
        root=Path(__file__).resolve().parents[1]
        script=r'''
import os,sys,json,ctypes as c
from aow4mod.steam_transport import native_output_to_stderr
try:
 with native_output_to_stderr():
  os.write(1,b'native-fd\n')
  if os.name=='nt':
   k=c.WinDLL('kernel32');k.GetStdHandle.argtypes=[c.c_int32];k.GetStdHandle.restype=c.c_void_p
   k.WriteFile.argtypes=[c.c_void_p,c.c_void_p,c.c_uint32,c.POINTER(c.c_uint32),c.c_void_p]
   raw=c.create_string_buffer(b'native-win32\n');count=c.c_uint32()
   assert k.WriteFile(k.GetStdHandle(-11),raw,len(raw.value),c.byref(count),None)
  raise RuntimeError('test restoration')
except RuntimeError: pass
print(json.dumps({'stdout_restored':True}))
'''
        environment=dict(__import__('os').environ,PYTHONPATH=str(root/'scripts'))
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,env=environment,cwd=root.parent)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'stdout_restored':True})
        self.assertIn('native-fd',result.stderr)
        if sys.platform=='win32':self.assertIn('native-win32',result.stderr)


if __name__=='__main__':unittest.main()
