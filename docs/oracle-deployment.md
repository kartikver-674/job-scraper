# Deploying the inference service on Oracle Cloud Always Free

A runbook. Follow it top to bottom on a fresh instance; every command is meant to
be pasted. **Nothing here has been run against a real Oracle instance** — it is
derived from the service's actual code and the measurements in
[inference-hosting.md](inference-hosting.md), and the verification section is how
you prove each step rather than trust it.

**Target architecture** (decided in [inference-hosting.md §7](inference-hosting.md)):

```
   your laptop ──HTTPS :443──► Caddy ──► gunicorn :8811 ──► Ollama :11434 ──► qwen3:8b
                              (public)   (127.0.0.1)        (127.0.0.1)
                   ╰─────────────────── one Oracle A1 VM, 2 OCPU / 12 GB ────────────╯
```

Only :443 (and :80, for certificate issuance) is ever public. 8811 and 11434 must
be unreachable from anywhere but the box itself, and §13 is how you prove it.

---

## 0. What ships, and what does not

**Three files go to the server:**

```
inference.py          the provider boundary + the Ollama transport
inference_service.py  the Flask service
gunicorn.conf.py      worker/timeout settings, derived from MAX_TIMEOUT
```

Plus `flask` and `gunicorn`. That is the whole dependency list — everything else
is the standard library.

**The résumé pipeline does not ship.** No `local_extract.py`, no `local_search`,
no prompts, no schema, no corpus, no scraper. The service is handed a prompt and
a JSON Schema by the client and has no idea what a résumé is. That is worth
saying out loud because it bounds the blast radius: a compromise of this box
leaks whatever is in flight, and nothing at rest, because nothing is at rest.

**Prerequisites**

- An Oracle Cloud account with Always Free eligibility, in a region where A1
  capacity exists. "Out of host capacity" on creation is common — retry, or try
  another availability domain.
- A domain name (or subdomain) you can point at the instance. Caddy needs it for
  Let's Encrypt. Without one, see §10.3.
- An SSH keypair.

---

## 1. Provision the instance

Oracle Console → Compute → Instances → **Create instance**.

| Field | Value |
| --- | --- |
| Image | **Canonical Ubuntu 24.04** (aarch64 build) |
| Shape | **VM.Standard.A1.Flex** |
| OCPUs | **2** |
| Memory | **12 GB** |
| Boot volume | 50 GB (the 47 GB minimum plus room; the model is 5.2 GB) |
| VNIC | Assign a public IPv4 address |
| SSH key | Paste your public key |

2 OCPU / 12 GB is the entire Always Free ARM allowance as of June 2026 (it was
4/24 before; Oracle halved it without announcing). Do not create a second A1
instance — you have no allowance left, and the excess gets terminated.

Note the public IP. Point your domain's `A` record at it now, so DNS has
propagated by the time Caddy asks for a certificate.

---

## 2. Oracle security list — the first of two firewalls

Oracle blocks at the **virtual network** layer *and* the instance ships its own
**iptables** rules. Both must allow a port. Forgetting the second is the single
most common reason an Oracle instance appears to ignore its security list.

Console → Networking → Virtual Cloud Networks → *your VCN* → Security Lists →
*Default Security List* → **Ingress Rules**.

You want exactly three ingress rules, and nothing else:

| Source CIDR | Protocol | Dest. port | Why |
| --- | --- | --- | --- |
| `<your.laptop.ip>/32` | TCP | 22 | SSH. Narrow it to your own IP; `0.0.0.0/0` on 22 is an invitation |
| `0.0.0.0/0` | TCP | 80 | Let's Encrypt HTTP-01 challenge, and Caddy's redirect to HTTPS |
| `0.0.0.0/0` | TCP | 443 | The API |

**Delete or never create any rule for 8811 or 11434.** If one exists, remove it
before going further.

---

## 3. First login, and the instance's own firewall

```bash
# Substitute your own values once; the rest of this runbook reuses them.
IP=203.0.113.10                 # the instance's public IP
DOMAIN=infer.example.com        # the name whose A record points at it

ssh ubuntu@$IP
```

Ubuntu images on Oracle come with an iptables ruleset whose `INPUT` chain rejects
everything except SSH. Look at it before you change it:

```bash
sudo iptables -L INPUT --line-numbers -n
```

