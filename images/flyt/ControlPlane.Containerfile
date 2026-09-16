FROM docker.io/library/python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/WoogiBoogi1129/flyt-k8s-poc" org.opencontainers.image.revision=$VCS_REF
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLYT_MODE=review
WORKDIR /opt/flyt/control
COPY runtime/shm/control/ /opt/flyt/control/
USER 65532:65532
EXPOSE 8080 8443
ENTRYPOINT ["python3", "/opt/flyt/control/control_plane.py"]
CMD []
