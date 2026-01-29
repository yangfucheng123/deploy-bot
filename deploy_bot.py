import os
import asyncio
import subprocess
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv

# 加载配置文件
load_dotenv()
app = FastAPI()

# 配置项（从.env读取）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SERVERCHAN_SENDKEY = os.getenv("SERVERCHAN_SENDKEY")
SERVER_PUBLIC_IP = os.getenv("SERVER_PUBLIC_IP")

# 定义部署命令的请求体（规范入参）
class DeployRequest(BaseModel):
    app_name: str  # 应用名称（如test_web）
    repo_url: str  # Git仓库地址（如https://github.com/xxx/test_flask_app.git）
    port: int      # 要暴露的端口（如8080）

# ========== 核心功能函数 ==========
async def execute_linux_cmd(cmd: str) -> str:
    """
    执行Linux命令，返回输出结果（包含错误信息）
    适配Python 3.8/3.9：移除create_subprocess_shell的timeout参数，改用wait_for+communicate
    :param cmd: 要执行的Linux命令
    :return: 命令输出/错误信息
    """
    try:
        # 异步执行命令（移除timeout参数，Python3.8不支持该位置传timeout）
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        # 将timeout移到communicate，通过wait_for实现30秒超时（Python3.8+支持）
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=30
        )
        # 解码输出（处理中文乱码）
        stdout_str = stdout.decode("utf-8", errors="ignore")
        stderr_str = stderr.decode("utf-8", errors="ignore")
        
        if stderr_str:
            return f"命令执行失败：{cmd}\n错误信息：{stderr_str}"
        return f"命令执行成功：{cmd}\n输出：{stdout_str}"
    except asyncio.TimeoutError:
        return f"命令执行超时：{cmd}"
    except Exception as e:
        return f"命令执行异常：{cmd}\n异常信息：{str(e)}"

async def ai_troubleshoot(error_info: str) -> str:
    """
    调用DeepSeek API排查部署错误，返回具体修复命令/步骤
    :param error_info: 错误信息
    :return: AI给出的修复建议
    """
    try:
        # 初始化DeepSeek客户端（适配OpenAI兼容接口，base_url必须带/v1）
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1"  # 关键修复：添加/v1路径
        )
        # 构造精准的提示词（确保AI返回可执行的Linux命令）
        messages = [
            {
                "role": "system",
                "content": """你是资深Linux运维工程师，精通Web应用部署（Python/Flask/Django），
                现在需要排查部署错误，要求：
                1. 先说明错误原因；
                2. 给出具体的Linux修复命令（可直接复制执行）；
                3. 步骤清晰，新手能看懂。"""
            },
            {"role": "user", "content": f"部署Web应用时遇到错误：{error_info}"}
        ]
        # 调用DeepSeek API
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.1,  # 降低随机性，保证建议精准
            max_tokens=1500
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI排查失败：{str(e)}\n请手动排查错误。"

def send_wechat_notification(title: str, content: str):
    """
    发送微信通知（通过Server酱）
    :param title: 通知标题
    :param content: 通知内容（支持Markdown）
    """
    try:
        url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
        data = {
            "title": title,
            "desp": content
        }
        response = requests.post(url, data=data, timeout=10)
        if response.json()["code"] == 0:
            print("微信通知发送成功")
        else:
            print(f"微信通知发送失败：{response.text}")
    except Exception as e:
        print(f"发送通知异常：{str(e)}")

