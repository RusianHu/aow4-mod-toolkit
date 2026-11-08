"""Pinned Microsoft DirectXTex CPU tool; downloaded only by explicit command."""
import json
from pathlib import Path
import subprocess
import urllib.request

from .core import FormatError
from .filesystem import file_hash,new_directory,save_record

SHA256='dcfdec10244e02cf5037fba089c55fb7e1326b1c8181742d77d15fa5cb5eef06'
URL='https://github.com/microsoft/DirectXTex/releases/download/may2026/texconv.exe'
VERSION='2026.5.8.1'


def inspect(path):
    path=Path(path).resolve()
    if not path.is_file() or file_hash(path)!=SHA256:raise FormatError('texconv must match the reviewed Microsoft may2026 x64 release SHA-256')
    return {'file':str(path),'sha256':SHA256,'version':VERSION,'source':URL}


def download(output,proxy=None):
    output=Path(output).resolve()
    with new_directory(output) as stage:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy,'https':proxy}) if proxy else urllib.request.ProxyHandler())
        request=urllib.request.Request(URL,headers={'User-Agent':'AoW4ModToolkit'})
        with opener.open(request,timeout=60) as response:data=response.read(16*1024*1024+1)
        if len(data)>16*1024*1024:raise FormatError('texconv download exceeds bound')
        file=stage/'texconv.exe';file.write_bytes(data);inspect(file)
        save_record(stage/'toolchain.json',{'sha256':SHA256,'version':VERSION,'source':URL})
    return inspect(output/'texconv.exe')


def run(tool,args,folder,timeout=180):
    binding=inspect(tool);folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    if not 1<=timeout<=600:raise FormatError('Texture codec timeout must be 1..600 seconds')
    command=[binding['file'],'-nologo',*map(str,args)]
    with (folder/'stdout.log').open('wb') as stdout,(folder/'stderr.log').open('wb') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,cwd=folder,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=5);raise FormatError('Texture codec exceeded its timeout; evidence: '+str(folder))
    if file_hash(binding['file'])!=binding['sha256']:raise FormatError('Texture tool changed during conversion')
    record={'tool':binding,'command':command,'exit_code':code,'stdout_sha256':file_hash(folder/'stdout.log'),'stderr_sha256':file_hash(folder/'stderr.log')}
    save_record(folder/'process.json',record)
    if code:raise FormatError('Texture codec failed; evidence: '+str(folder))
    return record
