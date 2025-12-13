#!/usr/bin/env python3
"""
专用版：QQ企业邮箱 → Telegram 转发 (修复时区问题)
说明：此版本已修复时间显示问题，确保显示北京时间。
"""

import os
import time
import imaplib
import email
import re
import requests
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from email.header import decode_header
from datetime import datetime
import pytz  # 新增：用于时区处理

# ========== 配置说明（在Koyeb环境变量中设置）==========
# 1. EMAIL: 你的完整企业邮箱地址（如 monitor@company.com）
# 2. PASSWORD: 企业邮箱的客户端专用密码（在管理后台生成）
# 3. BOT_TOKEN: 你的Telegram Bot Token
# 4. CHAT_ID: 你的Telegram Chat ID
# ==================================================

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== 1. 设置时区（核心修复）==========
# 定义上海/北京时区
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

def get_beijing_time():
    """获取当前的北京时间，并格式化为字符串"""
    now_utc = datetime.utcnow()
    now_beijing = pytz.utc.localize(now_utc).astimezone(BEIJING_TZ)
    return now_beijing.strftime('%Y-%m-%d %H:%M:%S')

def parse_email_time(email_time_str):
    """尝试解析邮件头中的时间，并转换为北京时间"""
    if not email_time_str:
        return get_beijing_time()  # 如果邮件没有时间，使用当前北京时间
    
    try:
        # 尝试解析邮件时间（邮件头时间通常是GMT/UTC）
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(email_time_str)
        
        # 转换为北京时间
        if dt.tzinfo is None:
            # 如果邮件时间没有时区信息，假定为UTC
            dt = pytz.utc.localize(dt)
        
        # 转换为北京时间
        beijing_time = dt.astimezone(BEIJING_TZ)
        return beijing_time.strftime('%Y-%m-%d %H:%M:%S')
        
    except Exception as e:
        logger.warning(f"解析邮件时间失败，使用当前时间: {e}")
        return get_beijing_time()

# ========== 2. 健康检查服务器 ==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        current_time = get_beijing_time()  # 健康检查也返回北京时间
        self.wfile.write(f'OK - 服务器时间: {current_time}'.encode())
    def log_message(self, format, *args):
        pass

def health_server():
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    logger.info(f"✅ 健康检查已就绪 (端口 8000) | 启动时间: {get_beijing_time()}")
    server.serve_forever()

