# "Meera's laptop": the customer's machine in the WOYM demo.
#
# It is a container on the same host as the vendor's agents, NOT a separate
# computer, and it is not sandboxed from the vendor. The separation in the
# demo is Switch ownership: only @acme-env (owned by Meera) acts in here, and
# only through the allow-listed probes in woym/sandbox/customer.py.
FROM node:20-bookworm-slim

ENV TZ=Asia/Kolkata \
    LANG=en_IN.UTF-8

# The node image's uid 1000 user is "node"; rename it to meera.
RUN usermod -l meera -d /home/meera -m node && groupmod -n meera node

USER meera
WORKDIR /home/meera/acme-payouts
COPY --chown=meera:meera acme-payouts/ ./

# bizclock itself is installed after start (make customer-up), by streaming
# `git archive v2.3.1` from the vendor's remote into node_modules/bizclock,
# the same way a patched build is installed later.
CMD ["sleep", "infinity"]
