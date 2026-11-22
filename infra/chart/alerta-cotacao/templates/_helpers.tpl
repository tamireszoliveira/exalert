{{/* Nome base dos recursos: "<release>" (ou fullnameOverride), cortado para caber em 63 com sufixos. */}}
{{- define "alerta.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "alerta.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "alerta.labels" -}}
{{ include "alerta.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: alerta-cotacao
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Labels de um componente. Uso: include "alerta.componentLabels" (list . "api") */}}
{{- define "alerta.componentLabels" -}}
{{ include "alerta.labels" (index . 0) }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "alerta.componentSelector" -}}
{{ include "alerta.selectorLabels" (index . 0) }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{/* Imagem de um componente: <registro>/<repositório>:<tag do componente | global | appVersion> */}}
{{- define "alerta.image" -}}
{{- $root := index . 0 -}}
{{- $image := index . 1 -}}
{{- $tag := $image.tag | default $root.Values.global.imageTag | default $root.Chart.AppVersion -}}
{{- if $root.Values.global.imageRegistry -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $root.Values.global.imageRegistry) $image.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $image.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "alerta.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" (include "alerta.fullname" .)) -}}
{{- end -}}

{{- define "alerta.jdbcUrl" -}}
{{- if .Values.postgres.enabled -}}
{{- printf "jdbc:postgresql://%s-postgres:5432/%s" (include "alerta.fullname" .) .Values.postgres.database -}}
{{- else -}}
{{- required "postgres.enabled=false exige externalDatabase.jdbcUrl" .Values.externalDatabase.jdbcUrl -}}
{{- end -}}
{{- end -}}

{{/* Segurança padrão dos contêineres: sem root, sem escalar privilégio, sistema de arquivos só leitura. */}}
{{- define "alerta.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}

{{- define "alerta.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}


{{/*
Valor de uma chave do Secret, já em base64. Ordem: valor informado nos values;
senão o que já está no cluster (lookup); senão um aleatório (se gerar=true); senão vazio.
Uso: include "alerta.secretValue" (list $existente "CHAVE" .Values.secrets.x gerar)
*/}}
{{- define "alerta.secretValue" -}}
{{- $existing := index . 0 -}}
{{- $key := index . 1 -}}
{{- $value := index . 2 -}}
{{- $generate := index . 3 -}}
{{- if $value -}}
{{- $value | b64enc -}}
{{- else if hasKey $existing $key -}}
{{- index $existing $key -}}
{{- else if $generate -}}
{{- randAlphaNum 40 | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Força a troca dos pods quando o Secret criado pelo chart muda. */}}
{{- define "alerta.secretChecksum" -}}
{{- if not .Values.secrets.existingSecret -}}
checksum/secret: {{ include (print .Template.BasePath "/secret.yaml") . | sha256sum }}
{{- end -}}
{{- end -}}