# ========== 3. QQ企业邮箱监控核心 ==========
class QqExmailMonitor:
    def __init__(self):
        # 固定配置：QQ企业邮箱服务器
        self.imap_server = "imap.exmail.qq.com"
        self.imap_port = 993
        
        # 从环境变量读取账号信息
        self.email = os.environ.get("EMAIL", "").strip()
        self.password = os.environ.get("PASSWORD", "").strip()
        self.bot_token = os.environ.get("BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("CHAT_ID", "").strip()
        
        # 检查配置
        if not all([self.email, self.password, self.bot_token, self.chat_id]):
            logger.error("❌ 错误：请设置所有环境变量 (EMAIL, PASSWORD, BOT_TOKEN, CHAT_ID)")
            raise ValueError("缺少必要配置")
        
        logger.info("=" * 50)
        logger.info(f"📧 监控邮箱: {self.email}")
        logger.info(f"🔐 服务器: {self.imap_server}")
        logger.info(f"⏰ 系统时区: 亚洲/上海 (UTC+8)")
        logger.info(f"🕛 当前北京时间: {get_beijing_time()}")
        logger.info("=" * 50)
    
    def get_email_connection(self):
        """连接到QQ企业邮箱"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=15)
            mail.login(self.email, self.password)
            mail.select("INBOX")
            return mail
        except Exception as e:
            logger.error(f"❌ 连接邮箱失败: {e}")
            return None
    
    def get_latest_unread_email(self, mail):
        """获取最新一封未读邮件"""
        try:
            status, messages = mail.search(None, 'UNSEEN')
            if status != "OK" or not messages[0]:
                return None
            
            latest_email_id = messages[0].split()[-1]
            status, msg_data = mail.fetch(latest_email_id, '(RFC822)')
            if status != "OK":
                return None
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # 提取标题
            subject_raw = msg.get("Subject", "无标题")
            decoded = decode_header(subject_raw)
            subject = ""
            for content, charset in decoded:
                if isinstance(content, bytes):
                    subject += content.decode(charset if charset else 'utf-8', errors='ignore')
                else:
                    subject += str(content)
            
            # 提取邮件时间并转换为北京时间
            email_date = msg.get("Date", "")
            beijing_time_str = parse_email_time(email_date)
            
            # 提取正文
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
            else:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            
            return latest_email_id, subject.strip(), body, beijing_time_str
            
        except Exception as e:
            logger.error(f"❌ 读取邮件失败: {e}")
            return None
    
    def find_verification_code(self, text):
        """在正文中查找验证码"""
        if not text:
            return None
        
        match = re.search(r'\b\d{6}\b', text[:500])
        if match:
            return match.group(0)
        
        match = re.search(r'验证码[：:]\s*(\d{4,8})', text[:500])
        if match:
            return match.group(1)
        
        return None
    
    def send_to_telegram(self, subject, code=None, email_time=None):
        """发送到Telegram（使用北京时间）"""
        try:
            # 使用传入的邮件时间，如果没有则用当前北京时间
            display_time = email_time if email_time else get_beijing_time()
            emoji = "🔐" if code else "📧"
            
            message = f"{emoji} *企业邮箱通知*\n\n"
            message += f"📋 *标题*: {subject}\n\n"
            message += f"⏰ *时间*: {display_time} (北京时间)\n"  # 明确标注
            
            if code:
                message += f"\n🔢 *验证码*: `{code}`\n"
            
            message += "\n📬 自动监控服务"
            
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ 已通知Telegram | 邮件时间: {display_time}")
                return True
            else:
                logger.error(f"❌ Telegram发送失败: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 发送到Telegram时出错: {e}")
            return False
    
    def mark_email_as_read(self, mail, email_id):
        """标记邮件为已读"""
        try:
            mail.store(email_id, '+FLAGS', '\\Seen')
            return True
        except:
            return False
    
    def run(self):
        """主监控循环"""
        logger.info(f"🚀 QQ企业邮箱监控服务启动于 {get_beijing_time()}")
        
        check_count = 0
        while True:
            try:
                check_count += 1
                current_beijing_time = get_beijing_time()
                
                # 每10次检查输出一次带时间的日志
                if check_count % 10 == 0:
                    logger.info(f"⏳ 服务运行中... 检查次数: {check_count} | 当前时间: {current_beijing_time}")
                
                # 连接邮箱
                mail = self.get_email_connection()
                if not mail:
                    time.sleep(30)
                    continue
                
                # 检查新邮件
                result = self.get_latest_unread_email(mail)
                
                if result:
                    email_id, subject, body, email_time = result
                    
                    # 查找验证码
                    code = self.find_verification_code(body)
                    
                    # 发送到Telegram（传入邮件时间）
                    self.send_to_telegram(subject, code, email_time)
                    
                    # 标记为已读
                    self.mark_email_as_read(mail, email_id)
                
                # 关闭连接
                mail.close()
                mail.logout()
                
                # 等待15秒后再次检查
                time.sleep(15)
                
            except KeyboardInterrupt:
                logger.info(f"👋 服务停止于 {get_beijing_time()}")
                break
            except Exception as e:
                logger.error(f"❌ 监控循环出错: {e}")
                time.sleep(30)

# ========== 4. 主程序入口 ==========
def main():
    # 启动健康检查服务器
    health_thread = threading.Thread(target=health_server, daemon=True)
    health_thread.start()
    
    # 启动邮箱监控
    try:
        monitor = QqExmailMonitor()
        monitor.run()
    except Exception as e:
        logger.error(f"❌ 服务启动失败: {e}")
        time.sleep(30)

if __name__ == "__main__":
    main()
