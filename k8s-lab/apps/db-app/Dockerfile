FROM node:22-alpine
WORKDIR /app
# Dependencies first: this layer is reused until package*.json changes.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.js ./
USER node
EXPOSE 8080
CMD ["node", "server.js"]