async def deploy_web_app(task_id: str, req: DeployRequest):
    """
    核心部署逻辑：拉代码→装依赖→启动服务→验证
    :param task_id: 任务ID（追踪用）
    :param req: 部署请求参数
    """
    # 部署目录（统一放在/opt下）
    deploy_dir = f"/opt/{req.app_name}"
    # 初始化通知内容
    notify_content = f"### 部署任务[{task_id}]\n应用名称：{req.app_name}\n仓库地址：{req.repo_url}\n端口：{req.port}\n\n"

    try:
        # ========== 步骤1：拉取Git代码 ==========
        notify_content += "#### 步骤1：拉取代码\n"
        # 先删除旧目录（避免冲突）
        await execute_linux_cmd(f"rm -rf {deploy_dir}")
        pull_cmd = f"git clone {req.repo_url} {deploy_dir}"
        pull_result = await execute_linux_cmd(pull_cmd)
        notify_content += f"{pull_result}\n\n"
        
        # 【核心修复】只识别git真正的错误关键词，避免误判正常的clone输出
        git_error_keywords = ["fatal:", "error:", "Repository not found", "Could not resolve host", "Permission denied"]
        if any(keyword in pull_result for keyword in git_error_keywords):
            # AI排查错误
            ai_suggest = await ai_troubleshoot(pull_result)
            notify_content += f"#### AI修复建议\n{ai_suggest}\n"
            send_wechat_notification(f"部署失败[{task_id}]", notify_content)
            return

        # ========== 步骤2：安装依赖 ==========
        notify_content += "#### 步骤2：安装依赖\n"
        # 假设是Python项目（requirements.txt），指定python3.9 pip避免环境问题
        install_cmd = f"cd {deploy_dir} && python3.9 -m pip install -r requirements.txt --user"
        install_result = await execute_linux_cmd(install_cmd)
        notify_content += f"{install_result}\n\n"

        # ========== 步骤3：启动Web服务 ==========
        notify_content += "#### 步骤3：启动服务\n"
        # 用gunicorn启动（后台运行，绑定所有IP）
        # 假设应用入口文件是app.py，Flask实例是app
        start_cmd = f"""
        cd {deploy_dir} && 
        nohup python3.9 -m gunicorn -w 4 -b 0.0.0.0:{req.port} app:app > {deploy_dir}/app.log 2>&1 &
        """
        start_result = await execute_linux_cmd(start_cmd)
        notify_content += f"{start_result}\n\n"

        # 等待2秒（让服务启动）
        await asyncio.sleep(2)

        # ========== 步骤4：验证服务是否启动 ==========
        notify_content += "#### 步骤4：验证服务\n"
        # 检查端口是否被监听
        check_cmd = f"netstat -tulpn | grep :{req.port} | grep python3.9"
        check_result = await execute_linux_cmd(check_cmd)
        if check_result:
            notify_content += f"端口{req.port}监听成功！\n"
            notify_content += f"#### 部署成功\n可访问：http://{SERVER_PUBLIC_IP}:{req.port}\n"
            send_wechat_notification(f"部署成功[{task_id}]", notify_content)
        else:
            notify_content += f"端口{req.port}未监听，服务启动失败！\n"
            # AI排查启动失败问题
            ai_suggest = await ai_troubleshoot(f"启动命令：{start_cmd}\n端口{req.port}未监听")
            notify_content += f"#### AI修复建议\n{ai_suggest}\n"
            send_wechat_notification(f"部署失败[{task_id}]", notify_content)

    except Exception as e:
        notify_content += f"#### 部署异常\n{str(e)}\n"
        send_wechat_notification(f"部署异常[{task_id}]", notify_content)

# ========== API接口 ==========
@app.post("/deploy_web")
async def deploy_web(req: DeployRequest):
    """
    部署Web应用的API接口
    示例调用：curl -X POST http://服务器IP:8000/deploy_web -H "Content-Type: application/json" -d '{"app_name":"test_web","repo_url":"https://github.com/xxx/test_flask_app.git","port":8080}'
    """
    # 生成唯一任务ID
    task_id = f"{req.app_name}_{req.port}_{asyncio.get_event_loop().time():.0f}"
    # 异步执行部署（避免API阻塞）
    asyncio.create_task(deploy_web_app(task_id, req))
    return {
        "code": 200,
        "msg": "部署任务已启动",
        "task_id": task_id,
        "tips": f"可等待微信通知，或稍后检查http://{SERVER_PUBLIC_IP}:{req.port}"
    }

@app.get("/health")
async def health_check():
    """健康检查接口（验证API服务是否运行）"""
    return {"code": 200, "msg": "部署机器人运行正常"}

# ========== 启动服务 ==========
if __name__ == "__main__":
    import uvicorn
    # 启动FastAPI服务，监听所有IP的8000端口
    uvicorn.run(
        app="deploy_bot:app",
        host="0.0.0.0",
        port=8000,
        reload=False  # 生产环境关闭reload
    )
