# syntax=docker/dockerfile:1
# Checker: só biblioteca padrão do Python, então a imagem é a slim e mais nada.
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY alerta_checker/ ./alerta_checker/

# Usuário numérico: o Kubernetes consegue conferir o runAsNonRoot
USER 10001:10001

# "python -m <módulo>": o padrão é o worker; o mesmo build serve o mercado falso
#   docker run ... alerta_checker.fake_market
ENTRYPOINT ["python", "-m"]
CMD ["alerta_checker.worker"]