You will see an `ACCEPT` for port 22 and, near the bottom, a
`REJECT ... icmp-host-prohibited`. Insert the two new rules **above** that reject
— substitute the reject's line number for `6` if yours differs:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

`netfilter-persistent save` is not optional — without it the rules vanish on
reboot and the service comes back invisible.

Confirm 8811 and 11434 appear nowhere:

```bash
sudo iptables -L INPUT -n | grep -E "8811|11434" || echo "good: neither port is open"
```

Then update the OS:

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3-venv python3-pip curl ca-certificates
```

Ubuntu 24.04 ships Python 3.12. The service needs 3.10+; no newer syntax is used.

---

## 4. A user and a home for the service

Run it as its own unprivileged user that cannot log in:

```bash
sudo useradd --system --create-home --home-dir /opt/sweep-inference \
             --shell /usr/sbin/nologin sweep
sudo install -d -o sweep -g sweep -m 0755 /opt/sweep-inference/app
```

---

## 5. Python environment

```bash
sudo -u sweep python3 -m venv /opt/sweep-inference/venv
sudo -u sweep /opt/sweep-inference/venv/bin/pip install --upgrade pip
sudo -u sweep /opt/sweep-inference/venv/bin/pip install "flask>=3.0.0" "gunicorn>=21.0.0"
```

Both are pure Python and install on ARM64 without a compiler.

---

## 6. Copy the three files

**From your laptop**, in the repo root:

```bash
scp inference.py inference_service.py gunicorn.conf.py ubuntu@$IP:/tmp/
```

**On the server:**

```bash
sudo install -o sweep -g sweep -m 0644 \
     /tmp/inference.py /tmp/inference_service.py /tmp/gunicorn.conf.py \
     /opt/sweep-inference/app/
rm /tmp/inference.py /tmp/inference_service.py /tmp/gunicorn.conf.py
```

(If you would rather `git clone` the repo so updates are `git pull`, that works
too — point the unit's `WorkingDirectory` at the checkout. Three files by `scp`
is the smaller thing, and it makes the "no pipeline on the server" property
physically true rather than merely intended.)

---

## 7. Ollama

### 7.1 Install and pull the model

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull qwen3:8b          # ~5.2 GB; several minutes on a 2-OCPU box
ollama list
```

The installer creates and starts `ollama.service` and an `ollama` system user.

### 7.2 The `OLLAMA_HOST` footgun

`OLLAMA_HOST` means **two different things** on this box:

- To **Ollama's own service**, it is *what address to bind to*.
- To **our inference service**, it is *what address to connect to*.

Setting it globally — in `/etc/environment`, or in a profile script — sets both.
If the value is `0.0.0.0`, **you have just published an unauthenticated LLM on the
public internet.**

So: never set it globally, never set it in `ollama.service`. Ollama's default
bind is already `127.0.0.1:11434`, which is what we want. Our service gets its own
copy in its own `EnvironmentFile` (§8).

Verify Ollama is loopback-only:

```bash
sudo ss -lntp | grep 11434
# must show 127.0.0.1:11434 — if it shows 0.0.0.0:11434 or *:11434, stop and fix it
```

### 7.3 Bound memory

Give Ollama a hard ceiling so that memory pressure kills *it* rather than sshd.
See §16.4 for the reasoning and the arithmetic.

```bash
sudo systemctl edit ollama
```

Add:

```ini
[Service]
MemoryMax=9G
MemorySwapMax=0
Restart=always
RestartSec=5
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

---

## 8. The bearer token

Generate it on the server, put it in a file only `sweep` and root can read, and
never in the unit file — unit files are world-readable.

```bash
sudo install -d -o root -g sweep -m 0750 /etc/sweep-inference
printf 'SWEEP_INFERENCE_TOKEN=%s\n' "$(openssl rand -hex 32)" \
  | sudo tee /etc/sweep-inference/env > /dev/null
sudo chown root:sweep /etc/sweep-inference/env
sudo chmod 0640 /etc/sweep-inference/env
```

Now append the rest of the configuration:

```bash
sudo tee -a /etc/sweep-inference/env > /dev/null <<'EOF'
# --- where to listen. Caddy is the only thing that may reach us. ---
SWEEP_INFERENCE_HOST=127.0.0.1
SWEEP_INFERENCE_PORT=8811

