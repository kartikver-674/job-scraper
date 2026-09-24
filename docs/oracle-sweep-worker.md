# The sweep worker on Oracle

Long-running sweeps for the public beta, on the A1 that already hosts the
inference fallback. Render keeps the session and the UI; this owns the
processes that outlive a browser tab.

```
browser ──► Render Free (session, beta gate)
              └─server-side, bearer──► Caddy :443 /worker/* ──► :8812 sweep-worker
                                                                  └── scraper.py children
```

**The inference service is not touched by any of this.** Different unix user,
different port, different systemd unit, different bearer token, different
directory. The only shared file is the Caddyfile, which gains one route and
is reloaded gracefully — `caddy reload` does not restart anything else.

## Headroom (measured, 15 September 2026)

| | |
| --- | --- |
| Box | A1, 2 OCPU, 12 GB RAM, 50 GB disk |
| Already used | ~5.2 GB Ollama (capped `MemoryMax=9G`) + ~0.3 GB gunicorn + ~0.8 GB OS |
| One sweep | **262 MB peak, 6 s CPU per 171 s run** — it waits on the network |
| Verdict | One sweep at a time uses ~5% of what is spare. The queue exists for politeness to the inference service beside it, not for memory. |

## 0. Before you start

You need: SSH to the A1, and the Render service's environment page. Nothing
here runs against the inference service, and nothing needs its token.

```bash
# On the box, confirm what is already running. Read-only.
free -m; df -h /; systemctl is-active ollama sweep-inference caddy
```

## 1. A user, directories, and the code

The worker runs as its own unix user so it cannot read
`/etc/sweep-inference/env`.

```bash
sudo useradd --system --home /opt/sweep-worker --shell /usr/sbin/nologin sweepworker
sudo mkdir -p /opt/sweep-worker /var/lib/sweep-worker/runs /etc/sweep-worker
sudo chown -R sweepworker:sweepworker /opt/sweep-worker /var/lib/sweep-worker

sudo -u sweepworker git clone https://github.com/kartikver-674/job-scraper.git \
     /opt/sweep-worker/app
sudo -u sweepworker git -C /opt/sweep-worker/app checkout main
```

**Do not copy a `.env` into the checkout.** The worker must hold no Apify
token of its own: a public run is funded by the visitor who asked for it, or
it does not happen. The worker strips `APIFY_TOKEN*` out of every child's
environment and, since V2-D1, hands a paid run its visitor's keys on stdin
(`SWEEP_BYOK_CREDENTIALS=stdin`), on which the engine never calls
`load_dotenv()` or reads an `APIFY_TOKEN*` variable — so an accidental `.env`
is ignored by a visitor's run. (Before V2-D1 it was not: stripping the
environment did not stop the engine's own `load_dotenv()`.) Do not put one there.

## 2. Python

```bash
sudo -u sweepworker python3 -m venv /opt/sweep-worker/venv
sudo -u sweepworker /opt/sweep-worker/venv/bin/pip install --upgrade pip
sudo -u sweepworker /opt/sweep-worker/venv/bin/pip install \
     -r /opt/sweep-worker/app/requirements.txt
```

## 3. The worker's own bearer token

Known to this box and to Render. Never to a browser.

```bash
printf 'SWEEP_WORKER_TOKEN=%s\n' "$(openssl rand -hex 32)" \
  | sudo tee /etc/sweep-worker/env > /dev/null
sudo tee -a /etc/sweep-worker/env > /dev/null <<'EOF'
SWEEP_WORKER_RUNS=/var/lib/sweep-worker/runs
SWEEP_WORKER_CHECKOUT=/opt/sweep-worker/app
SWEEP_WORKER_TTL=172800
EOF
sudo chown root:sweepworker /etc/sweep-worker/env
sudo chmod 640 /etc/sweep-worker/env

# You need this value for Render in step 7. Print it once:
sudo grep SWEEP_WORKER_TOKEN /etc/sweep-worker/env
```

## 4. The service

`workers 1` is not a default — the queue, the child table and the janitor live
in this process's memory. A second worker would run a second sweep and neither
would know about the other.

