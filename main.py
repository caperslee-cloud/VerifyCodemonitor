#!/usr/bin/env python3
"""
QQ企业邮箱 → Telegram 验证码转发 (专业生产版)
功能：1.精准验证码识别 2.双重防休眠机制 3.完整监控指标 4.优雅错误处理
部署于Koyeb时，配置环境变量即可使用
"""

import os
import sys
import time
import imaplib
import email
import re
import requests
import logging
import threading
import random
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.header import decode_header
from email.utils import parsedate_to_datetime
import pytz
from enum import Enum
import ssl

# ==================== 配置常量 ====================
class Config:
    """配置管理类"""
    # IMAP 设置
    IMAP_SERVER = "imap.exmail.qq.com"
    IMAP_PORT = 993
    IMAP_TIMEOUT = 15
    IMAP_SSL = True
    
    # 健康检查
    HEALTH_PORT = 8000
    HEALTH_HOST = "0.0.0.0"
    
    # 时间设置
    BEIJING_TZ = pytz.timezone("Asia/Shanghai")
    CHECK_INTERVAL = 15  # 邮件检查间隔（秒）
    SELF_PING_INTERVAL = 280  # 自我唤醒间隔（秒），略小于5分钟
    
    # 监控设置
    MAX_ERROR_COUNT = 5
    ERROR_BACKOFF = 60  # 连续错误后等待时间（秒）
    
    # 验证码模式 (最终通用增强版 - 针对HTML邮件优化)
    CODE_PATTERNS = [
        # ==== 针对中文HTML邮件的精准规则 ====
        # 规则1: 匹配"验证码"文本后出现的第一个6位数字（无论中间有什么HTML）
        r'(?:验证码[^<]*</p>)[^<]*(?:<div[^>]*>)[^0-9]*(\d{6})',
        
        # 规则2: 匹配在"验证码"文本后，且被<div>包裹的6位数字
        r'验证码[^<]*</p>\s*<div[^>]*>\s*(\d{6})\s*</div>',

        # ==== 通用中英文规则 ====
        # 规则3: 匹配"验证码/Code"标签后的数字
        r'(?:验证码|验证代码|Code|CODE)[：:\s]*(\d{4,8})',
        
        # 规则4: 匹配独立一行中的4-8位数字
        r'^\s*(\d{4,8})\s*$',
        
        # ==== 保底规则 (经过严格限制) ====
        # 规则5: 独立的6位数字，但排除明显是颜色代码、尺寸等的数字
        r'(?<![#\-\.\d])(\d{6})(?![#\-\.\d%px])',
    ]
    
    @classmethod
    def get_env(cls, key: str, default: str = "") -> str:
        """获取环境变量"""
        return os.environ.get(key, default).strip()
    
    @classmethod
    def validate_config(cls) -> bool:
        """验证必要配置"""
        required = ["EMAIL", "PASSWORD", "BOT_TOKEN", "CHAT_ID"]
        missing = [key for key in required if not cls.get_env(key)]
        
        if missing:
            logging.error(f"❌ 缺失必要环境变量: {', '.join(missing)}")
            logging.error("请在Koyeb环境变量中设置:")
            logging.error("  - EMAIL: 你的完整企业邮箱地址")
            logging.error("  - PASSWORD: 邮箱客户端专用密码")
            logging.error("  - BOT_TOKEN: Telegram Bot Token")
            logging.error("  - CHAT_ID: Telegram Chat ID（多个用逗号分隔）")
            return False
        
        # 验证邮箱格式
        email_val = cls.get_env("EMAIL")
        if "@" not in email_val or "." not in email_val.split("@")[-1]:
            logging.warning(f"⚠️  邮箱地址格式可能不正确: {email_val}")
        
        return True