# --- one model, one generation at a time. 2 OCPU cannot do two. ---
SWEEP_INFERENCE_WORKERS=1
SWEEP_INFERENCE_QUEUE_WAIT=30

# --- the runtime. This copy of OLLAMA_HOST is a CONNECT target. ---
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b

# --- Oracle ONLY, and not for speed. ---
# Pins the model resident so memory utilisation stays near 44% of 12 GB,
# above the 20% floor below which Oracle reclaims an idle Always Free
# instance. Measured: it does NOT speed up a single profile (the two model
# calls are back to back), and saves ~2.9s between two users' profiles.
# The application default stays 30s and is correct on a laptop.
# Do not "optimise" this back to 30s — you will lose the instance.
SWEEP_MODEL_KEEP_ALIVE=-1
EOF
```

Read the token back once — you need it on your laptop:

```bash
sudo grep SWEEP_INFERENCE_TOKEN /etc/sweep-inference/env
```

---

## 9. The inference service unit

```bash
sudo tee /etc/systemd/system/sweep-inference.service > /dev/null <<'EOF'
[Unit]
Description=Sweep inference service (gunicorn -> Ollama -> qwen3:8b)
Documentation=https://github.com/your-org/job-scraper/blob/main/docs/inference-service.md
# Wants, not Requires: the service starts and reports `degraded` when the
# runtime is down, rather than refusing to boot. Ollama is a separate
# process with its own restart, and "Ollama is slow to come up" should not
# become an outage that needs a human.
Wants=ollama.service
After=network-online.target ollama.service

[Service]
Type=exec
User=sweep
Group=sweep
WorkingDirectory=/opt/sweep-inference/app
EnvironmentFile=/etc/sweep-inference/env
ExecStart=/opt/sweep-inference/venv/bin/python -m inference_service
Restart=always
RestartSec=5

# Longer than gunicorn's own graceful_timeout (330s), which is itself longer
# than the app's MAX_TIMEOUT (300s). systemd must be the last thing to lose
# patience, or it kills a generation that was about to finish.
TimeoutStopSec=360
KillSignal=SIGTERM

# It writes nothing and needs nothing.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sweep-inference
sudo systemctl status sweep-inference --no-pager
```

`python -m inference_service` validates the token and configuration, prints a
startup report, then **execs gunicorn** with `gunicorn.conf.py`. There is no
separate development server. From that file, unchanged:

| | |
| --- | --- |
| `workers` | 1 — the model slot is a `threading.Semaphore`, so it is per-process |
| `threads` | 8 — so `/healthz` and auth failures answer during a generation |
| `timeout` | **360 s** — gunicorn kills a worker that exceeds it. Its default of 30 s would kill this service mid-generation on its ordinary workload |
| `graceful_timeout` | 330 s — an in-flight generation finishes on SIGTERM |

---

## 10. Caddy and TLS

### 10.1 Install

```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
```

### 10.2 Configure

Replace `infer.example.com` with your domain:

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
infer.example.com {
	# Automatic HTTPS: Caddy obtains and renews a Let's Encrypt certificate
	# and redirects :80 to :443 on its own. Nothing to schedule.

	encode gzip

	# Only the two routes that exist. Flask auto-registers a /static/<path>
	# route we never use; it 404s harmlessly, but there is no reason to
	# expose a route the application does not have.
	@api path /v1/generate /healthz
	handle @api {
		reverse_proxy 127.0.0.1:8811 {
			# Longer than the app's 300s MAX_TIMEOUT, so the application
			# deadline is always the one that fires. The app's timeout
			# produces a `model_timeout` category the caller can act on;
			# a proxy timeout produces a dropped connection nobody can
			# categorise.
			transport http {
				read_timeout 360s
				write_timeout 360s
			}
		}
	}

	handle {
		respond "not found" 404
	}

	log {
		output file /var/log/caddy/access.log {
			roll_size 10MiB
			roll_keep 5
		}
	}
}
EOF

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy's access log records method, path, status and client IP — never a body, so
no prompt reaches it.

### 10.3 If you have no domain

Let's Encrypt will not issue for a bare IP. Either:

- use a free subdomain (DuckDNS, nip.io with a real provider, etc.) and point it
  at the instance; or
- replace the site address with `:443` and add `tls internal`, which issues a
  Caddy-signed certificate. Your client must then trust that CA, and
  `SWEEP_INFERENCE_URL` pointing at `https://<ip>` will fail verification until
  it does. Workable for a POC, ugly for anything else.

