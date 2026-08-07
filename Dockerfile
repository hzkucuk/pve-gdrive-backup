# pve-gdrive-backup — konteyner imaji
# Arka uc Python 3 stdlib + rclone; arayuz derlenmis olarak pve_gdrive.py icine gomulu.
# Node.js YALNIZCA gelistirme makinesinde gerekir, bu imajda yoktur.
FROM debian:12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 rclone ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*

COPY pve_gdrive.py /usr/local/bin/pve_gdrive.py
RUN chmod 755 /usr/local/bin/pve_gdrive.py

# Konteynerde systemd yok: zamanlayici surecin kendi icinde calisir.
# PYTHONUNBUFFERED: tampon yuzunden "docker logs" bos gorunmesin
ENV PVE_GDRIVE_CONTAINER=1 \
    PVE_GDRIVE_CONF=/config/pve-gdrive.conf \
    PYTHONUNBUFFERED=1

VOLUME ["/config", "/var/lib/pve-gdrive", "/root/.config/rclone"]
EXPOSE 8787

# Saglik kontrolu: HEAD istegi (TLS acikken de calissin diye -k)
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request,ssl,os,sys; \
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; \
[urllib.request.urlopen(u,timeout=8,context=ctx) for u in ['https://127.0.0.1:8787/'] ] " \
  || python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/',timeout=8)"

ENTRYPOINT ["python3", "/usr/local/bin/pve_gdrive.py"]
CMD ["serve"]
