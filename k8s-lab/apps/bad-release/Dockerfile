FROM node:22-alpine
# APP_VERSION is baked into the image; BREAK=1 builds a deliberately broken release.
ARG APP_VERSION=v1
ARG BREAK=0
ENV APP_VERSION=${APP_VERSION}
WORKDIR /app
COPY package.json ./
COPY server.js routes.js ./
# Simulate a bad release: the file the server needs is missing from the image.
RUN if [ "$BREAK" = "1" ]; then rm routes.js; fi
USER node
EXPOSE 8080
CMD ["node", "server.js"]
