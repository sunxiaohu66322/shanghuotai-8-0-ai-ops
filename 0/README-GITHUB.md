# shanghuotai-8-0-ai-ops

> 由 DSH skill `github-project-push` 上传。本仓库是**本地项目的结构化快照**，不是可直接运行的部署包。

- Owner: `sunxiaohu66322` / Visibility: `public`
- 上传时刻: 2026-11-14 18:16:04 +08:00
- 纳入文件: 1291 / 120.15 MB
- 排除文件: 1305（编译产物 / 运行数据库 / 备份 / 探针 / 凭据 / 大素材）

## 包里没有什么（别把「未收」当成「不存在」）

`dist/` `build/` `*.db` `*.sqlite` `shops.json` `token.txt` `.env`
`node_modules` `__pycache__` `.codegraph` 以及所有 `_` 开头的快照/探针/验收脚本目录，
均按硬排除规则剔除。运行态数据库里的业务数据一条都没有上传；读到 `0` 或「未取得」
请按原文口径理解，不要推断为「系统里没有」。

## 安全

推送前对全部纳入的文本文件做了密钥正则终检（`_GITHUB_MANIFEST.json` 里有
`secret_hits` 字段，必须为 `0`）。凭据只从本机 `gh` keyring 现取，仓库内不含任何令牌。