---

## 11. Bring it up, in order

```bash
sudo systemctl enable --now ollama
sudo systemctl enable --now sweep-inference
sudo systemctl enable --now caddy
systemctl is-active ollama sweep-inference caddy
```

---

## 12. Verify on the box

```bash
# 1. Exactly three listeners, and two of them are loopback-only.
sudo ss -lntp | grep -E "11434|8811|:443|:80"
#   127.0.0.1:11434   ollama
#   127.0.0.1:8811    gunicorn
#   *:443, *:80       caddy
# Any 0.0.0.0 or * on 11434 or 8811 is a STOP-AND-FIX.

# 2. The service is healthy and can see the runtime.
curl -s localhost:8811/healthz
# {"model":"qwen3:8b","model_slots":1,"runtime_reachable":true,"status":"ok"}

# 3. The startup report says what it thinks it is.
sudo journalctl -u sweep-inference -n 30 --no-pager | grep -E "bind|model|slots|keep_alive"
#   keep_alive -1  <- confirms the Oracle-only setting is in force

# 4. A real generation, end to end, through Caddy.
TOKEN=$(sudo grep -oP '(?<=SWEEP_INFERENCE_TOKEN=).*' /etc/sweep-inference/env)
curl -s -X POST https://infer.example.com/v1/generate \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","prompt":"Reply with the name Ada Okonkwo.",
       "schema":{"type":"object","properties":{"name":{"type":"string"}},
                 "required":["name"]},"timeout":280}'
# {"duration_ms":...,"model":"qwen3:8b","request_id":"...","result":{"name":"Ada Okonkwo"}}
```

Expect the first generation to take noticeably longer than later ones — the model
is being loaded. After that, `SWEEP_MODEL_KEEP_ALIVE=-1` keeps it resident.

---

## 13. Verify from your laptop that only HTTPS is exposed

This is the step that matters. Run it from a machine that is **not** the
instance. `$HOST` is your domain or public IP.

```bash
HOST=$DOMAIN        # or the public IP, if you used `tls internal`

# --- 1. HTTPS works, with a valid certificate -------------------------
curl -sS https://$HOST/healthz
# {"model":"qwen3:8b","model_slots":1,"runtime_reachable":true,"status":"ok"}

# --- 2. Authentication is enforced ------------------------------------
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$HOST/v1/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"x","schema":{"type":"object"}}'
# 401

# --- 3. THE IMPORTANT ONE: the two internal ports are unreachable ------
nc -z -v -w 5 $HOST 8811  ; echo "8811 exit=$?"
nc -z -v -w 5 $HOST 11434 ; echo "11434 exit=$?"
# Both must FAIL. A non-zero exit is what you want.

curl --max-time 8 -sS http://$HOST:11434/api/tags ; echo "ollama exit=$?"
curl --max-time 8 -sS http://$HOST:8811/healthz   ; echo "gunicorn exit=$?"
# Both must fail. If either returns JSON, STOP: the port is public.

# --- 4. Optional, if nmap is installed --------------------------------
nmap -Pn -p 22,80,443,8811,11434 $HOST
# 80/tcp    open
# 443/tcp   open
# 22/tcp    open   (or filtered, if you narrowed the source CIDR)
# 8811/tcp  filtered
# 11434/tcp filtered
```

**How to read the failure.** `filtered` / a timeout means the packet was dropped
before reaching a listener — the firewall is doing its job. `closed` /
"Connection refused" means the packet *reached the host* and nothing was
listening; that still means no firewall is protecting the port, and if the
service ever binds `0.0.0.0` it becomes instantly public. Timeouts are the
answer you want. If you see "refused", re-check §2 and §3.

---

## 14. Point Sweep at it

On your laptop, in the repo:

```bash
export SWEEP_PROFILE_ENGINE=local
export SWEEP_INFERENCE_BACKEND=remote
export SWEEP_INFERENCE_URL=https://$DOMAIN
# Paste the token you read back in §8. $TOKEN from §12 was set on the SERVER
# and does not cross the ssh boundary.
export SWEEP_INFERENCE_TOKEN=paste-the-64-hex-characters-here

python -m sweep
```

