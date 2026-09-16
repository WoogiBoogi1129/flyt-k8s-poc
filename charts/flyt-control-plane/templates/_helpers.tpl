{{- define "flyt.name" -}}
{{- printf "%s-flyt" .Release.Name | trunc 48 | trimSuffix "-" -}}
{{- end -}}
{{- define "flyt.clusterName" -}}
{{- printf "%s-%s" .Release.Namespace (include "flyt.name" .) | trunc 253 | trimSuffix "-" -}}
{{- end -}}
{{- define "flyt.image" -}}
{{- printf "%s@%s" .Values.image.repository (required "image.digest is required" .Values.image.digest) -}}
{{- end -}}
{{- define "flyt.tlsSecret" -}}
{{- if .Values.tls.certManager.enabled -}}
{{ include "flyt.name" . }}-tls
{{- else -}}
{{ required "tls.existingSecret is required" .Values.tls.existingSecret }}
{{- end -}}
{{- end -}}