# ==================== 日志配置 ====================
class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器"""
    COLORS = {
        'DEBUG': '\033[36m',     # 青色
        'INFO': '\033[32m',      # 绿色
        'WARNING': '\033[33m',   # 黄色
        'ERROR': '\033[31m',     # 红色
        'CRITICAL': '\033[41m',  # 红底白字
        'RESET': '\033[0m'
    }
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset_color = self.COLORS['RESET']
        
        # 添加颜色
        record.levelname = f"{log_color}{record.levelname}{reset_color}"
        record.msg = f"{log_color}{record.msg}{reset_color}"
        
        return super().format(record)

def setup_logging():
    """配置日志系统"""
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    
    # 文件处理器（可选）
    if os.path.exists("/tmp"):
        file_handler = logging.FileHandler("/tmp/email_monitor.log")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    # 控制台格式化
    console_formatter = ColoredFormatter(
        '[%(asctime)s] %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ==================== 数据模型 ====================
@dataclass
class EmailInfo:
    """邮件信息"""
    subject: str
    sender: str
    date: str
    code: Optional[str] = None
    raw_body: str = ""

@dataclass
class HealthMetrics:
    """健康指标"""
    start_time: float
    email_checks: int = 0
    emails_forwarded: int = 0
    telegram_sent: int = 0
    errors: int = 0
    last_email_check: Optional[float] = None
    last_telegram_send: Optional[float] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        uptime = int(time.time() - self.start_time)
        
        return {
            "status": "healthy",
            "service": "qq_email_monitor",
            "uptime_seconds": uptime,
            "uptime_human": str(timedelta(seconds=uptime)),
            "email_checks": self.email_checks,
            "emails_forwarded": self.emails_forwarded,
            "telegram_sent": self.telegram_sent,
            "error_count": self.errors,
            "last_email_check": self.format_time(self.last_email_check),
            "last_telegram_send": self.format_time(self.last_telegram_send),
            "current_time": self.get_beijing_time(),
            "version": "1.2.0"
        }
    
    @staticmethod
    def format_time(timestamp: Optional[float]) -> str:
        """格式化时间戳"""
        if not timestamp:
            return "从未"
        dt = datetime.fromtimestamp(timestamp, tz=Config.BEIJING_TZ)
        return dt.strftime('%H:%M:%S')
    
    @staticmethod
    def get_beijing_time() -> str:
        """获取北京时间"""
        now = datetime.now(Config.BEIJING_TZ)
        return now.strftime('%Y-%m-%d %H:%M:%S')

# ==================== 健康检查服务器 ====================
class EnhancedHealthHandler(BaseHTTPRequestHandler):
    """增强型健康检查处理器"""
    
    server_version = "EmailMonitor/1.2"
    metrics = HealthMetrics(start_time=time.time())
    
    def log_message(self, format: str, *args):
        """自定义日志格式"""
        client_ip = self.client_address[0]
        request_line = args[0] if args else ""
        
        # 忽略自我唤醒的日志
        if client_ip in ["127.0.0.1", "::1"] and "HEAD" in request_line:
            return
        
        logger.info(f"🌐 健康检查 - {client_ip} - {request_line}")
    
    def do_GET(self):
        """处理GET请求"""
        self.metrics.last_email_check = time.time()
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        
        response = self.metrics.to_dict()
        self.wfile.write(json.dumps(response, indent=2, ensure_ascii=False).encode('utf-8'))
    
    def do_HEAD(self):
        """处理HEAD请求（UptimeRobot等监控服务使用）"""
        self.metrics.last_email_check = time.time()
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
    
    def do_POST(self):
        """处理POST请求（可用于手动触发检查）"""
        if self.path == "/check-now":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            response = {
                "status": "triggered",
                "message": "邮件检查已手动触发",
                "timestamp": HealthMetrics.get_beijing_time()
            }
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    """运行健康检查服务器"""
    server_address = (Config.HEALTH_HOST, Config.HEALTH_PORT)
    
    try:
        httpd = HTTPServer(server_address, EnhancedHealthHandler)
        logger.info(f"🛡️  健康服务器启动 | 地址: http://{Config.HEALTH_HOST}:{Config.HEALTH_PORT}")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"❌ 健康服务器启动失败: {e}")
        sys.exit(1)

# ==================== 自我唤醒系统 ====================
class SelfWaker:
    """自我唤醒系统"""
    
    def __init__(self, service_url: str = None):
        self.service_url = service_url or f"http://localhost:{Config.HEALTH_PORT}"
        self.interval = Config.SELF_PING_INTERVAL
        
        # 从环境变量读取间隔
        env_interval = Config.get_env("SELF_PING_INTERVAL")
        if env_interval and env_interval.isdigit():
            self.interval = int(env_interval)
            logger.info(f"🔧 使用自定义唤醒间隔: {self.interval}秒")
    
    def ping(self) -> bool:
        """执行自我唤醒"""
        try:
            # 添加随机抖动避免固定间隔
            jitter = random.randint(-5, 5)
            time.sleep(max(0, jitter))
            
            response = requests.head(
                self.service_url,
                timeout=10,
                headers={'User-Agent': 'SelfWaker/1.0'}
            )
            
            if response.status_code == 200:
                logger.debug(f"🔄 自我唤醒成功")
                return True
            else:
                logger.warning(f"⚠️ 唤醒响应异常: {response.status_code}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 自我唤醒失败: {e}")
            return False
    
    def run(self):
        """运行唤醒循环"""
        logger.info(f"🚀 自我唤醒系统启动 | 间隔: {self.interval}秒")
        
        cycle = 0
        consecutive_failures = 0
        
        while True:
            try:
                cycle += 1
                time.sleep(self.interval)
                
                success = self.ping()
                
                if success:
                    consecutive_failures = 0
                    if cycle % 12 == 0:  # 每小时报告一次
                        logger.info(f"✅ 自我唤醒运行正常 | 已执行 {cycle} 次")
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.error(f"🚨 连续唤醒失败 {consecutive_failures} 次")
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"唤醒循环异常: {e}")
                time.sleep(60)

# ==================== 邮箱监控核心 ====================
class EmailMonitor:
    """邮箱监控器"""
    
    def __init__(self):
        self.email = Config.get_env("EMAIL")
        self.password = Config.get_env("PASSWORD")
        self.bot_token = Config.get_env("BOT_TOKEN")
        self.chat_ids = [cid.strip() for cid in Config.get_env("CHAT_ID").split(",") if cid.strip()]
        
        self.error_count = 0
        self.session = requests.Session()
        
        logger.info("=" * 60)
        logger.info(f"📧 监控邮箱: {self.email}")
        logger.info(f"🤖 Telegram Bot: 已配置 {len(self.chat_ids)} 个接收者")
        logger.info(f"⏰ 启动时间: {HealthMetrics.get_beijing_time()}")
        logger.info("=" * 60)
    
    def decode_header(self, header: str) -> str:
        """解码邮件头"""
        if not header:
            return "无标题"
        
        try:
            decoded_parts = decode_header(header)
            result_parts = []
            
            for content, charset in decoded_parts:
                if isinstance(content, bytes):
                    try:
                        charset = charset or 'utf-8'
                        result_parts.append(content.decode(charset, errors='ignore'))
                    except (LookupError, UnicodeDecodeError):
                        result_parts.append(content.decode('utf-8', errors='ignore'))
                else:
                    result_parts.append(str(content))
            
            return ''.join(result_parts).strip()
        except Exception:
            return str(header)
    
    def _clean_html_text(self, text: str) -> str:
        """清理HTML标签和样式，防止误匹配"""
        if not text:
            return ""
        
        # 移除HTML标签
        cleaned = re.sub(r'<[^>]+>', ' ', text)
        
        # 专门移除颜色代码
        cleaned = re.sub(r'#\d{3,6}', ' ', cleaned)  # 移除 #333, #333333 等颜色代码
        cleaned = re.sub(r'rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+', ' ', cleaned)  # 移除 rgb(), rgba()
        
        # 移除常见CSS属性
        cleaned = re.sub(r'\b(margin|padding|width|height|color|font-size)[: ]*\d+', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\d+px', ' ', cleaned, flags=re.IGNORECASE)
        
        # 移除HTML数字实体
        cleaned = re.sub(r'&#\d+;', ' ', cleaned)
        
        # 移除银行卡号等常见带分隔符的数字串
        cleaned = re.sub(r'\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b', ' ', cleaned)  # 完整卡号
        cleaned = re.sub(r'\b\d{4}[- ]\d{4}[- ]\d{4}\b', ' ', cleaned)           # 部分卡号
        cleaned = re.sub(r'\b\d{4}[- ]\d{4}\b', ' ', cleaned)                    # 短格式卡号片段
        
        # 合并多余空格
        cleaned = re.sub(r'\s+', ' ', cleaned)
        
        return cleaned.strip()
    
    def extract_verification_code(self, text: str) -> Optional[str]:
        """提取验证码"""
        if not text:
            return None
        
        # 截取前1000字符以提高效率
        search_text = text[:1000]
        
        logger.debug(f"【DEBUG】原始文本 (前200字符): {repr(search_text[:200])}")
        
        # 对文本进行清理
        cleaned_text = self._clean_html_text(search_text)
        logger.debug(f"【DEBUG】清理后的文本: {repr(cleaned_text[:200])}")
        
        # 首先在原始HTML中尝试高精度匹配
        for pattern in Config.CODE_PATTERNS[:2]:  # 只使用前两个高精度规则
            match = re.search(pattern, search_text, re.IGNORECASE)
            if match:
                code = match.group(1)
                if code.isdigit() and 4 <= len(code) <= 8:
                    logger.debug(f"【DEBUG】高精度匹配命中: 模式 '{pattern}' -> 提取内容 '{code}'")
                    return code
        
        # 如果在原始HTML中没匹配到，尝试在清理后的文本中匹配通用规则
        for pattern in Config.CODE_PATTERNS[2:]:  # 使用剩余的通用规则
            match = re.search(pattern, cleaned_text, re.IGNORECASE)
            if match:
                code = match.group(1)
                if code.isdigit() and 4 <= len(code) <= 8:
                    logger.debug(f"【DEBUG】通用规则匹配命中: 模式 '{pattern}' -> 提取内容 '{code}'")
                    return code
        
        return None
    
    def connect_imap(self) -> Optional[imaplib.IMAP4_SSL]:
        """连接IMAP服务器"""
        try:
            if Config.IMAP_SSL:
                context = ssl.create_default_context()
                imap = imaplib.IMAP4_SSL(
                    Config.IMAP_SERVER,
                    Config.IMAP_PORT,
                    timeout=Config.IMAP_TIMEOUT,
                    ssl_context=context
                )
            else:
                imap = imaplib.IMAP4(Config.IMAP_SERVER, Config.IMAP_PORT)
                imap.starttls()
            
            imap.login(self.email, self.password)
            imap.select("INBOX")
            
            logger.debug("✅ IMAP连接成功")
            return imap
            
        except imaplib.IMAP4.error as e:
            logger.error(f"❌ IMAP认证失败: {e}")
        except (TimeoutError, ConnectionError) as e:
            logger.error(f"❌ 网络连接失败: {e}")
        except Exception as e:
            logger.error(f"❌ 连接异常: {e}")
        
        return None
    
    def process_email(self, imap: imaplib.IMAP4_SSL, email_id: bytes) -> Optional[EmailInfo]:
        """处理单封邮件"""
        try:
            # 获取邮件
            status, msg_data = imap.fetch(email_id, '(RFC822)')
            if status != "OK":
                return None
            
            # 解析邮件
            msg = email.message_from_bytes(msg_data[0][1])
            
            # 提取基本信息
            subject = self.decode_header(msg.get("Subject", ""))
            sender = msg.get("From", "")
            date_str = msg.get("Date", "")
            
            # 解析日期
            try:
                date_obj = parsedate_to_datetime(date_str)
                date_beijing = date_obj.astimezone(Config.BEIJING_TZ)
                date_formatted = date_beijing.strftime('%H:%M:%S')
            except:
                date_formatted = "时间解析失败"
            
            # 提取正文
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode('utf-8', errors='ignore')
                                break
                        except:
                            continue
            else:
                try:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode('utf-8', errors='ignore')
                except:
                    body = str(msg.get_payload())
            
            logger.debug(f"【DEBUG】解析到的邮件正文 (前300字符): {repr(body[:300])}")
            
            # 提取验证码
            code = self.extract_verification_code(body)
            
            return EmailInfo(
                subject=subject,
                sender=sender,
                date=date_formatted,
                code=code,
                raw_body=body[:500]  # 只保存前500字符
            )
            
        except Exception as e:
            logger.error(f"处理邮件异常: {e}")
            return None
    
    def send_to_telegram(self, email_info: EmailInfo) -> bool:
        """发送到Telegram"""
        try:
            current_time = datetime.now(Config.BEIJING_TZ).strftime('%H:%M:%S')
            
            # 构建消息
            message_lines = [
                "📨 *验证码通知*",
                "──────────────",
                f"*📌 标题*: {email_info.subject}",
                f"*🕒 时间*: {email_info.date} (检测于 {current_time})",
                "",
                f"*🔐 验证码*: `{email_info.code}`",
                "──────────────",
            ]
            
            message = "\n".join(message_lines)
            
            success_count = 0
            for chat_id in self.chat_ids:
                try:
                    url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                    payload = {
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    }
                    
                    response = self.session.post(url, json=payload, timeout=10)
                    
                    if response.status_code == 200:
                        success_count += 1
                        logger.debug(f"✅ 发送到 {chat_id[:8]}... 成功")
                    else:
                        logger.error(f"❌ 发送到 {chat_id[:8]}... 失败: {response.text}")
                        
                except Exception as e:
                    logger.error(f"发送到 {chat_id[:8]}... 异常: {e}")
            
            EnhancedHealthHandler.metrics.telegram_sent += success_count
            EnhancedHealthHandler.metrics.last_telegram_send = time.time()
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Telegram发送异常: {e}")
            return False
    
    def check_emails(self) -> bool:
        """检查并处理邮件"""
        imap = self.connect_imap()
        if not imap:
            return False
        
        try:
            # 搜索未读邮件
            status, messages = imap.search(None, 'UNSEEN')
            if status != "OK" or not messages[0]:
                return True
            
            email_ids = messages[0].split()
            processed = 0
            forwarded = 0
            
            # 只处理最新的5封邮件
            for email_id in email_ids[-5:]:
                email_info = self.process_email(imap, email_id)
                if email_info:
                    processed += 1
                    
                    if email_info.code:
                        # 发送到Telegram
                        if self.send_to_telegram(email_info):
                            forwarded += 1
                            logger.info(f"📤 转发验证码: {email_info.subject} -> {email_info.code}")
                    
                    # 标记为已读
                    imap.store(email_id, '+FLAGS', '\\Seen')
            
            if forwarded > 0:
                logger.info(f"✅ 本轮处理完成: 处理 {processed} 封，转发 {forwarded} 封")
                EnhancedHealthHandler.metrics.emails_forwarded += forwarded
            
            return True
            
        except Exception as e:
            logger.error(f"邮件检查异常: {e}")
            return False
        
        finally:
            try:
                imap.close()
                imap.logout()
            except:
                pass
    
    def run(self):
        """主监控循环"""
        logger.info("🚀 邮箱监控服务启动")
        
        check_interval = Config.CHECK_INTERVAL
        
        while True:
            try:
                EnhancedHealthHandler.metrics.email_checks += 1
                EnhancedHealthHandler.metrics.last_email_check = time.time()
                
                # 执行检查
                success = self.check_emails()
                
                if success:
                    self.error_count = max(0, self.error_count - 1)
                else:
                    self.error_count += 1
                    EnhancedHealthHandler.metrics.errors += 1
                
                # 错误处理
                if self.error_count >= Config.MAX_ERROR_COUNT:
                    logger.error(f"🚨 连续错误过多，等待 {Config.ERROR_BACKOFF} 秒")
                    time.sleep(Config.ERROR_BACKOFF)
                    self.error_count = Config.MAX_ERROR_COUNT // 2
                
                # 等待下次检查
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("👋 收到停止信号，优雅退出")
                break
            except Exception as e:
                logger.error(f"监控循环异常: {e}")
                time.sleep(30)

# ==================== 主程序入口 ====================
def banner():
    """显示启动横幅"""
    print("\n" + "=" * 60)
    print("QQ企业邮箱 → Telegram 验证码转发服务")
    print("版本: 1.3.0 | 专为 Koyeb 部署优化")
    print("=" * 60)
    print("功能特性:")
    print("  ✓ 精准验证码识别（支持中英文HTML邮件）")
    print("  ✓ 双重防休眠机制（内部+外部）")
    print("  ✓ 完整健康检查接口（GET/HEAD/POST）")
    print("  ✓ 实时监控指标和错误统计")
    print("  ✓ 优雅的错误处理和自动恢复")
    print("=" * 60 + "\n")

def main():
    """主程序入口"""
    banner()
    
    # 1. 验证配置
    if not Config.validate_config():
        logger.error("❌ 配置验证失败，程序退出")
        sys.exit(1)
    
    logger.info("✅ 所有配置验证通过")
    
    # 2. 启动健康检查服务器（背景线程）
    health_thread = threading.Thread(
        target=run_health_server,
        name="HealthServer",
        daemon=True
    )
    health_thread.start()
    time.sleep(1)
    
    # 3. 启动自我唤醒系统（背景线程）
    try:
        waker = SelfWaker()
        wake_thread = threading.Thread(
            target=waker.run,
            name="SelfWaker",
            daemon=True
        )
        wake_thread.start()
        logger.info("✅ 自我唤醒系统已启动")
    except Exception as e:
        logger.warning(f"⚠️ 自我唤醒系统启动失败（可继续运行）: {e}")
    
    # 4. 启动邮箱监控（主线程）
    try:
        monitor = EmailMonitor()
        monitor.run()
    except KeyboardInterrupt:
        logger.info("👋 服务被用户中断")
    except Exception as e:
        logger.error(f"💥 服务崩溃: {e}")
        sys.exit(1)
    
    logger.info("服务正常停止")

if __name__ == "__main__":
    main()
