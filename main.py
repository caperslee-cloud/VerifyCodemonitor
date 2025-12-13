#!/usr/bin/env python3
"""
163邮箱验证码转发到Telegram
作者：您的专属助手
版本：v1.0 - 极简版
"""

import os
import time
import imaplib
import email
import re
import requests
import logging
from email.header import decode_header

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

print("=" * 50)
print("📧 163邮箱 → Telegram 验证码转发服务")
print("=" * 50)

class SimpleEmailMonitor:
    def __init__(self):
        # 从环境变量读取配置
        self.email = os.environ.get("EMAIL_163", "").strip()
        self.password = os.environ.get("PASSWORD_163", "").strip()
        self.bot_token = os.environ.get("BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("CHAT_ID", "").strip()
        
        # 验证配置
        self.check_config()
        
    def check_config(self):
        """检查配置"""
        if not self.email:
            logger.error("❌ 请设置 EMAIL_163 环境变量")
            exit(1)
        if not self.password:
            logger.error("❌ 请设置 PASSWORD_163 环境变量")
            exit(1)
        if not self.bot_token:
            logger.error("❌ 请设置 BOT_TOKEN 环境变量")
            exit(1)
        if not self.chat_id:
            logger.error("❌ 请设置 CHAT_ID 环境变量")
            exit(1)
            
        logger.info(f"✅ 监控邮箱: {self.email}")
        logger.info(f"✅ Telegram Chat ID: {self.chat_id}")
        
    def decode_subject(self, subject):
        """解码邮件标题"""
        try:
            decoded = decode_header(subject)
            result = ""
            for content, charset in decoded:
                if isinstance(content, bytes):
                    result += content.decode(charset if charset else 'utf-8', errors='ignore')
                else:
                    result += str(content)
            return result.strip()
        except:
            return str(subject)
    
    def find_verification_code(self, text):
        """在文本中查找验证码"""
        if not text:
            return None
        
        # 常见验证码格式
        patterns = [
            r'验证码[：:]\s*(\d{4,8})',
            r'【.*?】\s*(\d{4,8})',
            r'code[：:]\s*(\d{4,8})',
            r'\b(\d{6})\b',  # 6位数字
            r'\b(\d{4})\b',  # 4位数字
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text[:500], re.IGNORECASE)
            if match:
                code = match.group(1) if match.groups() else match.group(0)
                if code.isdigit() and 4 <= len(code) <= 8:
                    return code
        return None
    
    def send_to_telegram(self, subject, code=None):
        """发送消息到Telegram"""
        # 构建消息
        emoji = "🔐" if code else "📧"
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"{emoji} *邮箱通知*\n\n"
        message += f"📋 *标题*: {subject}\n\n"
        message += f"⏰ *时间*: {current_time}\n"
        
        if code:
            message += f"\n🔢 *验证码*: `{code}`\n"
        
        message += "\n📬 自动监控服务"
        
        # 发送请求
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        
        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                logger.info(f"✅ 已发送到Telegram")
                return True
            else:
                logger.error(f"❌ Telegram错误: {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ 发送失败: {e}")
            return False
    
    def check_email(self):
        """检查新邮件"""
        try:
            # 连接163邮箱
            mail = imaplib.IMAP4_SSL("imap.163.com", 993, timeout=10)
            mail.login(self.email, self.password)
            mail.select("INBOX")
            
            # 查找未读邮件
            status, messages = mail.search(None, 'UNSEEN')
            
            if status == "OK" and messages[0]:
                email_ids = messages[0].split()
                logger.info(f"发现 {len(email_ids)} 封新邮件")
                
                # 只处理最新的一封
                latest_id = email_ids[-1]
                
                # 获取邮件
                status, data = mail.fetch(latest_id, '(RFC822)')
                if status == "OK":
                    # 解析邮件
                    msg = email.message_from_bytes(data[0][1])
                    
                    # 获取标题
                    subject_raw = msg.get("Subject", "无标题")
                    subject = self.decode_subject(subject_raw)
                    
                    logger.info(f"邮件标题: {subject}")
                    
                    # 获取正文
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body_bytes = part.get_payload(decode=True)
                                if body_bytes:
                                    body = body_bytes.decode('utf-8', errors='ignore')
                                    break
                    else:
                        body_bytes = msg.get_payload(decode=True)
                        if body_bytes:
                            body = body_bytes.decode('utf-8', errors='ignore')
                    
                    # 查找验证码
                    code = self.find_verification_code(body)
                    
                    # 发送到Telegram
                    self.send_to_telegram(subject, code)
                    
                    # 标记为已读
                    mail.store(latest_id, '+FLAGS', '\\Seen')
                    logger.info("邮件已标记为已读")
            
            # 关闭连接
            mail.close()
            mail.logout()
            return True
            
        except imaplib.IMAP4.error as e:
            logger.error(f"❌ 邮箱登录失败: {e}")
            logger.error("请检查: 1.授权码是否正确 2.IMAP服务是否开启")
            return False
        except Exception as e:
            logger.error(f"❌ 检查邮件失败: {e}")
            return False
    
    def run(self):
        """主运行循环"""
        logger.info("🚀 服务启动，开始监控...")
        logger.info(f"📧 监控邮箱: {self.email}")
        logger.info(f"⏰ 每10秒检查一次")
        logger.info("=" * 50)
        
        error_count = 0
        
        while True:
            try:
                success = self.check_email()
                
                if success:
                    error_count = 0
                else:
                    error_count += 1
                    if error_count >= 3:
                        logger.error("❌ 连续错误过多，等待60秒后重试")
                        time.sleep(60)
                        error_count = 0
                
                # 等待10秒后再次检查
                time.sleep(10)
                
            except KeyboardInterrupt:
                logger.info("👋 服务停止")
                break
            except Exception as e:
                logger.error(f"❌ 运行错误: {e}")
                time.sleep(30)

def main():
    """程序入口"""
    monitor = SimpleEmailMonitor()
    monitor.run()

if __name__ == "__main__":
    main()
