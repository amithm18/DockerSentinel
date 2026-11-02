# Pinned base image (Node 22 LTS); never :latest.
FROM node:22-alpine
WORKDIR /app
# Copy package files first so this layer is cached when only source changes.
# No dependencies, so there is no npm ci step.
COPY package.json ./
COPY server.js ./
# Run as the unprivileged "node" user that ships with the image.
USER node
EXPOSE 8080
CMD ["node", "server.js"]
