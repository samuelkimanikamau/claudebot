# Deploying claudebot.ve.ke

Static landing page (`site/index.html` + `site/install.sh`) served by nginx on the
host box. Replace `<SERVER_IP>` below with your origin IP. Note: if you front the
domain with Cloudflare (proxied/orange-cloud), do **not** publish the origin IP —
that defeats the proxy's origin hiding.

## 1. DNS

At the `ve.ke` DNS provider, add an **A record**:

```
claudebot.ve.ke   A   <SERVER_IP>
```

(Add an `AAAA` record too if the box has an IPv6 address.) Wait for it to resolve:
`dig +short claudebot.ve.ke`.

## 2. Upload the site

From this repo (the `site/` dir is the web root; the `deploy/` subfolder is excluded):

```bash
rsync -avz --delete --exclude 'deploy' \
  site/ vutia-prod:/var/www/claudebot.ve.ke/
```

## 3. nginx vhost

```bash
scp site/deploy/claudebot.ve.ke.conf vutia-prod:/etc/nginx/sites-available/
ssh vutia-prod '
  ln -sf /etc/nginx/sites-available/claudebot.ve.ke.conf /etc/nginx/sites-enabled/ &&
  nginx -t && systemctl reload nginx'
```

## 4. TLS (Let's Encrypt)

```bash
ssh vutia-prod 'certbot --nginx -d claudebot.ve.ke --non-interactive --agree-tos -m samuel@ve.ke'
```

certbot rewrites the vhost to add the `443` block and an `80 → 443` redirect, and
sets up auto-renewal.

## 5. Verify

```bash
curl -sI https://claudebot.ve.ke            # 200
curl -fsSL https://claudebot.ve.ke/install.sh | head -3   # the installer
```

## Re-deploying after a content change

Just re-run step 2 (rsync). No nginx reload needed for static content.
