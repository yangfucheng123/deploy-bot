import os
import asyncio
import subprocess
import requests
import json  # 新增：用于手动格式化JSON
from bs4 import BeautifulSoup
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.responses import JSONResponse  # 保留，但移除indent参数

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
    repo_url: str  # Git仓库地址
    port: int      # 要暴露的端口

# 搜索请求体（简化，无需API密钥）
class SearchRequest(BaseModel):
    query: str          # 搜索关键词（如"Python FastAPI 教程"）
    num_results: int = 3    # 返回结果数量，默认3条（最多10条，避免反爬）

# ========== 原有核心功能函数（完全不变） ==========
async def execute_linux_cmd(cmd: str) -> str:
    """执行Linux命令，返回输出结果"""
    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=30
        )
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
    """调用DeepSeek API排查部署错误"""
    try:
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1"
        )
        messages = [
            {
                "role": "system",
                "content": """你是资深Linux运维工程师，精通Web应用部署，
                1. 先说明错误原因；
                2. 给出具体的Linux修复命令；
                3. 步骤清晰，新手能看懂。"""
            },
            {"role": "user", "content": f"部署Web应用时遇到错误：{error_info}"}
        ]
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.1,
            max_tokens=1500
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI排查失败：{str(e)}\n请手动排查错误。"

def send_wechat_notification(title: str, content: str):
    """发送微信通知（Server酱）"""
    try:
        url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
        data = {"title": title, "desp": content}
        response = requests.post(url, data=data, timeout=10)
        if response.json()["code"] == 0:
            print("微信通知发送成功")
        else:
            print(f"微信通知发送失败：{response.text}")
    except Exception as e:
        print(f"发送通知异常：{str(e)}")

async def deploy_web_app(task_id: str, req: DeployRequest):
    """核心部署逻辑（不变）"""
    deploy_dir = f"/opt/{req.app_name}"
    notify_content = f"### 部署任务[{task_id}]\n应用名称：{req.app_name}\n仓库地址：{req.repo_url}\n端口：{req.port}\n\n"

    try:
        # 步骤1：拉取Git代码
        notify_content += "#### 步骤1：拉取代码\n"
        await execute_linux_cmd(f"rm -rf {deploy_dir}")
        pull_cmd = f"git clone {req.repo_url} {deploy_dir}"
        pull_result = await execute_linux_cmd(pull_cmd)
        notify_content += f"{pull_result}\n\n"
        
        # 修复：只识别git真正的错误关键词
        git_error_keywords = ["fatal:", "error:", "Repository not found", "Could not resolve host", "Permission denied"]
        if any(keyword in pull_result for keyword in git_error_keywords):
            ai_suggest = await ai_troubleshoot(pull_result)
            notify_content += f"#### AI修复建议\n{ai_suggest}\n"
            send_wechat_notification(f"部署失败[{task_id}]", notify_content)
            return

        # 步骤2：安装依赖
        notify_content += "#### 步骤2：安装依赖\n"
        install_cmd = f"cd {deploy_dir} && python3.9 -m pip install -r requirements.txt --user"
        install_result = await execute_linux_cmd(install_cmd)
        notify_content += f"{install_result}\n\n"

        # 步骤3：启动Web服务
        notify_content += "#### 步骤3：启动服务\n"
        start_cmd = f"""
        cd {deploy_dir} && 
        nohup python3.9 -m gunicorn -w 4 -b 0.0.0.0:{req.port} app:app > {deploy_dir}/app.log 2>&1 &
        """
        start_result = await execute_linux_cmd(start_cmd)
        notify_content += f"{start_result}\n\n"

        await asyncio.sleep(2)

        # 步骤4：验证服务是否启动
        notify_content += "#### 步骤4：验证服务\n"
        check_cmd = f"netstat -tulpn | grep :{req.port} | grep python3.9"
        check_result = await execute_linux_cmd(check_cmd)
        if check_result:
            notify_content += f"端口{req.port}监听成功！\n"
            notify_content += f"#### 部署成功\n可访问：http://{SERVER_PUBLIC_IP}:{req.port}\n"
            send_wechat_notification(f"部署成功[{task_id}]", notify_content)
        else:
            notify_content += f"端口{req.port}未监听，服务启动失败！\n"
            ai_suggest = await ai_troubleshoot(f"启动命令：{start_cmd}\n端口{req.port}未监听")
            notify_content += f"#### AI修复建议\n{ai_suggest}\n"
            send_wechat_notification(f"部署失败[{task_id}]", notify_content)

    except Exception as e:
        notify_content += f"#### 部署异常\n{str(e)}\n"
        send_wechat_notification(f"部署异常[{task_id}]", notify_content)

