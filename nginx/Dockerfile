# Imagem do Nginx com config + frontend embutidos (contexto de build = raiz do repo).
FROM nginx:1.30-alpine
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/ /usr/share/nginx/html/
# Falha o build se houver erro de sintaxe na configuração
RUN nginx -t
