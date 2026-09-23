# Stage 1: moi truong phat trien, cung la stage ma docker-compose.yml dung.
# Vite dev server, hot reload qua bind mount, khong dich ra tep tinh.
FROM node:24-slim AS dev

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]

# Stage 2: dich ra tep tinh. Tach rieng khoi stage dev de khi chay dev khong
# phai cho buoc dich, va de stage prod ben duoi co that /app/dist.
# Truoc day dong nay bi comment lai, nen prod chep mot thu muc khong ton tai.
FROM dev AS build

RUN npm run build

# Stage 3: phuc vu tep tinh bang nginx. Day la stage cuoi nen cung la muc tieu
# mac dinh khi build khong chi dinh target.
FROM nginx:alpine AS prod

RUN rm -rf /usr/share/nginx/html/*

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
