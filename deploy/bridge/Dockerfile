# The Proton Mail Bridge sidecar: the community image, plus the one library newer Bridge releases load.
# The Bridge updates itself into its state folder (/root); from 3.2x on it needs libfido2, which the base image
# lacks, and without it the updated Bridge fails to start ("libfido2.so.1: cannot open shared object file").
FROM shenxn/protonmail-bridge:3.19.0-1@sha256:3717b4441130675dc9131196de9f9c5287d2ea21b138d83b0486429e1737638a
RUN apt-get update \
 && apt-get install -y --no-install-recommends libfido2-1 \
 && rm -rf /var/lib/apt/lists/*