Nothing else changes. `SWEEP_MODEL_KEEP_ALIVE` is **not** set on the laptop — it
is a server-side setting, and the application default of `30s` remains correct
for anyone running the model locally.

To A/B against a local Ollama at any time, set
`SWEEP_INFERENCE_BACKEND=local-direct`. The profile is byte-identical either way;
`python -m bench.backends` is the check.

---

## 15. Logs and rotation

| What | Where | Rotation |
| --- | --- | --- |
| Inference service | `journalctl -u sweep-inference` | journald |
| Ollama | `journalctl -u ollama` | journald |
| Caddy access | `/var/log/caddy/access.log` | Caddy itself: 10 MiB × 5 |
| Caddy errors | `journalctl -u caddy` | journald |

Cap journald so logs cannot fill a 50 GB boot volume:

```bash
sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=200M/' /etc/systemd/journald.conf
sudo systemctl restart systemd-journald
```

One line per request, and the field set is asserted by a test:

```
request_id=65440b5bcb86 path=/v1/generate status=200 model=qwen3:8b \
  duration_ms=11963 outcome=ok
```

Request id, path, status, model, duration, outcome category. **No prompt, no
résumé, no model output, no token, and nothing derived from the request body.**
An unhandled exception logs its type and stack frames but not its message —
`json`, `urllib` and runtime adapters all put the thing they choked on into the
message, and here that thing would be a résumé.

Useful queries:

```bash
sudo journalctl -u sweep-inference -f                         # follow
sudo journalctl -u sweep-inference --since "1 hour ago" | grep -v outcome=ok
sudo journalctl -u sweep-inference --since today | grep -c outcome=model_busy
```

---

## 16. Expected behaviour when things go wrong

### 16.1 The VM reboots, or Oracle restarts it

All three units are `enable`d, so they start at boot in dependency order. The
iptables rules survive because of `netfilter-persistent save` (§3) — if you
skipped that, the box comes back healthy and invisible.

First request after a boot pays the model load. Everything else is automatic;
there is no state to restore because there is no state.

### 16.2 Oracle reclaims the instance

Oracle may reclaim an Always Free instance when, over 7 days, CPU **and** network
**and** memory utilisation are all under 20%. At 10 profiles/day CPU sits near
2%, so **memory is the only criterion keeping the instance alive** — which is why
`SWEEP_MODEL_KEEP_ALIVE=-1` is in the environment file. Pinned, the model holds
~5.3 GB, or 44% of 12 GB.

Check it is actually pinned, any time:

```bash
curl -s localhost:11434/api/ps | python3 -m json.tool
# expires_at should read year 2318 — Ollama's way of writing "never"
```

If reclaimed, Sweep's calls fail with `RemoteServiceError` — **explicitly, with
no silent fallback to a local Ollama.** Recovery is re-provisioning from this
runbook; there is nothing to back up.

### 16.3 Ollama is down, or the model is missing

- `GET /healthz` → **503**, `{"status":"degraded","runtime_reachable":false}`
- `POST /v1/generate` → **503**, category `model_unavailable`
- The service itself **stays up and keeps answering** — it does not crash-loop,
  and it did not refuse to boot in the first place.
- On the client: `ModelUnavailable`, which Sweep's `local-first` engine treats as
  worth one Gemini call. On `engine=local` it is a hard error.

```bash
sudo systemctl status ollama
sudo journalctl -u ollama -n 50 --no-pager
ollama list                       # is qwen3:8b actually pulled?
sudo systemctl restart ollama
```

### 16.4 The queue is full

One generation runs at a time. A second request waits up to
`SWEEP_INFERENCE_QUEUE_WAIT` (30 s), then:

- **429**, category `model_busy`, with a `Retry-After` header (never 0).
- On the client: `ModelBusy`, which is **neither** `ModelUnavailable` **nor**
  `RemoteServiceError`. It does not escalate to Gemini — spending a paid call
  because a queue was busy, *under load*, is the worst moment to start paying.
  Sweep raises it; it does not retry automatically.
- `/healthz` and authentication failures still answer in milliseconds throughout,
  because they take no model slot.

