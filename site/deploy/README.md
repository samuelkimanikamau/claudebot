# Deploying claudebot.ve.ke

Static landing page (`site/index.html` + `site/install.sh`) served by nginx on the
VE.KE box (`157.173.124.117`, same server as tikiti.co.ke).

## 1. DNS

At the `ve.ke` DNS provider, add an **A record**:

```
claudebot.ve.ke   A   157.173.124.117
```

(Add an `AAAA` record too if the box has an IPv6 address.) Wait for it to resolve:
`dig +short claudebot.ve.ke` → `157.173.124.117`.

## 2. Upload the site

From this repo (the `site/` dir is the web root; the `deploy/` subfolder is excluded):

```bash
rsync -avz --delete --exclude 'deploy' \
  site/ root@157.173.124.117:/var/www/claudebot.ve.ke/
```

## 3. nginx vhost

```bash
scp site/deploy/claudebot.ve.ke.conf root@157.173.124.117:/etc/nginx/sites-available/
ssh root@157.173.124.117 '
  ln -sf /etc/nginx/sites-available/claudebot.ve.ke.conf /etc/nginx/sites-enabled/ &&
  nginx -t && systemctl reload nginx'
```

## 4. TLS (Let's Encrypt)

```bash
ssh root@157.173.124.117 'certbot --nginx -d claudebot.ve.ke --non-interactive --agree-tos -m samuel@ve.ke'
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
