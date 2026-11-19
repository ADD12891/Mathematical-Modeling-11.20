# 项目同步与文件上传

远端仓库：https://github.com/ADD12891/Mathematical-Modeling-11.20

本地项目目录：`E:\Mathematical-Modeling-11.20`

默认分支：`main`。远端名称：`origin`。

## 拉取远端更新

在 PowerShell 中运行：

```powershell
Set-Location 'E:\Mathematical-Modeling-11.20'
git pull --ff-only origin main
```

## 添加文件并上传

将需要上传的文件放入项目目录，然后将下列 `文件路径` 替换为实际文件的相对路径：

```powershell
git status
git add -- '文件路径'
git diff --cached
git commit -m 'Add project files'
git pull --rebase origin main
git push origin main
```

提交前检查暂存内容，避免包含密码、令牌和不应公开的个人信息。如果拉取时产生冲突，先解决冲突并完成变基，再推送。

## 检查连接与同步状态

```powershell
git remote -v
git status -sb
git log -1 --oneline
```

上传需要使用对该仓库具有写入权限的 GitHub 账号。
