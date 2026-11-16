# Marginalia 跨设备部署

当前推荐的跨设备方式是局域网：Electron 桌面端把 API 和前端页面一起提供，手机连接同一 Wi‑Fi 后访问电脑端显示的地址即可。桌面端是数据源，手机不保存独立副本。

## 服务端

仓库提供了 `backend/Dockerfile`。部署时：

1. 用 `backend/` 构建容器。
2. 把持久化磁盘挂载到 `/data`，否则重启后数据库、PDF 和解析结果会丢失。
3. 设置 `PAPERDESK_ALLOW_REMOTE=1`、`PORT=8080`。公网必须启用 HTTPS。
4. 让服务可以访问 `/api/health`，并确认返回 `status: ok`。

现有本地资料迁移到云端时，应在远程服务第一次启动前，把本机数据目录中的 `database/`、`papers/`、`cache/`、`settings/` 等内容复制到远程 `/data/`，然后第一次注册/登录 `lance`。旧根目录库会绑定给 `lance`；其它账号会自动使用独立目录。

## 浏览器/手机端

构建 Web 端时，在 `src/renderer/.env.local` 设置：

```text
VITE_PAPERDESK_BACKEND_URL=https://你的后端域名
```

Electron 端自动启动局域网后端；普通浏览器/PWA 端读取当前页面来源，不需要第三方项目配置。

## 安全边界

- 每个账号有独立 SQLite、PDF、缓存、任务和设置目录。
- 局域网模式不显示登录页面，安全边界是用户自己的局域网；不要将端口映射到公网。
- 远程部署必须使用 HTTPS 和持久化磁盘；不要把数据目录挂到临时文件系统。
- 不要把 `service_role` key、服务器磁盘路径或数据库文件放到前端。
