# syntax=docker/dockerfile:1
# PWA estático servido por nginx sem root (porta 8080, usuário 101).
# Imagem nova do nginx-unprivileged: nginx/nginx-unprivileged (a antiga, nginxinc/, parou na 1.30.5).
FROM nginx/nginx-unprivileged:1.31-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY public/ /usr/share/nginx/html/
EXPOSE 8080
