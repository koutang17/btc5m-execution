# Windows 服务化安装说明

## 已准备

- 便携版 NSSM: <PROJECT_ROOT>\tools\nssm.exe (2.24 64位)
- 安装脚本: install_services_admin.ps1
- 卸载脚本: uninstall_services_admin.ps1
- 控制脚本: service_control.ps1

## 服务

| 服务 | 程序 |
|---|---|
| BTC5mDashboard | dashboard_server.py |
| BTC5mBotA | predict_live_bot.py, A |
| BTC5mBotB | predict_live_bot.py, B |
| BTC5mTunnel | cloudflared 命名隧道 |

## 安装步骤

1. 右键开始菜单，打开 Windows PowerShell（管理员）
2. 运行:

    cd <PROJECT_ROOT>
    powershell -ExecutionPolicy Bypass -File .\install_services_admin.ps1

3. 按提示输入服务账户:

    <DOMAIN>\<user>

4. 输入该 Windows 账户密码。密码只用于 NSSM 配置服务登录，不写入脚本。

## 安装时会做什么

- 停止旧的看板、A/B机器人、cloudflared 进程
- 停用每分钟的 BTC5m Watchdog 计划任务
- 删除旧的 Dashboard/Tunnel 登录自启项
- 安装4个 NSSM 服务
- 设置服务账户
- 启动服务

## STOP/PAUSE 规则

- 暂停: 写 PAUSE，进程不退出
- 停止: 写 STOP，机器人正常退出，服务不自动重启 (NSSM AppExit 0 = Exit)
- 启动: 删除 STOP，执行 sc start BTC5mBotA/B
- 继续: 删除 PAUSE

## 验证

    cd <PROJECT_ROOT>
    .\service_control.ps1 status
    Get-Service BTC5mDashboard,BTC5mBotA,BTC5mBotB,BTC5mTunnel

## 如果服务启动失败

常见原因:

1. 账户没有“作为服务登录”权限
   - 运行 secpol.msc
   - 本地策略 -> 用户权限分配 -> 作为服务登录
   - 添加 <DOMAIN>\<user>

2. BAW 登录失效
   - 查看 service_logs\BTC5mBotA.err.log
   - 用该账户重新登录一次

3. 端口8600被旧进程占用
   - 安装脚本已经会清理旧进程；若还有，手动结束

## 卸载

管理员 PowerShell:

    cd <PROJECT_ROOT>
    powershell -ExecutionPolicy Bypass -File .\uninstall_services_admin.ps1

卸载后可按旧方式启动:

    start_dashboard_task.bat
    start_bot_A.bat
    start_bot_B.bat

## 已安装服务的参数修复

如果服务启动后日志里出现 Python 交互提示符：

    >>>

说明 NSSM 的服务参数为空，Python 没有执行脚本。

管理员 PowerShell 运行:

    cd D:deepseektc_5m_analysis
    powershell -ExecutionPolicy Bypass -File .epair_services_admin.ps1

修复脚本会:

- 停止4个服务
- 结束旧的看板/机器人/隧道进程
- 重新写入 AppParameters
- 重新启动服务
