FROM node:20-alpine
WORKDIR /app
ARG REACT_APP_API_URL=http://localhost:8000
ENV REACT_APP_API_URL=$REACT_APP_API_URL
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend /app
RUN npm run build
RUN npm install -g serve
EXPOSE 3000
CMD ["serve", "-s", "build", "-l", "tcp://0.0.0.0:3000"]
