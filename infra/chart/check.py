#!/usr/bin/env python3
"""Confere o chart como o CI confere: renderiza com cada values e valida os manifestos.

  - recursos do Kubernetes: schemas oficiais (kubernetes-validate, modo estrito)
  - HTTPRoute/Gateway e CiliumNetworkPolicy: schema dos CRDs (Gateway API e Cilium)

    pip install kubernetes-validate jsonschema pyyaml
    python3 infra/chart/check.py
"""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

import jsonschema
import kubernetes_validate
import yaml

CHART = Path(__file__).resolve().parent / "alerta-cotacao"
K8S_VERSIONS = ["1.31.0", "1.36.0"]
CRDS = [
    "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.6.1/config/crd/standard/gateway.networking.k8s.io_httproutes.yaml",
    "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.6.1/config/crd/standard/gateway.networking.k8s.io_gateways.yaml",
    "https://raw.githubusercontent.com/cilium/cilium/v1.19.4/pkg/k8s/apis/cilium.io/client/crds/v2/ciliumnetworkpolicies.yaml",
]
VARIANTS = {
    "padrão": [],
    "local": ["-f", str(CHART / "values-local.yaml")],
    "prod": ["-f", str(CHART / "values-prod.yaml")],
    "banco externo": [
        "--set", "postgres.enabled=false",
        "--set", "externalDatabase.jdbcUrl=jdbc:postgresql://db.exemplo:5432/alerta",
        "--set", "networkPolicy.externalDatabaseCidr=10.20.0.5/32",
    ],
}


def crd_schemas() -> dict[tuple[str, str], dict]:
    schemas = {}
    for url in CRDS:
        with urllib.request.urlopen(url, timeout=30) as response:
            crd = yaml.safe_load(response.read())
        for version in crd["spec"]["versions"]:
            key = (f"{crd['spec']['group']}/{version['name']}", crd["spec"]["names"]["kind"])
            schemas[key] = openapi_to_jsonschema(version["schema"]["openAPIV3Schema"])
    return schemas


def openapi_to_jsonschema(schema):
    """x-kubernetes-int-or-string aceita número ou texto: tira o "type" que diria só um."""
    if isinstance(schema, dict):
        if schema.get("x-kubernetes-int-or-string"):
            schema = {k: v for k, v in schema.items() if k != "type"}
        return {k: openapi_to_jsonschema(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [openapi_to_jsonschema(v) for v in schema]
    return schema


def main() -> int:
    subprocess.run(["helm", "lint", str(CHART), "--strict"], check=True, capture_output=True)
    crds = crd_schemas()
    problems = 0
    for name, args in VARIANTS.items():
        rendered = subprocess.run(
            ["helm", "template", "alerta", str(CHART), "--namespace", "alerta", *args],
            check=True, capture_output=True, text=True,
        ).stdout
        docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
        for doc in docs:
            where = f"[{name}] {doc['kind']}/{doc['metadata']['name']}"
            key = (doc["apiVersion"], doc["kind"])
            if key in crds:
                errors = [f"{e.json_path}: {e.message[:200]}" for e in jsonschema.Draft7Validator(crds[key]).iter_errors(doc)]
            else:
                errors = []
                for version in K8S_VERSIONS:
                    try:
                        kubernetes_validate.validate(doc, version, strict=True)
                    except kubernetes_validate.ValidationError as exc:
                        errors.append(f"k8s {version}: {exc.message[:200]}")
            for error in errors:
                problems += 1
                print(f"ERRO {where}: {error}")
        print(f"{name}: {len(docs)} manifestos conferidos")
    print("tudo certo" if not problems else f"{problems} problema(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
