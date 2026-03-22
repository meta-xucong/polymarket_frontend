import paramiko
import textwrap

HOST = "43.251.227.106"
PORT = 31467
USER = "root"
PASSWORD = "Xu25942321"

remote_script = textwrap.dedent(
    """
    set -e
    export HOME=/root
    if test -d /root/.acme.sh; then
      echo acme_present
    else
      curl -fsSL https://get.acme.sh | sh -s email=admin@alcochrom.icu
    fi
    . /root/.acme.sh/acme.sh.env
    systemctl stop nginx
    /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt
    /root/.acme.sh/acme.sh --issue -d www.alcochrom.icu --standalone --alpn --keylength ec-256
    mkdir -p /etc/nginx/ssl
    /root/.acme.sh/acme.sh --install-cert -d www.alcochrom.icu --ecc \
      --fullchain-file /etc/nginx/ssl/www.alcochrom.icu.fullchain.pem \
      --key-file /etc/nginx/ssl/www.alcochrom.icu.key
    cat > /etc/nginx/sites-enabled/polymarket-panel.conf <<'EOF'
    server {
        listen 443 ssl http2 default_server;
        server_name www.alcochrom.icu;

        ssl_certificate /etc/nginx/ssl/www.alcochrom.icu.fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/www.alcochrom.icu.key;
        ssl_protocols TLSv1.2 TLSv1.3;

        location / {
            proxy_pass http://127.0.0.1:8787;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_read_timeout 300;
        }
    }
    EOF
    nginx -t
    systemctl start nginx
    systemctl reload nginx
    curl -sS https://www.alcochrom.icu/api/auth/session | sed -n '1,2p'
    openssl s_client -connect www.alcochrom.icu:443 -servername www.alcochrom.icu </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates
    """
).strip() + "\n"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=20)
sftp = client.open_sftp()
with sftp.open("/root/setup_domain_cert.sh", "w") as handle:
    handle.write(remote_script)
    handle.flush()
sftp.chmod("/root/setup_domain_cert.sh", 0o700)
sftp.close()
stdin, stdout, stderr = client.exec_command("bash /root/setup_domain_cert.sh", timeout=1200)
print(stdout.read().decode("utf-8", "replace"))
print(stderr.read().decode("utf-8", "replace"))
client.close()
