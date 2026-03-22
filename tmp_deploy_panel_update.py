import posixpath
from pathlib import Path

import paramiko

HOST = "43.251.227.106"
PORT = 31467
USER = "root"
PASSWORD = "Xu25942321"

LOCAL_ROOT = Path(r"D:\AI\vibe_coding4")
REMOTE_ROOT = "/opt/polyapp/current"

FILES = [
    r"POLYMARKET_MAKER_copytrade_v2\panel\server.py",
    r"POLYMARKET_MAKER_copytrade_v2\panel\static\index.html",
    r"POLYMARKET_MAKER_copytrade_v2\panel\static\app.js",
    r"POLYMARKET_MAKER_copytrade_v2\panel\tests\test_panel_integration.py",
    r"deploy\linux\install_instance.sh",
    r"deploy\linux\requirements-runtime.txt",
    r"deploy\panel.env.example",
]


def upload_file(sftp: paramiko.SFTPClient, relative_windows_path: str) -> None:
    local_path = LOCAL_ROOT / relative_windows_path
    remote_path = posixpath.join(REMOTE_ROOT, relative_windows_path.replace("\\", "/"))
    remote_dir = posixpath.dirname(remote_path)
    ensure_remote_dir(sftp, remote_dir)
    sftp.put(str(local_path), remote_path)


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def main() -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=20)
    sftp = client.open_sftp()
    for file_path in FILES:
        upload_file(sftp, file_path)
    sftp.close()

    commands = [
        "python3 -m py_compile /opt/polyapp/current/POLYMARKET_MAKER_copytrade_v2/panel/server.py",
        "rm -f /opt/polyapp/instances/user01/panel/auth.json",
        "python3 - <<'PY'\nfrom pathlib import Path\npath = Path('/etc/default/polymarket-panel-user01.env')\ntext = path.read_text(encoding='utf-8')\ntext = '\\n'.join(line for line in text.splitlines() if not line.startswith('POLY_AUTH_USERNAME=') and not line.startswith('POLY_AUTH_PASSWORD='))\nif 'POLY_AUTH_DEFAULT_USERNAME=' not in text:\n    text += '\\nPOLY_AUTH_DEFAULT_USERNAME=admin'\nif 'POLY_AUTH_DEFAULT_PASSWORD=' not in text:\n    text += '\\nPOLY_AUTH_DEFAULT_PASSWORD=admin'\npath.write_text(text.rstrip() + '\\n', encoding='utf-8')\nprint(path.read_text(encoding='utf-8'))\nPY",
        "systemctl restart polymarket-panel-user01",
        "python3 - <<'PY'\nimport json\nimport requests\ns = requests.Session()\nbase = 'https://127.0.0.1'\nr = s.get(base + '/api/auth/session', verify=False)\nprint('session_before', r.status_code, json.dumps(r.json(), ensure_ascii=True))\nr = s.post(base + '/api/auth/login', json={'username': 'admin', 'password': 'admin'}, verify=False)\nprint('login', r.status_code, json.dumps(r.json(), ensure_ascii=True))\nr = s.get(base + '/api/runtime', verify=False)\nprint('runtime_without_cookie', r.status_code, json.dumps(r.json(), ensure_ascii=True))\nr = s.get(base + '/api/runtime', verify=False, headers={'Cookie': s.cookies.get_dict() and '; '.join(f'{k}={v}' for k, v in s.cookies.get_dict().items())})\nprint('runtime_with_cookie', r.status_code, json.dumps(r.json(), ensure_ascii=True))\nr = s.post(base + '/api/auth/credentials', json={'username': 'adminops', 'password': 'adminops1', 'password_confirm': 'adminops1'}, verify=False)\nprint('credentials_update', r.status_code, json.dumps(r.json(), ensure_ascii=True))\nr = s.get(base + '/api/runtime', verify=False)\nprint('runtime_after_update', r.status_code, json.dumps(r.json(), ensure_ascii=True))\nPY",
        "rm -f /opt/polyapp/instances/user01/panel/auth.json",
        "systemctl restart polymarket-panel-user01",
    ]

    for command in commands:
        print(f'===CMD=== {command.splitlines()[0]}')
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        print(stdout.read().decode('utf-8', 'replace'))
        err = stderr.read().decode('utf-8', 'replace')
        if err:
            print('---ERR---')
            print(err)
    client.close()


if __name__ == "__main__":
    main()