# ========== 优化后的搜索核心函数（适配低版本） ==========
def get_search_headers():
    """返回模拟浏览器的请求头，避免被识别为爬虫"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.so.com/",
        "Connection": "keep-alive"
    }

def get_real_url(redirect_url):
    """解析360跳转链接（优化：超时兜底+快速失败）"""
    try:
        # 缩短超时时间+关闭重定向跟随（避免阻塞）
        response = requests.head(
            redirect_url,
            headers=get_search_headers(),
            allow_redirects=False,  # 关闭自动跳转（减少耗时）
            timeout=2,              # 超时缩短为2秒
            verify=False
        )
        # 手动提取Location头（跳转链接），不依赖自动重定向
        return response.headers.get("Location", redirect_url)
    except Exception as e:
        # 解析失败直接返回原链接，不抛异常
        return redirect_url

async def search_info(req: SearchRequest) -> dict:
    """
    免费爬取360搜索结果（适配低版本FastAPI）
    """
    try:
        # 限制最大返回结果数（避免反爬，最多10条）
        max_results = min(req.num_results, 10)
        # 构造360搜索URL（UTF-8编码关键词，避免中文乱码）
        search_url = f"https://www.so.com/s?q={requests.utils.quote(req.query)}&pn=0&rn={max_results}"
        
        # 使用Python3.9+的asyncio.to_thread（更稳定，替代loop.run_in_executor）
        response = await asyncio.to_thread(
            requests.get,
            search_url,
            headers=get_search_headers(),
            timeout=5,
            verify=False
        )
        
        # 解析HTML（兼容新旧DOM结构）
        soup = BeautifulSoup(response.text, "lxml")
        results = []
        
        # 兼容360搜索新旧两种DOM结构（避免解析失败）
        result_items = soup.find_all("li", class_="res-list") or soup.find_all("div", class_="result-card")
        for item in result_items[:max_results]:
            # 提取标题（兼容两种选择器）
            title_tag = item.find("h3", class_="res-title") or item.find("a", class_="js-search-title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True) if title_tag else ""
            
            # 提取链接（兼容两种选择器）
            link_tag = title_tag.find("a") if title_tag.name == "h3" else title_tag
            redirect_link = link_tag.get("href", "") if link_tag else ""
            real_link = get_real_url(redirect_link) if redirect_link else ""
            
            # 提取摘要（兼容两种选择器+兜底）
            snippet_tag = item.find("p", class_="res-desc") or item.find("p", class_="summary")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else "无摘要"
            
            # 提取来源（兼容两种选择器+兜底）
            source_tag = item.find("span", class_="res-site") or item.find("span", class_="url")
            source = source_tag.get_text(strip=True) if source_tag else "未知来源"
            
            # 过滤无效结果
            if real_link and title:
                results.append({
                    "title": title,
                    "real_link": real_link,
                    "snippet": snippet,
                    "source": source,
                    "engine": "360搜索（免费）"
                })
        
        return {
            "code": 200,
            "msg": "搜索成功（免费360搜索）",
            "data": {
                "query": req.query,
                "results": results
            }
        }
    except requests.exceptions.Timeout:
        return {"code": 504, "msg": "搜索超时，请稍后重试", "data": None}
    except requests.exceptions.ConnectionError:
        return {"code": 503, "msg": "网络连接失败", "data": None}
    except Exception as e:
        # 捕获所有异常，返回友好提示（避免500错误）
        return {
            "code": 500,
            "msg": f"搜索异常：{str(e)}",
            "data": None
        }

# ========== API接口（适配低版本FastAPI，移除indent参数） ==========
@app.post("/deploy_web")
async def deploy_web(req: DeployRequest):
    """原有：部署Web应用接口"""
    task_id = f"{req.app_name}_{req.port}_{asyncio.get_event_loop().time():.0f}"
    asyncio.create_task(deploy_web_app(task_id, req))
    return {
        "code": 200,
        "msg": "部署任务已启动",
        "task_id": task_id,
        "tips": f"可等待微信通知，或稍后检查http://{SERVER_PUBLIC_IP}:{req.port}"
    }

@app.get("/health")
async def health_check():
    """原有：健康检查接口"""
    return {"code": 200, "msg": "部署机器人运行正常"}

# 适配低版本的搜索接口（手动格式化JSON，无indent参数）
@app.post("/search")
async def search(req: SearchRequest):
    """
    免费信息搜索接口（基于360搜索，国内可访问）
    示例调用：curl -X POST http://服务器IP:8000/search -H "Content-Type: application/json" -d '{"query":"Python FastAPI 教程","num_results":3}'
    """
    result = await search_info(req)
    # 核心修改：手动序列化JSON为带缩进的字符串，兼容低版本FastAPI
    # 方式1：返回普通JSON（无缩进，接口正常）
    # return JSONResponse(content=result)
    # 方式2：返回带缩进的JSON字符串（终端易读，需指定媒体类型）
    return JSONResponse(
        content=json.dumps(result, ensure_ascii=False, indent=4),
        media_type="application/json; charset=utf-8"
    )

# ========== 启动服务 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app="deploy_bot:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