```bash
sudo tee /etc/systemd/system/sweep-worker.service > /dev/null <<'EOF'
[Unit]
Description=Sweep worker (public beta sweeps)
After=network-online.target
Wants=network-online.target

[Service]
User=sweepworker
Group=sweepworker
# NOT the checkout, deliberately. gunicorn auto-loads ./gunicorn.conf.py,
# and the checkout's belongs to the INFERENCE service — it binds
# 127.0.0.1:8811. Started from there, this worker would silently take the
# inference service's own configuration, which is the one boundary this
# whole design exists to keep. PYTHONPATH is what makes deploy.* importable
# from outside the tree.
WorkingDirectory=/var/lib/sweep-worker
Environment=PYTHONPATH=/opt/sweep-worker/app
EnvironmentFile=/etc/sweep-worker/env
ExecStart=/opt/sweep-worker/venv/bin/gunicorn \
  --workers 1 --threads 8 --timeout 120 \
  --bind 127.0.0.1:8812 \
  --access-logfile - --error-logfile - \
  'deploy.sweep_worker:main()'
Restart=always
RestartSec=5

# The sweep child is spawned inside this cgroup, so the ceiling covers
# both. 2 GB leaves the inference service and Ollama their room; if this
# is what runs out of memory, it is this that dies, not qwen3.
MemoryMax=2G
MemorySwapMax=0
# Politeness to the inference service: a sweep is network-bound and does
# not need a whole core, so it can never starve a model call.
CPUQuota=120%

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/sweep-worker /opt/sweep-worker/app/profiles /opt/sweep-worker/app/output
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sweep-worker
systemctl status sweep-worker --no-pager | head -12
```

`ProtectSystem=strict` plus that `ReadWritePaths` list is what stops this
service writing anywhere near the inference deployment.

## 5. Caddy: one route, reloaded not restarted

Back the config up first, then add the route **inside the existing site block**
for `sweep-inference.duckdns.org`, above the current `reverse_proxy`:

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
sudoedit /etc/caddy/Caddyfile
```

```caddyfile
	# The sweep worker. handle_path strips /worker, so the service sees
	# /v1/runs. Its own bearer token: a leak of one service's credential
	# does not grant the other.
	handle_path /worker/* {
		reverse_proxy 127.0.0.1:8812
	}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile   # refuses to reload a broken file
sudo systemctl reload caddy                          # graceful; inference keeps serving
curl -s https://sweep-inference.duckdns.org/healthz  # unchanged, still ok
```

If validate fails: `sudo cp /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile && sudo systemctl reload caddy`.

## 6. Verify

```bash
TOKEN=$(sudo grep -oP 'SWEEP_WORKER_TOKEN=\K.*' /etc/sweep-worker/env)

# Locally, before it is reachable from anywhere else.
curl -s -H "Authorization: Bearer $TOKEN" localhost:8812/healthz
# -> {"max_active":1,"queued":0,"running":0,"status":"ok"}

# No token: refused. Every endpoint, health included.
curl -s -o /dev/null -w '%{http_code}\n' localhost:8812/healthz          # 401

# Through Caddy, the way Render will reach it.
curl -s -H "Authorization: Bearer $TOKEN" \
     https://sweep-inference.duckdns.org/worker/healthz

# And the inference service is exactly as it was.
curl -s https://sweep-inference.duckdns.org/healthz

# The two services are on their own ports, and nothing moved.
sudo ss -lntp | grep -E '8811|8812'
# -> 8811 the inference gunicorn, 8812 the worker. If the worker appears
#    on 8811, it picked up the checkout's gunicorn.conf.py: check
#    WorkingDirectory in the unit above.
```

## 7. What Render needs

Add to the `sweep-beta` service's environment (values only in the dashboard):

| Variable | Value |
| --- | --- |
| `SWEEP_WORKER_URL` | `https://sweep-inference.duckdns.org/worker` |
| `SWEEP_WORKER_TOKEN` | the value from step 3 |

The browser never sees either. Render calls the worker server-side; the
browser polls Render.

## How it behaves

| Event | What happens |
| --- | --- |
| Visitor closes the tab | The sweep keeps running here. Render picks it back up from the `run_id` in the session cookie. |
| Render spins down or redeploys | Same — the run is untouched, and results wait on this disk. |
| Worker restarts mid-sweep | That run becomes `interrupted` with a reason. Queued **free** runs are re-queued in arrival order. |
| Worker restarts with a paid run queued | `interrupted`, because the Apify token was deliberately never written down. The visitor starts it again. |
| Two visitors at once | One runs, the rest queue FIFO and can see their position. |
| A run finishes | Results stay for 48 h, then the janitor deletes them — never while queued or running. |

## Operating it

```bash
journalctl -u sweep-worker -f                    # what it is doing
sudo du -sh /var/lib/sweep-worker/runs           # what it is holding
sudo systemctl restart sweep-worker              # safe: see the table above
ls /opt/sweep-worker/app/profiles/beta_*.py | wc -l   # rendered run profiles
```

Update the code:

```bash
sudo -u sweepworker git -C /opt/sweep-worker/app pull
sudo systemctl restart sweep-worker
```

## Removing it

Nothing here is load-bearing for inference, so removal is local:

```bash
sudo systemctl disable --now sweep-worker
sudo rm /etc/systemd/system/sweep-worker.service && sudo systemctl daemon-reload
sudo cp /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile && sudo systemctl reload caddy
sudo rm -rf /opt/sweep-worker /var/lib/sweep-worker /etc/sweep-worker
sudo userdel sweepworker
```