This is a capacity signal, not a fault. Measured serialisation of four concurrent
requests: 5.4 s / 10.4 s / 15.4 s / 20.4 s, clean 5 s steps.

Raising `SWEEP_INFERENCE_WORKERS` above 1 on this hardware makes things *worse*,
not better: a second concurrent generation makes Ollama load a second copy of a
5 GB model or evict the first, and both callers then finish later than either
would have alone.

### 16.5 Memory pressure

The arithmetic on 12 GB:

| | |
| --- | --- |
| Ollama + model, `num_ctx=2048` (typical) | **5.3 GB** |
| Ollama + model, `num_ctx=8192` (`ctx_for`'s ceiling, a very long résumé) | ~7.2 GB |
| gunicorn (capped at 512 MB) | ~60 MB |
| Caddy | ~20 MB |
| Ubuntu | ~0.5–1 GB |
| **Worst case** | **~8.3 GB of 12 GB** |

Comfortable, but it makes this a single-purpose box. Do not co-locate anything
memory-hungry.

Two deliberate choices, both in §7.3:

- **`MemoryMax=9G` on Ollama.** If it exceeds that, the kernel kills *Ollama's*
  cgroup rather than picking a victim across the whole box. The service then
  answers `model_unavailable`, `/healthz` goes `degraded`, and systemd restarts
  Ollama in 5 s. A bounded, self-healing failure instead of a random one.
- **No swap, and `MemorySwapMax=0`.** Swapping a 5 GB model does not rescue the
  box, it makes it thrash for minutes. A clean kill and a 5 s restart is better
  than an unreachable instance. This is also why `MemoryMax` matters more than
  it would elsewhere.

Symptom to watch for: `outcome=model_unavailable` lines appearing in bursts, with
`journalctl -u ollama` showing the service restarting.

```bash
free -h
systemctl show ollama -p MemoryCurrent,MemoryMax
curl -s localhost:11434/api/ps | python3 -m json.tool   # size, and context_length
```

### 16.6 The full error taxonomy

Unchanged from [inference-service.md](inference-service.md), and covered by tests:

| Category | Status | Client exception | Escalates to Gemini? |
| --- | --- | --- | --- |
| `unauthorized` | 401 | `RemoteServiceError` | no |
| `bad_request` | 400 | `RemoteServiceError` | no |
| `payload_too_large` | 413 | `RemoteServiceError` | no |
| `model_busy` | **429** | `ModelBusy` | **no** |
| `model_unavailable` | 503 | `ModelUnavailable` | yes, on `local-first` |
| `model_timeout` | 504 | `ModelUnavailable` | yes, on `local-first` |
| `bad_model_output` | 502 | `ModelUnavailable` | yes, on `local-first` |
| `internal_error` | 500 | `RemoteServiceError` | no |
| `not_found` | 404/405 | `RemoteServiceError` | no |

Any response that is **not** this shape — a proxy's HTML error page, a gateway
503 with no category — becomes `RemoteServiceError`, never `ModelUnavailable`. A
broken deployment must not be able to hide behind a paid API.

---

## 17. Routine operations

```bash
# Update the service (three files, no migration, no state)
scp inference.py inference_service.py gunicorn.conf.py ubuntu@$IP:/tmp/     # laptop
sudo install -o sweep -g sweep -m 0644 /tmp/inference*.py /tmp/gunicorn.conf.py \
     /opt/sweep-inference/app/
sudo systemctl restart sweep-inference    # in-flight generation finishes first

# Rotate the token — comma-separated accepts both during the changeover
sudo sed -i 's/^SWEEP_INFERENCE_TOKEN=.*/&,NEWTOKEN/' /etc/sweep-inference/env
sudo systemctl restart sweep-inference
#   ... update the client, confirm, then remove the old one and restart again.

# Stop everything
sudo systemctl stop sweep-inference caddy
#   Leave ollama running: it is what holds memory above Oracle's
#   reclamation floor. Stopping it for days risks the instance.
```

**Restart behaviour:** `Restart=always`, `RestartSec=5` on both units. On SIGTERM
the in-flight generation finishes — gunicorn's `graceful_timeout` is 330 s and
`TimeoutStopSec` is 360 s, so systemd is always the last to lose patience.
Verified locally: SIGTERM sent 8 s into a live extraction, the request returned
`200` at 9.8 s, then the port closed.
