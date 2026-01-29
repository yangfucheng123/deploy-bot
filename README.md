# 部署机器人（Deploy Bot）
一款基于FastAPI+Python的全自动Web应用部署机器人，支持从GitHub拉取代码、自动安装依赖、启动服务，并通过微信推送部署结果，解决手动部署繁琐、易出错的问题。

## 🌟 功能亮点
1. 全自动部署：调用API即可完成「拉取代码→安装依赖→启动服务→验证端口」全流程；
2. 错误排查：集成DeepSeek AI，自动分析部署错误并给出修复命令；
3. 微信通知：部署结果实时推送（成功/失败），无需手动检查；
4. 版本兼容：适配Python 3.8/3.9，解决asyncio timeout参数兼容问题；
5. 环境隔离：指定Python版本安装依赖，避免环境冲突。

## 📋 环境要求
### 服务器端
- 系统：Ubuntu 18.04+/CentOS 7+
- Python：3.8/3.9（推荐3.9）
- 依赖：git、pip3、gunicorn
- 网络：能访问GitHub、DeepSeek API、Server酱接口

### 依赖安装
```bash
# 安装系统依赖
sudo apt update && sudo apt install -y git python3.9 python3.9-pip

# 安装Python依赖
python3.9 -m pip install fastapi uvicorn python-dotenv openai requests
