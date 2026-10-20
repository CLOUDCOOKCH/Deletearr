"""Smoke-test a built image with no external network or real media. Cleans up only its own resources."""

import json
import subprocess
import sys
import time
import uuid

image = sys.argv[1] if len(sys.argv) > 1 else "deletarr:verification"
name = "deletarr-smoke-" + uuid.uuid4().hex[:12]
volume = name + "-config"


def docker(*args):
    return subprocess.check_output(
        ["docker", "--host=unix:///var/run/docker.sock", *args], text=True, stderr=subprocess.PIPE
    ).strip()


probe = """
import io,json,os,pathlib,secrets,tarfile,urllib.request,urllib.error
base='http://127.0.0.1:8980'
assert os.getuid()!=0
assert pathlib.Path('/config/deletarr.db').stat().st_mode & 0o077 == 0
assert json.load(urllib.request.urlopen(base+'/api/health'))['status']=='ok'
assert b'Deletarr' in urllib.request.urlopen(base+'/').read()
try:
 urllib.request.urlopen(base+'/api/settings')
 raise AssertionError('Management API is unauthenticated')
except urllib.error.HTTPError as e: assert e.code==401
status=json.load(urllib.request.urlopen(base+'/api/auth/status'))
if status['setup_required']:
 payload={'username':'smoke-admin','password':secrets.token_urlsafe(32),'setup_token':pathlib.Path('/config/setup-token').read_text().strip()}
 request=urllib.request.Request(base+'/api/auth/setup',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Origin':base})
 response=urllib.request.urlopen(request)
 cookie=response.headers['Set-Cookie']
 assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
 csrf=json.load(response)['csrf']
 session=cookie.split(';')[0]
 request=urllib.request.Request(base+'/api/settings',headers={'Cookie':session})
 assert json.load(urllib.request.urlopen(request))['dry_run'] is True
 request=urllib.request.Request(base+'/api/source',headers={'Cookie':session})
 archive=tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(request).read()),mode='r:gz')
 names=archive.getnames()
 assert any(n.endswith('/web/src/main.tsx') for n in names)
 assert any(n.endswith('/deletarr/executor.py') for n in names)
 assert not any(n.endswith('/credentials.key') or n.endswith('/.env') for n in names)
 assert not pathlib.Path('/config/setup-token').exists()
print('health, static UI, authentication, dry-run, permissions and source distribution: passed')
"""
try:
    docker("volume", "create", volume)
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        volume + ":/config",
        image,
    )
    for attempt in range(30):
        if docker("inspect", "--format", "{{.State.Running}}", name) != "true":
            print(docker("logs", name))
            raise RuntimeError("Disposable smoke container exited during startup")
        try:
            docker(
                "exec",
                name,
                "python",
                "-c",
                "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8980/api/health',timeout=1)",
            )
            break
        except subprocess.CalledProcessError:
            time.sleep(1)
    else:
        raise RuntimeError("Container never became ready")
    print(docker("exec", name, "python", "-c", probe))
    docker("restart", "--time", "120", name)
    for attempt in range(30):
        try:
            print(docker("exec", name, "python", "-c", probe))
            break
        except subprocess.CalledProcessError:
            time.sleep(1)
    else:
        raise RuntimeError("Container failed restart validation")
    info = json.loads(docker("inspect", name))[0]
    assert info["Config"]["User"] == "deletarr:deletarr"
    assert info["HostConfig"]["ReadonlyRootfs"]
    print("Persistent administrator and SQLite state after restart: passed")
finally:
    subprocess.run(["docker", "--host=unix:///var/run/docker.sock", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "--host=unix:///var/run/docker.sock", "volume", "rm", volume], capture_output=True)
