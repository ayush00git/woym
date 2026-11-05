# A customer's "laptop" in the WOYM demo: Meera's (acme-laptop, the default) or
# Kiran's (kiran-laptop, epilogue), built from the same file with build args.
#
# It is a container on the same host as the vendor's agents, NOT a separate
# computer, and it is not sandboxed from the vendor. The separation in the
# demo is Switch ownership: only the customer's own agent (@acme-env for
# Meera, @nepal-env for Kiran) acts in here, and only through the allow-listed
# probes in woym/sandbox/customer.py.
FROM node:20-bookworm-slim

ARG CUSTOMER=meera
ARG TZ_NAME=Asia/Kolkata
ARG LANG_NAME=en_IN.UTF-8
# When the customer's repro order is placed (Friday, local time). The cutoff is 17:30.
ARG ORDER_AT=17:10

ENV TZ=${TZ_NAME} \
    LANG=${LANG_NAME} \
    ACME_ORDER_AT=${ORDER_AT}

# The node image's uid 1000 user is "node"; rename it to the customer.
RUN usermod -l "${CUSTOMER}" -d "/home/${CUSTOMER}" -m node && groupmod -n "${CUSTOMER}" node

USER ${CUSTOMER}
WORKDIR /home/${CUSTOMER}/acme-payouts
COPY --chown=1000:1000 acme-payouts/ ./

# bizclock itself is installed after start (customer up), by streaming
# `git archive v2.3.1` from the vendor's remote into node_modules/bizclock,
# the same way a patched build is installed later.
CMD ["sleep", "infinity"]
