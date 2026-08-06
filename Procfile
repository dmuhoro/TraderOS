# TraderOS supervisor manifest (A4): the API and the trading daemon run as
# separately-supervised processes so a crash in one does not kill the other and
# the orchestrator is restarted by the platform supervisor (Railway/Fly/etc).
# The daemon only engages its real loop when the pilot gate has passed; on a
# fresh host it starts the supervised sidecar supervised under cmd_daemon.
web: traderos-api
worker: traderos daemon
