# Многоэтапная сборка: собираем статику на Node, раздаём через nginx.
FROM node:22-alpine AS build
WORKDIR /app

# npmjs.org из РФ может быть недоступен — используем зеркало + ретраи.
RUN npm config set registry https://registry.npmmirror.com \
 && npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000

COPY package*.json ./
# Удаляем lockfile: в нём resolved-ссылки на npmjs.org, которые недоступны.
RUN rm -f package-lock.json && npm install

COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
